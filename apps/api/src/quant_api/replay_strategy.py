import hashlib
from datetime import date, timedelta
from typing import Any

import orjson
from quant_core.config import (
    REPLAY_PORTFOLIO_VERSION,
    REPLAY_SCORE_VERSION,
    PortfolioConfig,
    TrendScoreConfig,
)
from quant_core.enums import ExperimentObjective, PeerGroup, Sleeve

from quant_api.schemas import (
    ReplayDataConfig,
    ReplayStrategyConfig,
    ReplaySuccessCriteria,
)


def strategy_hash(strategy: ReplayStrategyConfig) -> str:
    return hashlib.sha256(
        orjson.dumps(strategy.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
    ).hexdigest()


def strategy_domain(
    strategy: ReplayStrategyConfig,
) -> tuple[TrendScoreConfig, PortfolioConfig]:
    signal = strategy.signal
    portfolio = strategy.portfolio
    execution = strategy.execution
    slots = portfolio.peer_group_slots
    enabled = set(strategy.data.peer_groups)
    slot_map = {
        PeerGroup.US_STOCK: slots.us_stock,
        PeerGroup.US_EQUITY_ETF: slots.us_equity_etf,
        PeerGroup.KR_KOSPI: slots.kr_kospi,
        PeerGroup.KR_KOSDAQ: slots.kr_kosdaq,
        PeerGroup.KR_DOMESTIC_EQUITY_ETF: slots.kr_domestic_equity_etf,
        PeerGroup.KR_OVERSEAS_EQUITY_ETF: slots.kr_overseas_equity_etf,
    }
    slot_map = {group: count if group in enabled else 0 for group, count in slot_map.items()}
    score_config = TrendScoreConfig(
        version=REPLAY_SCORE_VERSION,
        component_weights_bps=signal.component_weights_bps.model_dump(),
        require_above_sma200=signal.require_above_sma200,
        require_positive_six_month=signal.require_positive_six_month,
        require_absolute_liquidity=signal.require_absolute_liquidity,
        require_order_size_liquidity=signal.require_order_size_liquidity,
        minimum_adv_multiplier=signal.minimum_adv_multiplier,
    )
    weights = portfolio.sleeve_weights_bps
    portfolio_config = PortfolioConfig(
        version=REPLAY_PORTFOLIO_VERSION,
        initial_capital_krw=float(portfolio.initial_capital_krw),
        sleeve_weights_bps={
            Sleeve.US_STOCK: weights.us_stock,
            Sleeve.KR_STOCK: weights.kr_stock,
            Sleeve.US_ETF: weights.us_etf,
            Sleeve.KR_ETF: weights.kr_etf,
        },
        entry_score=float(signal.entry_score),
        exit_score=float(signal.exit_score),
        peer_entry_scores={
            group: float(values.entry_score) for group, values in signal.peer_overrides.items()
        },
        peer_exit_scores={
            group: float(values.exit_score) for group, values in signal.peer_overrides.items()
        },
        peer_group_slots=slot_map,
        review_frequency=execution.review_frequency,
        execution_delay_sessions=execution.execution_delay_sessions,
        position_sizing=portfolio.position_sizing,
        replacement_policy=portfolio.replacement_policy,
        replacement_score_gap=float(portfolio.replacement_score_gap),
        market_gate_mode=signal.market_gate_mode,
        fixed_stop_loss=strategy.risk.fixed_stop_loss,
        trailing_stop_loss=strategy.risk.trailing_stop_loss,
        initial_fx_cost=execution.initial_fx_cost,
        us_buy_cost=execution.us_buy_cost,
        us_sell_cost=execution.us_sell_cost,
        kr_buy_cost=execution.kr_buy_cost,
        kr_sell_cost=execution.kr_sell_cost,
        slippage_bps=execution.slippage_bps,
    )
    return score_config, portfolio_config


def default_period(manifest: dict[str, Any]) -> ReplayDataConfig:
    raw_start = date.fromisoformat(str(manifest["history_start"]))
    end = date.fromisoformat(str(manifest["requested_end"])) - timedelta(days=1)
    start = raw_start + timedelta(days=380)
    split = start + timedelta(days=round((end - start).days * 0.60))
    return ReplayDataConfig(start_date=start, split_date=split, end_date=end)


def default_success_criteria(objective: ExperimentObjective) -> ReplaySuccessCriteria:
    if objective is ExperimentObjective.RETURN:
        return ReplaySuccessCriteria(
            minimum_cagr_improvement_pp=0.5,
            maximum_mdd_degradation_pp=2.0,
        )
    if objective is ExperimentObjective.DRAWDOWN:
        return ReplaySuccessCriteria(
            minimum_mdd_improvement_pp=2.0,
            maximum_cagr_degradation_pp=1.0,
        )
    if objective is ExperimentObjective.COST:
        return ReplaySuccessCriteria(
            minimum_cost_reduction_ratio=0.10,
            maximum_cagr_degradation_pp=1.0,
        )
    return ReplaySuccessCriteria(
        minimum_sharpe_improvement=0.10,
        maximum_cagr_degradation_pp=0.5,
        maximum_mdd_degradation_pp=1.0,
    )


def success_assessment(
    *,
    objective: ExperimentObjective,
    criteria: ReplaySuccessCriteria,
    baseline: dict[str, float],
    candidate: dict[str, float],
    baseline_cost: float,
    candidate_cost: float,
) -> dict[str, Any]:
    cagr_change_pp = (candidate.get("cagr", 0) - baseline.get("cagr", 0)) * 100
    mdd_improvement_pp = (
        abs(baseline.get("max_drawdown", 0)) - abs(candidate.get("max_drawdown", 0))
    ) * 100
    sharpe_change = candidate.get("sharpe", 0) - baseline.get("sharpe", 0)
    cost_reduction = (baseline_cost - candidate_cost) / baseline_cost if baseline_cost > 0 else 0.0
    checks: list[dict[str, Any]] = []
    if objective is ExperimentObjective.RETURN:
        checks = [
            {
                "label": "검증 CAGR 개선",
                "passed": cagr_change_pp >= criteria.minimum_cagr_improvement_pp,
            },
            {
                "label": "낙폭 악화 제한",
                "passed": mdd_improvement_pp >= -criteria.maximum_mdd_degradation_pp,
            },
        ]
    elif objective is ExperimentObjective.DRAWDOWN:
        checks = [
            {
                "label": "최대 낙폭 개선",
                "passed": mdd_improvement_pp >= criteria.minimum_mdd_improvement_pp,
            },
            {
                "label": "CAGR 감소 제한",
                "passed": cagr_change_pp >= -criteria.maximum_cagr_degradation_pp,
            },
        ]
    elif objective is ExperimentObjective.COST:
        checks = [
            {
                "label": "비용 감소",
                "passed": cost_reduction >= criteria.minimum_cost_reduction_ratio,
            },
            {
                "label": "CAGR 감소 제한",
                "passed": cagr_change_pp >= -criteria.maximum_cagr_degradation_pp,
            },
        ]
    else:
        checks = [
            {
                "label": "Sharpe 개선",
                "passed": sharpe_change >= criteria.minimum_sharpe_improvement,
            },
            {
                "label": "CAGR 감소 제한",
                "passed": cagr_change_pp >= -criteria.maximum_cagr_degradation_pp,
            },
            {
                "label": "낙폭 악화 제한",
                "passed": mdd_improvement_pp >= -criteria.maximum_mdd_degradation_pp,
            },
        ]
    return {
        "passed": all(bool(check["passed"]) for check in checks),
        "cagr_change_pp": round(cagr_change_pp, 3),
        "mdd_improvement_pp": round(mdd_improvement_pp, 3),
        "sharpe_change": round(sharpe_change, 3),
        "cost_reduction_ratio": round(cost_reduction, 4),
        "checks": checks,
    }
