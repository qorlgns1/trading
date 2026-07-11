import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from quant_core.calendar import next_trading_date
from quant_core.config import PEER_GROUP_SLEEVE, PEER_GROUP_SLOTS, PortfolioConfig
from quant_core.enums import DataStatus, PeerGroup, Sleeve
from quant_core.metrics import calculate_metrics
from quant_core.models import BacktestResult, PositionSnapshot, Trade

MARKET_EVENT_VERSION = "market-event-v1.1.0"


def market_for_group(group: PeerGroup) -> str:
    return "US" if group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF} else "KR"


@dataclass
class PortfolioPosition:
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    currency: str
    quantity: int
    last_score: float
    entry_date: date | None = None
    decision_date: date | None = None
    signal_date: date | None = None
    entry_score: float = 0.0
    entry_price: float = 0.0
    entry_fx: float = 1.0
    entry_notional_krw: float = 0.0
    cost_basis_krw: float = 0.0
    entry_cost_krw: float = 0.0
    dividends_krw: float = 0.0


@dataclass(frozen=True)
class PlannedOrder:
    side: str
    asset_id: str
    reason: str
    market: str
    scheduled_date: date | None = None
    decision_date: date | None = None
    signal_date: date | None = None
    score: float | None = None


@dataclass(frozen=True)
class ReviewPlan:
    orders: list[PlannedOrder]
    review_required_assets: set[str]


@dataclass(frozen=True)
class DailyLedgerRow:
    date: date
    sleeve: Sleeve
    cash_krw: float
    position_value_krw: float
    equity_krw: float
    exposure: float
    positions_count: int
    transaction_cost_krw: float
    dividend_krw: float
    fx_krw_per_usd: float


@dataclass(frozen=True)
class ReviewLedgerRow:
    review_date: date
    signal_date: date
    peer_group: PeerGroup
    benchmark_entry_allowed: bool
    candidate_count: int
    held_count: int
    planned_buy_count: int
    planned_sell_count: int
    projected_held_count: int
    review_required_count: int


@dataclass(frozen=True)
class RoundTrip:
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    currency: str
    status: str
    entry_date: date
    exit_date: date | None
    entry_score: float
    exit_score: float | None
    quantity: int
    entry_price: float
    exit_price: float | None
    entry_notional_krw: float
    exit_value_krw: float
    dividends_krw: float
    costs_krw: float
    net_pnl_krw: float
    net_return: float
    holding_days: int
    exit_reason: str


@dataclass(frozen=True)
class PreparedMarketReplay:
    signals_by_review: dict[date, list[dict[str, Any]]]
    review_dates: list[date]
    dates: list[date]
    candidate_ids: list[str]
    metadata: dict[str, dict[str, Any]]
    asset_index: dict[str, int]
    open_prices: np.ndarray
    close_prices: np.ndarray
    split_ratios: np.ndarray
    dividends: np.ndarray
    recovery_values: np.ndarray
    fx_by_date: dict[date, float]
    benchmark_indexes: dict[Sleeve, np.ndarray]


@dataclass(frozen=True)
class MarketReplayRun:
    result: BacktestResult
    daily_ledger: list[DailyLedgerRow]
    review_ledger: list[ReviewLedgerRow]
    round_trips: list[RoundTrip]
    position_counts: list[dict[str, Any]]
    equity_values: list[float]
    benchmark_values: list[float]


def _row_is_usable(row: dict[str, Any] | None) -> bool:
    if row is None:
        return False
    status = row.get("data_status")
    if status is not None and str(status) != DataStatus.READY.value:
        return False
    return bool(row.get("data_eligible")) and row.get("trend_score") is not None


