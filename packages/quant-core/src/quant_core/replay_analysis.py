from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date
from statistics import median
from typing import Any

import numpy as np

from quant_core.config import PEER_GROUP_SLEEVE, PortfolioConfig
from quant_core.enums import PeerGroup, Sleeve
from quant_core.market_portfolio import (
    DailyLedgerRow,
    MarketReplayRun,
    PreparedMarketReplay,
    ReviewLedgerRow,
    RoundTrip,
    delayed_trading_date,
    market_for_group,
)

REPLAY_ANALYSIS_VERSION = "replay-analysis-v2.0.0"


class ReplayInvariantError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayAnalysisBuild:
    analysis: dict[str, Any]
    exposure_matched_curve: list[float]


def _period_return(end: float, start: float) -> float:
    return end / start - 1 if start > 0 else 0.0


def _period_drawdown(values: list[float], start: float) -> float:
    series = np.asarray([start, *values], dtype=float)
    running_max = np.maximum.accumulate(series)
    return float(np.min(series / running_max - 1))


def _total_exposure(rows: list[DailyLedgerRow]) -> float:
    equity = sum(row.equity_krw for row in rows)
    positions = sum(row.position_value_krw for row in rows)
    return positions / equity if equity > 0 else 0.0


def _exposure_matched_curve(
    prepared: PreparedMarketReplay,
    actual: MarketReplayRun,
    config: PortfolioConfig,
) -> list[float]:
    exposures = {(row.date, row.sleeve): row.exposure for row in actual.daily_ledger}
    sleeve_values = {
        sleeve: config.initial_capital_krw * config.sleeve_weights_bps[sleeve] / 10_000
        for sleeve in Sleeve
    }
    combined = [sum(sleeve_values.values())]
    for index in range(1, len(prepared.dates)):
        previous_date = prepared.dates[index - 1]
        for sleeve in Sleeve:
            benchmark = prepared.benchmark_indexes[sleeve]
            benchmark_return = float(benchmark[index] / benchmark[index - 1] - 1)
            prior_exposure = exposures[(previous_date, sleeve)]
            sleeve_values[sleeve] *= 1 + prior_exposure * benchmark_return
        combined.append(sum(sleeve_values.values()))
    return combined


def _period_rows(
    prepared: PreparedMarketReplay,
    actual: MarketReplayRun,
    exposure_matched: list[float],
    *,
    monthly: bool,
    config: PortfolioConfig,
) -> list[dict[str, Any]]:
    ledger_by_date: dict[date, list[DailyLedgerRow]] = defaultdict(list)
    for row in actual.daily_ledger:
        ledger_by_date[row.date].append(row)
    trades_by_date: dict[date, int] = defaultdict(int)
    for trade in actual.result.trades:
        trades_by_date[trade.date] += 1
    costs_by_date: dict[date, float] = defaultdict(float)
    for row in actual.daily_ledger:
        costs_by_date[row.date] += row.transaction_cost_krw

    groups: dict[str, list[int]] = {}
    for index, current in enumerate(prepared.dates):
        key = current.strftime("%Y-%m") if monthly else str(current.year)
        groups.setdefault(key, []).append(index)
    rows: list[dict[str, Any]] = []
    prior_strategy = config.initial_capital_krw
    prior_benchmark = config.initial_capital_krw
    prior_matched = config.initial_capital_krw
    for key, indexes in groups.items():
        end_index = indexes[-1]
        dates = [prepared.dates[index] for index in indexes]
        strategy_values = [actual.equity_values[index] for index in indexes]
        strategy_return = _period_return(strategy_values[-1], prior_strategy)
        benchmark_return = _period_return(actual.benchmark_values[end_index], prior_benchmark)
        matched_return = _period_return(exposure_matched[end_index], prior_matched)
        exposures = [_total_exposure(ledger_by_date[current]) for current in dates]
        period_row: dict[str, Any] = {
            "period": key,
            "strategy_return": round(strategy_return, 6),
            "benchmark_return": round(benchmark_return, 6),
            "exposure_matched_return": round(matched_return, 6),
            "excess_return": round(strategy_return - benchmark_return, 6),
        }
        if not monthly:
            period_row.update(
                {
                    "max_drawdown": round(_period_drawdown(strategy_values, prior_strategy), 6),
                    "average_exposure": round(float(np.mean(exposures)), 6),
                    "trade_count": sum(trades_by_date[current] for current in dates),
                    "cost_krw": round(sum(costs_by_date[current] for current in dates), 0),
                }
            )
        rows.append(period_row)
        prior_strategy = strategy_values[-1]
        prior_benchmark = actual.benchmark_values[end_index]
        prior_matched = exposure_matched[end_index]
    return rows


