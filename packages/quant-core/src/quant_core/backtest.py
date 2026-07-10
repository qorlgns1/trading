import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from quant_core.config import (
    PEER_GROUP_SLEEVE,
    PEER_GROUP_SLOTS,
    PortfolioConfig,
    TrendScoreConfig,
)
from quant_core.enums import PeerGroup, Sleeve
from quant_core.metrics import calculate_metrics
from quant_core.models import BacktestResult, PositionSnapshot, Trade
from quant_core.scoring import score_trends


@dataclass
class _Position:
    asset_id: str
    symbol: str
    name: str
    peer_group: PeerGroup
    sleeve: Sleeve
    currency: str
    quantity: int
    last_score: float


@dataclass(frozen=True)
class _Order:
    side: str
    asset_id: str
    reason: str


def _portfolio_hash(config: PortfolioConfig, data_version: str) -> str:
    payload = {
        "data_version": data_version,
        "portfolio_version": config.version,
        "initial_capital_krw": config.initial_capital_krw,
        "sleeve_weights_bps": {
            sleeve.value: weight for sleeve, weight in config.sleeve_weights_bps.items()
        },
        "costs": [config.us_trade_cost, config.kr_trade_cost, config.initial_fx_cost],
        "scores": [config.entry_score, config.exit_score],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]


def _wide_matrix(frame: pl.DataFrame, value: str, asset_ids: list[str]) -> np.ndarray:
    wide = (
        frame.select("date", "asset_id", value)
        .pivot(on="asset_id", index="date", values=value, aggregate_function="first")
        .sort("date")
    )
    for asset_id in asset_ids:
        if asset_id not in wide.columns:
            wide = wide.with_columns(pl.lit(None).cast(pl.Float64).alias(asset_id))
    return wide.select(asset_ids).to_numpy()


def _weekly_dates(dates: list[date]) -> set[date]:
    weekly: set[date] = set()
    for index, current in enumerate(dates):
        is_last_date = index == len(dates) - 1
        is_last_session_of_week = (
            not is_last_date
            and current.isocalendar()[:2] != dates[index + 1].isocalendar()[:2]
        )
        if is_last_date or is_last_session_of_week:
            weekly.add(current)
    return weekly