def plan_weekly_orders(
    rows: list[dict[str, Any]],
    positions: dict[str, PortfolioPosition],
    *,
    config: PortfolioConfig | None = None,
    pending_sell_ids: set[str] | None = None,
) -> ReviewPlan:
    """Plan deterministic weekly orders without trading on unusable data."""
    config = config or PortfolioConfig()
    pending_sell_ids = pending_sell_ids or set()
    by_asset = {str(row["asset_id"]): row for row in rows}
    orders: list[PlannedOrder] = []
    review_required: set[str] = set()

    for asset_id, position in sorted(positions.items()):
        if asset_id in pending_sell_ids:
            continue
        row = by_asset.get(asset_id)
        if not _row_is_usable(row):
            review_required.add(asset_id)
            continue
        assert row is not None
        failed_gate = not bool(row.get("candidate_eligible"))
        fell_below_exit = float(row.get("trend_score") or 0) < config.exit_score
        if failed_gate or fell_below_exit:
            orders.append(
                PlannedOrder(
                    side="SELL",
                    asset_id=asset_id,
                    reason="EXIT_RULE",
                    market=market_for_group(position.peer_group),
                    signal_date=row.get("signal_date"),
                    score=float(row.get("trend_score") or 0),
                )
            )

    held_assets = set(positions)
    for group in PeerGroup:
        sleeve = PEER_GROUP_SLEEVE[group]
        if config.sleeve_weights_bps[sleeve] == 0:
            continue
        held_count = sum(positions[asset_id].peer_group is group for asset_id in held_assets)
        vacancies = PEER_GROUP_SLOTS[group] - held_count
        if vacancies <= 0:
            continue
        group_rows = [row for row in rows if row.get("peer_group") == group.value]
        benchmark_row = next(
            (row for row in group_rows if row.get("benchmark_sma200") is not None),
            None,
        )
        if benchmark_row is None or float(benchmark_row["benchmark_close"]) <= float(
            benchmark_row["benchmark_sma200"]
        ):
            continue
        candidates = [
            row
            for row in group_rows
            if _row_is_usable(row)
            and bool(row.get("candidate_eligible"))
            and float(row.get("trend_score") or 0) >= config.entry_score
            and str(row["asset_id"]) not in held_assets
            and str(row["asset_id"]) not in pending_sell_ids
        ]
        candidates.sort(
            key=lambda row: (
                -float(row.get("trend_score") or 0),
                -float(row.get("relative_momentum") or -999),
                str(row["asset_id"]),
            )
        )
        for candidate in candidates[:vacancies]:
            orders.append(
                PlannedOrder(
                    side="BUY",
                    asset_id=str(candidate["asset_id"]),
                    reason="WEEKLY_ENTRY",
                    market=market_for_group(group),
                    signal_date=candidate.get("signal_date"),
                    score=float(candidate.get("trend_score") or 0),
                )
            )
    return ReviewPlan(orders=orders, review_required_assets=review_required)


def _wide_matrix(
    frame: pl.DataFrame, value: str, dates: list[date], asset_ids: list[str]
) -> np.ndarray:
    wide = (
        frame.select("date", "asset_id", value)
        .pivot(on="asset_id", index="date", values=value, aggregate_function="first")
        .sort("date")
    )
    missing_dates = sorted(set(dates) - set(wide.get_column("date").to_list()))
    if missing_dates:
        wide = pl.concat(
            [wide, pl.DataFrame({"date": missing_dates})], how="diagonal_relaxed"
        ).sort("date")
    for asset_id in asset_ids:
        if asset_id not in wide.columns:
            wide = wide.with_columns(pl.lit(None).cast(pl.Float64).alias(asset_id))
    return wide.select(asset_ids).to_numpy()


def _benchmark_indexes(
    reference: pl.DataFrame,
    dates: list[date],
    fx_by_date: dict[date, float],
) -> dict[Sleeve, np.ndarray]:
    date_frame = pl.DataFrame({"date": dates})
    grouped = (
        reference.select("date", "peer_group", "benchmark_close")
        .unique(["date", "peer_group"], keep="last")
        .pivot(on="peer_group", index="date", values="benchmark_close")
    )
    grouped = date_frame.join(grouped, on="date", how="left").sort("date")
    for group in PeerGroup:
        if group.value not in grouped.columns:
            grouped = grouped.with_columns(pl.lit(None).cast(pl.Float64).alias(group.value))
    grouped = grouped.with_columns(
        pl.col(group.value).forward_fill().backward_fill() for group in PeerGroup
    )
    fx = np.asarray([fx_by_date[current] for current in dates])
    normalized: dict[PeerGroup, np.ndarray] = {}
    for group in PeerGroup:
        values = grouped.get_column(group.value).to_numpy()
        if market_for_group(group) == "US":
            values = values * fx
        normalized[group] = values / values[0]
    return {
        Sleeve.US_STOCK: normalized[PeerGroup.US_STOCK],
        Sleeve.US_ETF: normalized[PeerGroup.US_EQUITY_ETF],
        Sleeve.KR_STOCK: (
            normalized[PeerGroup.KR_KOSPI] * (2 / 3)
            + normalized[PeerGroup.KR_KOSDAQ] * (1 / 3)
        ),
        Sleeve.KR_ETF: (
            normalized[PeerGroup.KR_DOMESTIC_EQUITY_ETF] * (2 / 3)
            + normalized[PeerGroup.KR_OVERSEAS_EQUITY_ETF] * (1 / 3)
        ),
    }