def _sleeve_attribution(
    actual: MarketReplayRun,
    prepared: PreparedMarketReplay,
    config: PortfolioConfig,
) -> list[dict[str, Any]]:
    final_date = prepared.dates[-1]
    final_rows = {row.sleeve: row for row in actual.daily_ledger if row.date == final_date}
    rows_by_sleeve: dict[Sleeve, list[DailyLedgerRow]] = defaultdict(list)
    for row in actual.daily_ledger:
        rows_by_sleeve[row.sleeve].append(row)
    trade_counts: dict[Sleeve, int] = defaultdict(int)
    for trade in actual.result.trades:
        metadata = prepared.metadata.get(trade.asset_id)
        if metadata is not None:
            group = PeerGroup(str(metadata["peer_group"]))
            trade_counts[PEER_GROUP_SLEEVE[group]] += 1
    output: list[dict[str, Any]] = []
    for sleeve in Sleeve:
        initial = config.initial_capital_krw * config.sleeve_weights_bps[sleeve] / 10_000
        ending = final_rows[sleeve].equity_krw
        pnl = ending - initial
        sleeve_rows = rows_by_sleeve[sleeve]
        output.append(
            {
                "sleeve": sleeve.value,
                "initial_allocation_krw": round(initial, 2),
                "ending_value_krw": round(ending, 2),
                "pnl_krw": round(pnl, 2),
                "return": round(_period_return(ending, initial), 6) if initial > 0 else 0.0,
                "contribution": round(pnl / config.initial_capital_krw, 6),
                "average_exposure": round(float(np.mean([row.exposure for row in sleeve_rows])), 6),
                "trade_count": trade_counts[sleeve],
                "cost_krw": round(sum(row.transaction_cost_krw for row in sleeve_rows), 0),
                "dividend_krw": round(sum(row.dividend_krw for row in sleeve_rows), 0),
            }
        )
    return output


def _trade_summary(round_trips: list[RoundTrip]) -> dict[str, Any]:
    closed = [item for item in round_trips if item.status == "CLOSED"]
    opened = [item for item in round_trips if item.status == "OPEN"]
    wins = [item for item in closed if item.net_pnl_krw > 0]
    losses = [item for item in closed if item.net_pnl_krw < 0]
    average_gain = float(np.mean([item.net_return for item in wins])) if wins else 0.0
    average_loss = float(np.mean([item.net_return for item in losses])) if losses else 0.0
    gross_profit = sum(item.net_pnl_krw for item in wins)
    gross_loss = abs(sum(item.net_pnl_krw for item in losses))
    return {
        "closed_count": len(closed),
        "open_count": len(opened),
        "win_rate": round(len(wins) / len(closed), 6) if closed else 0.0,
        "average_gain": round(average_gain, 6),
        "average_loss": round(average_loss, 6),
        "payoff_ratio": round(average_gain / abs(average_loss), 4) if average_loss < 0 else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else 0.0,
        "median_holding_days": round(float(median(item.holding_days for item in closed)), 1)
        if closed
        else 0.0,
        "net_pnl_krw": round(sum(item.net_pnl_krw for item in closed), 0),
    }


def _round_trip_dict(item: RoundTrip) -> dict[str, Any]:
    payload = asdict(item)
    payload["peer_group"] = item.peer_group.value
    payload["sleeve"] = item.sleeve.value
    payload["entry_date"] = item.entry_date.isoformat()
    payload["exit_date"] = item.exit_date.isoformat() if item.exit_date else None
    for key in (
        "entry_score",
        "exit_score",
        "entry_price",
        "exit_price",
        "entry_notional_krw",
        "exit_value_krw",
        "dividends_krw",
        "costs_krw",
        "net_pnl_krw",
        "net_return",
    ):
        value = payload[key]
        if isinstance(value, float):
            payload[key] = round(value, 6 if key == "net_return" else 2)
    return payload