def _benchmark_curve(
    bars: pl.DataFrame,
    dates: list[date],
    fx: np.ndarray,
    config: PortfolioConfig,
) -> np.ndarray:
    grouped = (
        bars.group_by("date", "peer_group")
        .agg(pl.col("benchmark_close").first())
        .pivot(on="peer_group", index="date", values="benchmark_close")
        .sort("date")
    )
    normalized: dict[PeerGroup, np.ndarray] = {}
    for group in PeerGroup:
        series = grouped.get_column(group.value).to_numpy()
        if group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF}:
            series = series * fx
        normalized[group] = series / series[0]
    sleeve_index = {
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
    combined = np.zeros(len(dates))
    for sleeve, values in sleeve_index.items():
        combined += values * (config.sleeve_weights_bps[sleeve] / 10_000)
    return config.initial_capital_krw * combined


def run_reference_backtest(
    bars: pl.DataFrame,
    *,
    data_version: str,
    portfolio_config: PortfolioConfig | None = None,
    score_config: TrendScoreConfig | None = None,
    scored_bars: pl.DataFrame | None = None,
) -> BacktestResult:
    portfolio_config = portfolio_config or PortfolioConfig()
    score_config = score_config or TrendScoreConfig()
    scored = scored_bars if scored_bars is not None else score_trends(
        bars, score_config, data_version=data_version
    )

    dates = bars.get_column("date").unique().sort().to_list()
    if len(dates) < 253:
        raise ValueError("백테스트에는 최소 253개 거래일이 필요합니다.")
    universe = (
        bars.select("asset_id", "symbol", "name", "peer_group", "currency")
        .unique(subset=["asset_id"], maintain_order=True)
        .sort("asset_id")
    )
    metadata = {row["asset_id"]: row for row in universe.iter_rows(named=True)}
    asset_ids = universe.get_column("asset_id").to_list()
    asset_index = {asset_id: index for index, asset_id in enumerate(asset_ids)}
    open_prices = _wide_matrix(bars, "open", asset_ids)
    close_prices = _wide_matrix(bars, "close", asset_ids)
    split_ratios = _wide_matrix(bars, "split_ratio", asset_ids)
    dividends = _wide_matrix(bars, "dividend", asset_ids)
    recovery_values = _wide_matrix(bars, "recovery_value", asset_ids)
    fx = (
        bars.group_by("date")
        .agg(pl.col("fx_krw_per_usd").first())
        .sort("date")
        .get_column("fx_krw_per_usd")
        .to_numpy()
    )

    weekly_dates = _weekly_dates(dates)
    signal_columns = [
        "date",
        "asset_id",
        "peer_group",
        "trend_score",
        "relative_momentum",
        "data_eligible",
        "candidate_eligible",
        "benchmark_close",
        "benchmark_sma200",
    ]
    weekly_rows = scored.filter(pl.col("date").is_in(list(weekly_dates))).select(signal_columns)
    signals: dict[date, dict[str, dict[str, Any]]] = {}
    for row in weekly_rows.iter_rows(named=True):
        signals.setdefault(row["date"], {})[row["asset_id"]] = row

    initial_fx = float(fx[0])
    cash: dict[Sleeve, float] = {}
    target_per_slot: dict[Sleeve, float] = {}
    for sleeve in Sleeve:
        allocation_krw = (
            portfolio_config.initial_capital_krw
            * portfolio_config.sleeve_weights_bps[sleeve]
            / 10_000
        )
        if sleeve in {Sleeve.US_STOCK, Sleeve.US_ETF}:
            native = allocation_krw * (1 - portfolio_config.initial_fx_cost) / initial_fx
        else:
            native = allocation_krw
        cash[sleeve] = native
        target_per_slot[sleeve] = native / 3

    positions: dict[str, _Position] = {}
    pending: dict[int, list[_Order]] = {}
    trades: list[Trade] = []
    equity_values: list[float] = []
    exposure_values: list[float] = []
    last_prices = np.full(len(asset_ids), np.nan)
    last_scores: dict[str, float] = {}
    total_notional_krw = 0.0

    for date_index, current_date in enumerate(dates):
        current_fx = float(fx[date_index])

        for asset_id, position in list(positions.items()):
            column = asset_index[asset_id]
            split_ratio = split_ratios[date_index, column]
            if not np.isnan(split_ratio) and split_ratio > 1:
                position.quantity = int(position.quantity * split_ratio)
            dividend = dividends[date_index, column]
            if not np.isnan(dividend) and dividend > 0:
                cash[position.sleeve] += position.quantity * float(dividend)
            recovery = recovery_values[date_index, column]
            if not np.isnan(recovery) and recovery > 0:
                proceeds = position.quantity * float(recovery)
                cash[position.sleeve] += proceeds
                notional_krw = proceeds * (current_fx if position.currency == "USD" else 1)
                total_notional_krw += notional_krw
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
                    )
                )
                del positions[asset_id]

        orders = pending.pop(date_index, [])
        deferred: list[_Order] = []
        for order in sorted(orders, key=lambda value: value.side != "SELL"):
            column = asset_index[order.asset_id]
            price = open_prices[date_index, column]
            if np.isnan(price) or price <= 0:
                deferred.append(order)
                continue
            asset = metadata[order.asset_id]
            peer_group = PeerGroup(asset["peer_group"])
            sleeve = PEER_GROUP_SLEEVE[peer_group]
            is_us = asset["currency"] == "USD"
            cost_rate = (
                portfolio_config.us_trade_cost if is_us else portfolio_config.kr_trade_cost
            )
            if order.side == "SELL":
                sell_position = positions.get(order.asset_id)
                if sell_position is None:
                    continue
                notional = sell_position.quantity * float(price)
                cost_value = notional * cost_rate
                cash[sleeve] += notional - cost_value
                quantity = sell_position.quantity
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
                cash[sleeve] -= notional + cost_value
                row = signals.get(dates[max(date_index - 1, 0)], {}).get(order.asset_id, {})
                positions[order.asset_id] = _Position(
                    asset_id=order.asset_id,
                    symbol=asset["symbol"],
                    name=asset["name"],
                    peer_group=peer_group,
                    sleeve=sleeve,
                    currency=asset["currency"],
                    quantity=quantity,
                    last_score=float(row.get("trend_score") or last_scores.get(order.asset_id, 0)),
                )
            notional_krw = notional * (current_fx if is_us else 1)
            total_notional_krw += notional_krw
            trades.append(
                Trade(
                    date=current_date,
                    asset_id=order.asset_id,
                    symbol=asset["symbol"],
                    side=order.side,
                    quantity=quantity,
                    price=round(float(price), 4),
                    notional=round(notional, 2),
                    cost=round(cost_value, 2),
                    currency=asset["currency"],
                    reason=order.reason,
                )
            )
        if deferred and date_index + 1 < len(dates):
            pending.setdefault(date_index + 1, []).extend(deferred)

        available = ~np.isnan(close_prices[date_index])
        last_prices[available] = close_prices[date_index, available]
        cash_value_krw = sum(
            value * (current_fx if sleeve in {Sleeve.US_STOCK, Sleeve.US_ETF} else 1)
            for sleeve, value in cash.items()
        )
        position_value_krw = 0.0
        for asset_id, position in positions.items():
            price = last_prices[asset_index[asset_id]]
            if np.isnan(price):
                continue
            native_value = position.quantity * float(price)
            position_value_krw += native_value * (
                current_fx if position.currency == "USD" else 1
            )
        calculated_equity = cash_value_krw + position_value_krw
        equity = (
            portfolio_config.initial_capital_krw if date_index == 0 else calculated_equity
        )
        equity_values.append(equity)
        exposure_values.append(position_value_krw / equity if equity > 0 else 0.0)

        rows = signals.get(current_date)
        if rows is None or date_index + 1 >= len(dates):
            continue
        for asset_id, row in rows.items():
            if bool(row["data_eligible"]) and row["trend_score"] is not None:
                last_scores[asset_id] = float(row["trend_score"])
                if asset_id in positions:
                    positions[asset_id].last_score = float(row["trend_score"])

        planned_exits: set[str] = set()
        new_orders: list[_Order] = []
        for asset_id in positions:
            position_row = rows.get(asset_id)
            if position_row is None:
                continue
            failed_gate = not bool(position_row["candidate_eligible"])
            fell_below_exit_score = (
                float(position_row["trend_score"] or 0) < portfolio_config.exit_score
            )
            if failed_gate or fell_below_exit_score:
                planned_exits.add(asset_id)
                new_orders.append(_Order("SELL", asset_id, "EXIT_RULE"))

        held_after_exits = set(positions) - planned_exits
        for peer_group in PeerGroup:
            sleeve = PEER_GROUP_SLEEVE[peer_group]
            if portfolio_config.sleeve_weights_bps[sleeve] == 0:
                continue
            held_count = sum(
                positions[asset_id].peer_group == peer_group for asset_id in held_after_exits
            )
            vacancies = PEER_GROUP_SLOTS[peer_group] - held_count
            if vacancies <= 0:
                continue
            group_rows = [
                row for row in rows.values() if row["peer_group"] == peer_group.value
            ]
            benchmark_row = next(
                (row for row in group_rows if row["benchmark_sma200"] is not None), None
            )
            if benchmark_row is None or float(benchmark_row["benchmark_close"]) <= float(
                benchmark_row["benchmark_sma200"]
            ):
                continue
            candidates = [
                row
                for row in group_rows
                if bool(row["candidate_eligible"])
                and float(row["trend_score"] or 0) >= portfolio_config.entry_score
                and row["asset_id"] not in held_after_exits
            ]
            candidates.sort(
                key=lambda row: (
                    -float(row["trend_score"] or 0),
                    -float(row["relative_momentum"] or -999),
                    row["asset_id"],
                )
            )
            for candidate in candidates[:vacancies]:
                new_orders.append(_Order("BUY", candidate["asset_id"], "WEEKLY_ENTRY"))
        pending.setdefault(date_index + 1, []).extend(new_orders)

    equity_array = np.asarray(equity_values)
    benchmark = _benchmark_curve(bars, dates, fx, portfolio_config)
    metrics, drawdown = calculate_metrics(
        dates,
        equity_array,
        benchmark,
        total_trade_notional_krw=total_notional_krw,
        average_exposure=float(np.mean(exposure_values)),
        trade_count=len(trades),
    )
    final_fx = float(fx[-1])
    final_positions = []
    for asset_id, position in sorted(positions.items()):
        price = float(last_prices[asset_index[asset_id]])
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
                    * (final_fx if position.currency == "USD" else 1),
                    0,
                ),
                score=round(position.last_score, 1),
            )
        )

    return BacktestResult(
        run_id=str(uuid.uuid4()),
        data_version=data_version,
        score_version=score_config.version,
        portfolio_version=portfolio_config.version,
        config_hash=_portfolio_hash(portfolio_config, data_version),
        started_on=dates[0],
        ended_on=dates[-1],
        metrics=metrics,
        equity_curve=[
            {
                "date": current.isoformat(),
                "portfolio": round(float(equity_array[index]), 0),
                "benchmark": round(float(benchmark[index]), 0),
            }
            for index, current in enumerate(dates)
        ],
        drawdown_curve=[
            {"date": current.isoformat(), "drawdown": round(float(drawdown[index]), 6)}
            for index, current in enumerate(dates)
        ],
        trades=trades,
        final_positions=final_positions,
        warnings=[
            "공개 데모는 실제 종목이 아닌 결정적 가상 시장 데이터를 사용합니다.",
            "실제 데이터 모드는 현재 구성 종목을 사용하므로 생존편향이 포함될 수 있습니다.",
        ],
    )