def _combined_benchmark(
    prepared: PreparedMarketReplay, config: PortfolioConfig
) -> np.ndarray:
    combined = np.zeros(len(prepared.dates))
    for sleeve, values in prepared.benchmark_indexes.items():
        combined += values * (config.sleeve_weights_bps[sleeve] / 10_000)
    return config.initial_capital_krw * combined


def prepare_market_replay(
    bars: pl.DataFrame,
    weekly_signals: pl.DataFrame,
    reference: pl.DataFrame,
    *,
    portfolio_config: PortfolioConfig | None = None,
    asset_metadata: pl.DataFrame | None = None,
) -> PreparedMarketReplay:
    """Build immutable market matrices once for actual and counterfactual simulations."""
    config = portfolio_config or PortfolioConfig()
    if weekly_signals.is_empty():
        raise ValueError("완결된 주간 신호가 없습니다.")
    signals = weekly_signals.sort(["review_date", "peer_group", "asset_id"])
    review_dates = signals.get_column("review_date").unique().sort().to_list()
    first_review = review_dates[0]
    candidate_ids = (
        signals.filter(
            pl.col("candidate_eligible").fill_null(False)
            & (pl.col("trend_score") >= config.entry_score)
        )
        .get_column("asset_id")
        .unique()
        .to_list()
    )
    if not candidate_ids:
        raise ValueError("재생 기간에 진입 가능한 후보가 없습니다.")
    filtered_bars = bars.filter(pl.col("asset_id").is_in(candidate_ids)).sort(
        ["date", "asset_id"]
    )
    metadata_source = asset_metadata if asset_metadata is not None else filtered_bars
    metadata = {
        str(row["asset_id"]): row
        for row in metadata_source.select(
            "asset_id", "symbol", "name", "peer_group", "currency"
        )
        .unique("asset_id")
        .iter_rows(named=True)
    }
    candidate_ids = sorted(metadata)
    signals = signals.filter(pl.col("asset_id").is_in(candidate_ids))
    by_review = {
        key[0] if isinstance(key, tuple) else key: part.drop("review_date").to_dicts()
        for key, part in signals.partition_by("review_date", as_dict=True).items()
    }
    all_dates = sorted(
        current
        for current in reference.get_column("date").unique().to_list()
        if current >= first_review
    )
    if len(all_dates) < 2:
        raise ValueError("성과 계산에 필요한 평가일이 부족합니다.")
    filtered_bars = filtered_bars.filter(pl.col("date") >= first_review)
    fx_frame = (
        reference.group_by("date")
        .agg(pl.col("fx_krw_per_usd").drop_nulls().last())
        .sort("date")
    )
    fx_joined = (
        pl.DataFrame({"date": all_dates})
        .join(fx_frame, on="date", how="left")
        .with_columns(pl.col("fx_krw_per_usd").forward_fill().backward_fill())
    )
    if fx_joined.get_column("fx_krw_per_usd").null_count() > 0:
        raise ValueError("재생 기간의 환율을 정렬할 수 없습니다.")
    fx_by_date = {
        row["date"]: float(row["fx_krw_per_usd"])
        for row in fx_joined.iter_rows(named=True)
    }
    return PreparedMarketReplay(
        signals_by_review=by_review,
        review_dates=review_dates,
        dates=all_dates,
        candidate_ids=candidate_ids,
        metadata=metadata,
        asset_index={asset_id: index for index, asset_id in enumerate(candidate_ids)},
        open_prices=_wide_matrix(filtered_bars, "open", all_dates, candidate_ids),
        close_prices=_wide_matrix(filtered_bars, "close", all_dates, candidate_ids),
        split_ratios=_wide_matrix(filtered_bars, "split_ratio", all_dates, candidate_ids),
        dividends=_wide_matrix(filtered_bars, "dividend", all_dates, candidate_ids),
        recovery_values=_wide_matrix(filtered_bars, "recovery_value", all_dates, candidate_ids),
        fx_by_date=fx_by_date,
        benchmark_indexes=_benchmark_indexes(reference, all_dates, fx_by_date),
    )