def _trade_analysis(round_trips: list[RoundTrip]) -> dict[str, Any]:
    closed = [item for item in round_trips if item.status == "CLOSED"]
    by_sleeve = [
        {"sleeve": sleeve.value, **_trade_summary([x for x in round_trips if x.sleeve is sleeve])}
        for sleeve in Sleeve
    ]
    score_bands: list[tuple[str, Callable[[float], bool]]] = [
        ("65-69", lambda value: 65 <= value < 70),
        ("70-79", lambda value: 70 <= value < 80),
        ("80+", lambda value: value >= 80),
    ]
    by_entry_score = []
    for label, predicate in score_bands:
        items = [item for item in closed if predicate(item.entry_score)]
        summary = _trade_summary(items)
        by_entry_score.append({"band": label, **summary})
    exit_reasons = []
    for reason in sorted({item.exit_reason for item in closed}):
        items = [item for item in closed if item.exit_reason == reason]
        exit_reasons.append({"reason": reason, **_trade_summary(items)})
    ordered = sorted(closed, key=lambda item: (item.net_pnl_krw, item.asset_id))
    return {
        "overall": _trade_summary(round_trips),
        "by_sleeve": by_sleeve,
        "by_entry_score": by_entry_score,
        "by_exit_reason": exit_reasons,
        "best_trades": [_round_trip_dict(item) for item in reversed(ordered[-5:])],
        "worst_trades": [_round_trip_dict(item) for item in ordered[:5]],
    }