def _native_multiplier(currency: str, current_fx: float) -> float:
    return current_fx if currency == "USD" else 1.0


def _round_trip(
    position: PortfolioPosition,
    *,
    status: str,
    current_date: date,
    current_price: float,
    current_fx: float,
    exit_cost_krw: float,
    exit_reason: str,
    exit_score: float | None,
) -> RoundTrip:
    exit_value_krw = (
        position.quantity * current_price * _native_multiplier(position.currency, current_fx)
        - exit_cost_krw
    )
    net_pnl = exit_value_krw + position.dividends_krw - position.cost_basis_krw
    net_return = net_pnl / position.cost_basis_krw if position.cost_basis_krw > 0 else 0.0
    entry_date = position.entry_date or current_date
    return RoundTrip(
        asset_id=position.asset_id,
        symbol=position.symbol,
        name=position.name,
        peer_group=position.peer_group,
        sleeve=position.sleeve,
        currency=position.currency,
        status=status,
        entry_date=entry_date,
        exit_date=current_date if status == "CLOSED" else None,
        entry_score=position.entry_score,
        exit_score=exit_score,
        quantity=position.quantity,
        entry_price=position.entry_price,
        exit_price=current_price,
        entry_notional_krw=position.entry_notional_krw,
        exit_value_krw=exit_value_krw,
        dividends_krw=position.dividends_krw,
        costs_krw=position.entry_cost_krw + exit_cost_krw,
        net_pnl_krw=net_pnl,
        net_return=net_return,
        holding_days=max(0, (current_date - entry_date).days),
        exit_reason=exit_reason,
    )


def simulate_prepared_replay(
    prepared: PreparedMarketReplay,
    *,
    data_version: str,
    score_version: str,
    portfolio_config: PortfolioConfig | None = None,
    run_id: str = "real-replay",
    prices_are_split_adjusted: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> MarketReplayRun:
    """Simulate one cost model while retaining a reconciled diagnostic ledger."""
    config = portfolio_config or PortfolioConfig()
    initial_fx = prepared.fx_by_date[prepared.dates[0]]
    cash: dict[Sleeve, float] = {}
    target_per_slot: dict[Sleeve, float] = {}
    initial_fx_costs: dict[Sleeve, float] = {}
    for sleeve in Sleeve:
        allocation_krw = config.initial_capital_krw * config.sleeve_weights_bps[sleeve] / 10_000
        is_us = sleeve in {Sleeve.US_STOCK, Sleeve.US_ETF}
        initial_cost = allocation_krw * config.initial_fx_cost if is_us else 0.0
        native = (
            (allocation_krw - initial_cost) / initial_fx if is_us else allocation_krw
        )
        cash[sleeve] = native
        target_per_slot[sleeve] = native / 3
        initial_fx_costs[sleeve] = initial_cost

    positions: dict[str, PortfolioPosition] = {}
    pending: list[PlannedOrder] = []
    trades: list[Trade] = []
    closed_round_trips: list[RoundTrip] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    daily_ledger: list[DailyLedgerRow] = []
    review_ledger: list[ReviewLedgerRow] = []
    position_counts: list[dict[str, Any]] = []
    last_prices = np.full(len(prepared.candidate_ids), np.nan)
    total_notional_krw = 0.0
    review_required: set[str] = set()

    for date_index, current_date in enumerate(prepared.dates):
        if progress is not None and date_index % 100 == 0:
            progress(date_index, len(prepared.dates))
        current_fx = prepared.fx_by_date[current_date]
        daily_costs = {sleeve: 0.0 for sleeve in Sleeve}
        daily_dividends = {sleeve: 0.0 for sleeve in Sleeve}
        if date_index == 0:
            daily_costs.update(initial_fx_costs)

        for asset_id, position in list(positions.items()):
            column = prepared.asset_index[asset_id]
            split = prepared.split_ratios[date_index, column]
            if not prices_are_split_adjusted and not np.isnan(split) and split > 0 and split != 1:
                position.quantity = int(position.quantity * split)
            dividend = prepared.dividends[date_index, column]
            if not np.isnan(dividend) and dividend > 0:
                native_dividend = position.quantity * float(dividend)
                dividend_krw = native_dividend * _native_multiplier(position.currency, current_fx)
                cash[position.sleeve] += native_dividend
                position.dividends_krw += dividend_krw
                daily_dividends[position.sleeve] += dividend_krw
            recovery = prepared.recovery_values[date_index, column]
            if not np.isnan(recovery) and recovery > 0:
                proceeds = position.quantity * float(recovery)
                cash[position.sleeve] += proceeds
                proceeds_krw = proceeds * _native_multiplier(position.currency, current_fx)
                total_notional_krw += proceeds_krw
                trades.append(
                    Trade(
                        date=current_date,
                        asset_id=asset_id,
                        symbol=position.symbol,
                        side="SELL",
                        quantity=position.quantity,
                        price=float(recovery),
                        notional=proceeds,
                        cost=0.0,
                        currency=position.currency,
                        reason="DELISTED_RECOVERY",
                        score=position.last_score,
                    )
                )
                closed_round_trips.append(
                    _round_trip(
                        position,
                        status="CLOSED",
                        current_date=current_date,
                        current_price=float(recovery),
                        current_fx=current_fx,
                        exit_cost_krw=0.0,
                        exit_reason="DELISTED_RECOVERY",
                        exit_score=position.last_score,
                    )
                )
                del positions[asset_id]

        remaining: list[PlannedOrder] = []
        for order in sorted(pending, key=lambda item: item.side != "SELL"):
            if order.scheduled_date is None or current_date < order.scheduled_date:
                remaining.append(order)
                continue
            order_column = prepared.asset_index.get(order.asset_id)
            if order_column is None:
                continue
            price = prepared.open_prices[date_index, order_column]
            if np.isnan(price) or price <= 0:
                remaining.append(order)
                continue
            asset = prepared.metadata[order.asset_id]
            group = PeerGroup(asset["peer_group"])
            sleeve = PEER_GROUP_SLEEVE[group]
            currency = str(asset["currency"])
            cost_rate = config.us_trade_cost if currency == "USD" else config.kr_trade_cost
            multiplier = _native_multiplier(currency, current_fx)
            if order.side == "SELL":
                sell_position = positions.get(order.asset_id)
                if sell_position is None:
                    continue
                quantity = sell_position.quantity
                notional = quantity * float(price)
                cost_value = notional * cost_rate
                cost_krw = cost_value * multiplier
                cash[sleeve] += notional - cost_value
                daily_costs[sleeve] += cost_krw
                closed_round_trips.append(
                    _round_trip(
                        sell_position,
                        status="CLOSED",
                        current_date=current_date,
                        current_price=float(price),
                        current_fx=current_fx,
                        exit_cost_krw=cost_krw,
                        exit_reason=order.reason,
                        exit_score=order.score,
                    )
                )
                del positions[order.asset_id]
            else:
                if order.asset_id in positions:
                    continue
                spendable = min(target_per_slot[sleeve], cash[sleeve])
                quantity = math.floor(spendable / (float(price) * (1 + cost_rate)))
                if quantity < 1:
                    continue
                notional = quantity * float(price)
                cost_value = notional * cost_rate
                cost_krw = cost_value * multiplier
                cash[sleeve] -= notional + cost_value
                daily_costs[sleeve] += cost_krw
                positions[order.asset_id] = PortfolioPosition(
                    asset_id=order.asset_id,
                    symbol=str(asset["symbol"]),
                    name=str(asset["name"]),
                    peer_group=group,
                    sleeve=sleeve,
                    currency=currency,
                    quantity=quantity,
                    last_score=float(order.score or 0),
                    entry_date=current_date,
                    decision_date=order.decision_date,
                    signal_date=order.signal_date,
                    entry_score=float(order.score or 0),
                    entry_price=float(price),
                    entry_fx=current_fx,
                    entry_notional_krw=notional * multiplier,
                    cost_basis_krw=(notional + cost_value) * multiplier,
                    entry_cost_krw=cost_krw,
                )
            total_notional_krw += notional * multiplier
            trades.append(
                Trade(
                    date=current_date,
                    asset_id=order.asset_id,
                    symbol=str(asset["symbol"]),
                    side=order.side,
                    quantity=quantity,
                    price=round(float(price), 4),
                    notional=round(notional, 2),
                    cost=round(cost_value, 2),
                    currency=currency,
                    reason=order.reason,
                    decision_date=order.decision_date,
                    signal_date=order.signal_date,
                    score=round(float(order.score), 4) if order.score is not None else None,
                )
            )
        pending = remaining

        available = ~np.isnan(prepared.close_prices[date_index])
        last_prices[available] = prepared.close_prices[date_index, available]
        sleeve_cash_krw: dict[Sleeve, float] = {}
        sleeve_position_krw = {sleeve: 0.0 for sleeve in Sleeve}
        sleeve_counts = {sleeve: 0 for sleeve in Sleeve}
        group_counts = {group: 0 for group in PeerGroup}
        for sleeve, native_cash in cash.items():
            sleeve_cash_krw[sleeve] = native_cash * (
                current_fx if sleeve in {Sleeve.US_STOCK, Sleeve.US_ETF} else 1
            )
        for asset_id, position in positions.items():
            price = last_prices[prepared.asset_index[asset_id]]
            if np.isnan(price):
                continue
            market_value = (
                position.quantity
                * float(price)
                * _native_multiplier(position.currency, current_fx)
            )
            sleeve_position_krw[position.sleeve] += market_value
            sleeve_counts[position.sleeve] += 1
            group_counts[position.peer_group] += 1
        equity = 0.0
        position_total = 0.0
        for sleeve in Sleeve:
            sleeve_equity = sleeve_cash_krw[sleeve] + sleeve_position_krw[sleeve]
            equity += sleeve_equity
            position_total += sleeve_position_krw[sleeve]
            daily_ledger.append(
                DailyLedgerRow(
                    date=current_date,
                    sleeve=sleeve,
                    cash_krw=sleeve_cash_krw[sleeve],
                    position_value_krw=sleeve_position_krw[sleeve],
                    equity_krw=sleeve_equity,
                    exposure=(
                        sleeve_position_krw[sleeve] / sleeve_equity
                        if sleeve_equity > 0
                        else 0.0
                    ),
                    positions_count=sleeve_counts[sleeve],
                    transaction_cost_krw=daily_costs[sleeve],
                    dividend_krw=daily_dividends[sleeve],
                    fx_krw_per_usd=current_fx,
                )
            )
        equity_values.append(equity)
        exposure_values.append(position_total / equity if equity > 0 else 0.0)
        position_counts.append(
            {
                "date": current_date,
                "total": len(positions),
                **{group.value: group_counts[group] for group in PeerGroup},
            }
        )

        rows = prepared.signals_by_review.get(current_date)
        if rows is None:
            continue
        pending = [order for order in pending if order.side == "SELL"]
        pending_sells = {order.asset_id for order in pending}
        plan = plan_weekly_orders(
            rows, positions, config=config, pending_sell_ids=pending_sells
        )
        review_required.update(plan.review_required_assets)
        for group in PeerGroup:
            group_rows = [row for row in rows if row.get("peer_group") == group.value]
            if not group_rows:
                continue
            benchmark_row = next(
                (row for row in group_rows if row.get("benchmark_sma200") is not None),
                group_rows[0],
            )
            allowed = bool(
                benchmark_row.get("benchmark_close") is not None
                and benchmark_row.get("benchmark_sma200") is not None
                and float(benchmark_row["benchmark_close"])
                > float(benchmark_row["benchmark_sma200"])
            )
            held = sum(position.peer_group is group for position in positions.values())
            group_orders = [
                order
                for order in plan.orders
                if (
                    positions[order.asset_id].peer_group
                    if order.asset_id in positions
                    else PeerGroup(prepared.metadata[order.asset_id]["peer_group"])
                )
                is group
            ]
            buys = sum(order.side == "BUY" for order in group_orders)
            sells = sum(order.side == "SELL" for order in group_orders)
            signal_date_value = group_rows[0].get("signal_date") or current_date
            review_ledger.append(
                ReviewLedgerRow(
                    review_date=current_date,
                    signal_date=signal_date_value,
                    peer_group=group,
                    benchmark_entry_allowed=allowed,
                    candidate_count=sum(
                        _row_is_usable(row)
                        and bool(row.get("candidate_eligible"))
                        and float(row.get("trend_score") or 0) >= config.entry_score
                        for row in group_rows
                    ),
                    held_count=held,
                    planned_buy_count=buys,
                    planned_sell_count=sells,
                    projected_held_count=held - sells + buys,
                    review_required_count=sum(
                        asset_id in plan.review_required_assets
                        and positions[asset_id].peer_group is group
                        for asset_id in positions
                    ),
                )
            )
        for row in rows:
            asset_id = str(row["asset_id"])
            if asset_id in positions and _row_is_usable(row):
                positions[asset_id].last_score = float(row["trend_score"])
                review_required.discard(asset_id)
        for order in plan.orders:
            pending.append(
                PlannedOrder(
                    side=order.side,
                    asset_id=order.asset_id,
                    reason=order.reason,
                    market=order.market,
                    scheduled_date=next_trading_date(current_date, order.market),
                    decision_date=current_date,
                    signal_date=order.signal_date,
                    score=order.score,
                )
            )

    if progress is not None:
        progress(len(prepared.dates), len(prepared.dates))

    equity_array = np.asarray(equity_values)
    benchmark = _combined_benchmark(prepared, config)
    metrics, drawdown = calculate_metrics(
        prepared.dates,
        equity_array,
        benchmark,
        total_trade_notional_krw=total_notional_krw,
        average_exposure=float(np.mean(exposure_values)),
        trade_count=len(trades),
        initial_equity=config.initial_capital_krw,
    )
    final_date = prepared.dates[-1]
    final_fx = prepared.fx_by_date[final_date]
    final_positions: list[PositionSnapshot] = []
    open_round_trips: list[RoundTrip] = []
    for asset_id, position in sorted(positions.items()):
        price = float(last_prices[prepared.asset_index[asset_id]])
        final_positions.append(
            PositionSnapshot(
                asset_id=asset_id,
                symbol=position.symbol,
                name=position.name,
                peer_group=position.peer_group,
                sleeve=position.sleeve,
                quantity=position.quantity,
                price=round(price, 4),
                market_value_krw=round(
                    position.quantity
                    * price
                    * _native_multiplier(position.currency, final_fx),
                    0,
                ),
                score=round(position.last_score, 1),
            )
        )
        open_round_trips.append(
            _round_trip(
                position,
                status="OPEN",
                current_date=final_date,
                current_price=price,
                current_fx=final_fx,
                exit_cost_krw=0.0,
                exit_reason="OPEN",
                exit_score=position.last_score,
            )
        )
    result = BacktestResult(
        run_id=run_id,
        data_version=data_version,
        score_version=score_version,
        portfolio_version=config.version,
        config_hash=MARKET_EVENT_VERSION,
        started_on=prepared.dates[0],
        ended_on=prepared.dates[-1],
        metrics=metrics,
        equity_curve=[
            {
                "date": current.isoformat(),
                "portfolio": round(float(equity_array[index]), 0),
                "benchmark": round(float(benchmark[index]), 0),
            }
            for index, current in enumerate(prepared.dates)
        ],
        drawdown_curve=[
            {"date": current.isoformat(), "drawdown": round(float(drawdown[index]), 6)}
            for index, current in enumerate(prepared.dates)
        ],
        trades=trades,
        final_positions=final_positions,
        review_required_assets=sorted(review_required),
        warnings=[
            "현재 상장 종목 기준 과거 재생으로 생존편향이 포함됩니다.",
            "과거 시뮬레이션 결과는 투자 추천이나 공식 성과가 아닙니다.",
        ],
    )
    return MarketReplayRun(
        result=result,
        daily_ledger=daily_ledger,
        review_ledger=review_ledger,
        round_trips=[*closed_round_trips, *open_round_trips],
        position_counts=position_counts,
        equity_values=equity_values,
        benchmark_values=benchmark.tolist(),
    )


def run_market_replay(
    bars: pl.DataFrame,
    weekly_signals: pl.DataFrame,
    reference: pl.DataFrame,
    *,
    data_version: str,
    score_version: str,
    portfolio_config: PortfolioConfig | None = None,
    run_id: str = "real-replay",
    asset_metadata: pl.DataFrame | None = None,
    prices_are_split_adjusted: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> BacktestResult:
    """Compatibility wrapper for callers that only need the portfolio result."""
    config = portfolio_config or PortfolioConfig()
    prepared = prepare_market_replay(
        bars,
        weekly_signals,
        reference,
        portfolio_config=config,
        asset_metadata=asset_metadata,
    )
    return simulate_prepared_replay(
        prepared,
        data_version=data_version,
        score_version=score_version,
        portfolio_config=config,
        run_id=run_id,
        prices_are_split_adjusted=prices_are_split_adjusted,
        progress=progress,
    ).result