def _market_regimes(review_ledger: list[ReviewLedgerRow]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for group in PeerGroup:
        rows = [row for row in review_ledger if row.peer_group is group]
        allowed = sum(row.benchmark_entry_allowed for row in rows)
        output.append(
            {
                "peer_group": group.value,
                "review_count": len(rows),
                "entry_allowed_count": allowed,
                "entry_blocked_count": len(rows) - allowed,
                "entry_allowed_rate": round(allowed / len(rows), 6) if rows else 0.0,
                "average_candidate_count": round(
                    float(np.mean([row.candidate_count for row in rows])), 1
                )
                if rows
                else 0.0,
                "planned_buy_count": sum(row.planned_buy_count for row in rows),
                "planned_sell_count": sum(row.planned_sell_count for row in rows),
                "average_held_count": round(float(np.mean([row.held_count for row in rows])), 2)
                if rows
                else 0.0,
            }
        )
    return output


def _check(
    code: str, label: str, passed: bool, detail: str, *, severity: str = "ERROR"
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "status": "PASS" if passed else "FAIL",
        "severity": severity,
        "detail": detail,
    }


def _integrity_checks(
    prepared: PreparedMarketReplay,
    actual: MarketReplayRun,
    sleeve_attribution: list[dict[str, Any]],
    reconciliation_error: float,
    config: PortfolioConfig,
) -> list[dict[str, Any]]:
    timed_trades = [trade for trade in actual.result.trades if trade.decision_date is not None]
    signal_order_ok = all(
        trade.signal_date is None
        or (
            trade.decision_date is not None
            and trade.signal_date <= trade.decision_date < trade.date
        )
        for trade in timed_trades
    )

    def used_first_valid_open(trade_index: int) -> bool:
        trade = timed_trades[trade_index]
        if trade.decision_date is None:
            return False
        metadata = prepared.metadata.get(trade.asset_id)
        column = prepared.asset_index.get(trade.asset_id)
        if metadata is None or column is None:
            return False
        group = PeerGroup(str(metadata["peer_group"]))
        scheduled = delayed_trading_date(
            trade.decision_date,
            market_for_group(group),
            config.execution_delay_sessions,
        )
        for index, current in enumerate(prepared.dates):
            if current < scheduled:
                continue
            price = prepared.open_prices[index, column]
            if np.isfinite(price) and price > 0:
                return current == trade.date
            if current >= trade.date:
                return False
        return False

    next_open_ok = all(used_first_valid_open(index) for index in range(len(timed_trades)))
    max_positions = max((int(row["total"]) for row in actual.position_counts), default=0)
    group_overages = [
        (group, max(int(row[group.value]) for row in actual.position_counts))
        for group in PeerGroup
        if actual.position_counts
        and max(int(row[group.value]) for row in actual.position_counts)
        > config.peer_group_slots[group]
    ]
    minimum_cash = min((row.cash_krw for row in actual.daily_ledger), default=0.0)
    ledger_by_date: dict[date, float] = defaultdict(float)
    for row in actual.daily_ledger:
        ledger_by_date[row.date] += row.equity_krw
    equity_error = max(
        (
            abs(ledger_by_date[current] - actual.equity_values[index])
            for index, current in enumerate(prepared.dates)
        ),
        default=0.0,
    )
    sleeve_pnl = sum(float(row["pnl_krw"]) for row in sleeve_attribution)
    total_pnl = actual.equity_values[-1] - config.initial_capital_krw
    sleeve_error = abs(sleeve_pnl - total_pnl)
    finite = all(
        np.isfinite(value) and value > 0
        for values in (
            actual.equity_values,
            actual.benchmark_values,
        )
        for value in values
    )
    return [
        _check(
            "SIGNAL_TIME_ORDER",
            "신호 시점 순서",
            signal_order_ok,
            f"시점 정보가 있는 거래 {len(timed_trades):,}건 검사",
        ),
        _check(
            "NEXT_OPEN_EXECUTION",
            "다음 거래일 시가 체결",
            next_open_ok,
            "모든 주문 결정일이 체결일보다 앞섭니다."
            if next_open_ok
            else "결정일과 같거나 이전에 체결된 거래가 있습니다.",
        ),
        _check(
            "MAX_POSITION_COUNT",
            "설정된 최대 보유 종목",
            max_positions <= sum(config.peer_group_slots.values()),
            f"관측된 최대 보유 종목은 {max_positions}개입니다.",
        ),
        _check(
            "PEER_GROUP_CAPACITY",
            "비교군별 슬롯",
            not group_overages,
            "모든 비교군이 슬롯 한도를 지켰습니다."
            if not group_overages
            else ", ".join(f"{group.value} {count}개" for group, count in group_overages),
        ),
        _check(
            "NON_NEGATIVE_CASH",
            "음수 현금 방지",
            minimum_cash >= -0.01,
            f"최소 자산군 현금은 {minimum_cash:,.2f}원입니다.",
        ),
        _check(
            "EQUITY_RECONCILIATION",
            "일별 가치 합계",
            equity_error <= 1.0,
            f"일별 최대 합계 오차는 {equity_error:,.4f}원입니다.",
        ),
        _check(
            "SLEEVE_RECONCILIATION",
            "자산군 손익 합계",
            sleeve_error <= 1.0,
            f"최종 손익 합계 오차는 {sleeve_error:,.2f}원입니다.",
        ),
        _check(
            "GAP_RECONCILIATION",
            "성과 차이 분해",
            abs(reconciliation_error) <= 0.0001,
            f"분해 오차는 {reconciliation_error * 10_000:,.4f}bp입니다.",
        ),
        _check(
            "FINITE_PERFORMANCE",
            "성과 값 유효성",
            finite,
            "성과 곡선에 NaN, 무한대 또는 0 이하 값이 없습니다."
            if finite
            else "성과 곡선에 유효하지 않은 값이 있습니다.",
        ),
    ]


def _headline(
    gap: dict[str, float], annual: list[dict[str, Any]], average_exposure: float
) -> dict[str, Any]:
    effects = {
        "시장 노출·진입 제한": gap["exposure_effect"],
        "종목 선택·체결": gap["selection_execution_effect"],
        "매매·환전 비용": gap["cost_effect"],
    }
    largest_label, largest_value = max(effects.items(), key=lambda item: abs(item[1]))
    worst = min(annual, key=lambda row: float(row["excess_return"]))
    benchmark_gap = gap["actual_strategy_return"] - gap["full_benchmark_return"]
    direction = "높습니다" if benchmark_gap >= 0 else "낮습니다"
    return {
        "title": f"전략 누적수익률은 벤치마크보다 {abs(benchmark_gap) * 100:.1f}%p {direction}.",
        "summary": (
            f"분해상 가장 큰 항목은 {largest_label} ({largest_value * 100:+.1f}%p)입니다. "
            f"평균 시장 노출도는 {average_exposure * 100:.1f}%였습니다."
        ),
        "largest_effect_label": largest_label,
        "largest_effect": round(largest_value, 6),
        "largest_gap_period": str(worst["period"]),
        "largest_gap_excess_return": float(worst["excess_return"]),
    }


def analyze_replay(
    prepared: PreparedMarketReplay,
    actual: MarketReplayRun,
    no_cost: MarketReplayRun,
    *,
    portfolio_config: PortfolioConfig | None = None,
) -> ReplayAnalysisBuild:
    config = portfolio_config or PortfolioConfig()
    initial = config.initial_capital_krw
    exposure_matched = _exposure_matched_curve(prepared, actual, config)
    full_return = _period_return(actual.benchmark_values[-1], initial)
    matched_return = _period_return(exposure_matched[-1], initial)
    no_cost_return = _period_return(no_cost.equity_values[-1], initial)
    actual_return = _period_return(actual.equity_values[-1], initial)
    gap = {
        "full_benchmark_return": round(full_return, 6),
        "exposure_matched_return": round(matched_return, 6),
        "no_cost_strategy_return": round(no_cost_return, 6),
        "actual_strategy_return": round(actual_return, 6),
        "exposure_effect": round(matched_return - full_return, 6),
        "selection_execution_effect": round(no_cost_return - matched_return, 6),
        "cost_effect": round(actual_return - no_cost_return, 6),
    }
    reconciliation_error = actual_return - (
        full_return
        + (matched_return - full_return)
        + (no_cost_return - matched_return)
        + (actual_return - no_cost_return)
    )
    gap["reconciliation_error"] = round(reconciliation_error, 10)
    annual = _period_rows(
        prepared,
        actual,
        exposure_matched,
        monthly=False,
        config=config,
    )
    monthly = _period_rows(
        prepared,
        actual,
        exposure_matched,
        monthly=True,
        config=config,
    )
    sleeves = _sleeve_attribution(actual, prepared, config)
    checks = _integrity_checks(
        prepared,
        actual,
        sleeves,
        reconciliation_error,
        config,
    )
    failed = [check for check in checks if check["status"] == "FAIL"]
    if failed:
        labels = ", ".join(str(check["label"]) for check in failed)
        raise ReplayInvariantError(f"과거 재생 무결성 검사 실패: {labels}")
    explicit_cost = sum(row.transaction_cost_krw for row in actual.daily_ledger)
    initial_fx_cost = sum(
        config.initial_capital_krw
        * config.sleeve_weights_bps[sleeve]
        / 10_000
        * config.initial_fx_cost
        for sleeve in (Sleeve.US_STOCK, Sleeve.US_ETF)
    )
    analysis = {
        "version": REPLAY_ANALYSIS_VERSION,
        "headline": _headline(gap, annual, actual.result.metrics["average_exposure"]),
        "gap_analysis": gap,
        "cost_summary": {
            "initial_fx_cost_krw": round(initial_fx_cost, 0),
            "trade_cost_krw": round(explicit_cost - initial_fx_cost, 0),
            "explicit_cost_krw": round(explicit_cost, 0),
            "compounded_cost_drag_krw": round(
                no_cost.equity_values[-1] - actual.equity_values[-1], 0
            ),
            "cost_drag": round(no_cost_return - actual_return, 6),
        },
        "annual_periods": annual,
        "monthly_periods": monthly,
        "sleeve_attribution": sleeves,
        "trade_analysis": _trade_analysis(actual.round_trips),
        "market_regimes": _market_regimes(actual.review_ledger),
        "integrity_checks": checks,
    }
    return ReplayAnalysisBuild(
        analysis=analysis,
        exposure_matched_curve=exposure_matched,
    )
