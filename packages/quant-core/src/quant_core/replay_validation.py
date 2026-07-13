import math
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

import numpy as np

from quant_core.config import PortfolioConfig
from quant_core.market_portfolio import (
    MarketReplayRun,
    PreparedMarketReplay,
    simulate_prepared_replay,
    slice_prepared_replay,
)

REPLAY_VALIDATION_VERSION = "replay-validation-v1.0.0"


def period_metrics(
    values: list[float] | np.ndarray,
    dates: list[date],
    *,
    start_index: int,
    end_index: int,
    initial_capital: float,
) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    period = array[start_index:end_index]
    if len(period) < 2:
        raise ValueError("성과 구간에는 두 개 이상의 평가일이 필요합니다.")
    initial = initial_capital if start_index == 0 else float(array[start_index - 1])
    start_date = dates[0] if start_index == 0 else dates[start_index - 1]
    extended = np.concatenate(([initial], period))
    returns = np.diff(extended) / extended[:-1]
    years = max((dates[end_index - 1] - start_date).days / 365.25, 1 / 365.25)
    standard_deviation = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    running_max = np.maximum.accumulate(extended)[1:]
    drawdown = period / running_max - 1
    return {
        "start_value_krw": round(initial, 2),
        "end_value_krw": round(float(period[-1]), 2),
        "total_return": round(float(period[-1] / initial - 1), 6),
        "cagr": round(float((period[-1] / initial) ** (1 / years) - 1), 6),
        "max_drawdown": round(float(np.min(drawdown)), 6),
        "annual_volatility": round(standard_deviation * math.sqrt(252), 6),
        "sharpe": round(
            float(np.mean(returns) / standard_deviation * math.sqrt(252))
            if standard_deviation > 0
            else 0.0,
            4,
        ),
    }


def _run_summary(run: MarketReplayRun) -> dict[str, Any]:
    return {
        "started_on": run.result.started_on.isoformat(),
        "ended_on": run.result.ended_on.isoformat(),
        "metrics": dict(run.result.metrics),
        "trade_count": len(run.result.trades),
    }


def build_validation(
    prepared: PreparedMarketReplay,
    actual_run: MarketReplayRun,
    *,
    split_date: date,
    data_version: str,
    score_version: str,
    portfolio_config: PortfolioConfig,
) -> tuple[dict[str, Any], MarketReplayRun]:
    split_index = next(
        (index for index, current in enumerate(prepared.dates) if current >= split_date),
        None,
    )
    if split_index is None or split_index < 2 or len(prepared.dates) - split_index < 2:
        raise ValueError("학습·검증 구간을 나눌 수 있는 평가일이 부족합니다.")
    independent_prepared = slice_prepared_replay(
        prepared,
        start=prepared.dates[split_index],
        end=prepared.dates[-1],
    )
    independent = simulate_prepared_replay(
        independent_prepared,
        data_version=data_version,
        score_version=score_version,
        portfolio_config=portfolio_config,
        run_id=f"{actual_run.result.run_id}-validation",
        prices_are_split_adjusted=True,
    )
    payload = {
        "version": REPLAY_VALIDATION_VERSION,
        "requested_split_date": split_date.isoformat(),
        "resolved_split_date": prepared.dates[split_index].isoformat(),
        "training": period_metrics(
            actual_run.equity_values,
            prepared.dates,
            start_index=0,
            end_index=split_index,
            initial_capital=portfolio_config.initial_capital_krw,
        ),
        "continuous_validation": period_metrics(
            actual_run.equity_values,
            prepared.dates,
            start_index=split_index,
            end_index=len(prepared.dates),
            initial_capital=portfolio_config.initial_capital_krw,
        ),
        "independent_validation": _run_summary(independent),
    }
    return payload, independent


def _add_years(current: date, years: int) -> date:
    try:
        return current.replace(year=current.year + years)
    except ValueError:
        return current.replace(month=2, day=28, year=current.year + years)


def build_walk_forward(
    prepared: PreparedMarketReplay,
    *,
    data_version: str,
    score_version: str,
    portfolio_config: PortfolioConfig,
    train_years: int,
    test_years: int,
    step_years: int = 1,
) -> dict[str, Any]:
    windows: list[dict[str, Any]] = []
    cursor = prepared.dates[0]
    final_date = prepared.dates[-1]
    while True:
        test_start = _add_years(cursor, train_years)
        test_end = min(_add_years(test_start, test_years) - timedelta(days=1), final_date)
        if test_start >= final_date:
            break
        try:
            test_prepared = slice_prepared_replay(
                prepared,
                start=test_start,
                end=test_end,
            )
        except ValueError:
            break
        test_run = simulate_prepared_replay(
            test_prepared,
            data_version=data_version,
            score_version=score_version,
            portfolio_config=portfolio_config,
            run_id=f"walk-forward-{test_start.isoformat()}",
            prices_are_split_adjusted=True,
        )
        windows.append(
            {
                "train_start": cursor.isoformat(),
                "train_end": (test_start - timedelta(days=1)).isoformat(),
                "test_start": test_prepared.dates[0].isoformat(),
                "test_end": test_prepared.dates[-1].isoformat(),
                "metrics": dict(test_run.result.metrics),
            }
        )
        cursor = _add_years(cursor, step_years)
    cagrs = [float(window["metrics"]["cagr"]) for window in windows]
    drawdowns = [float(window["metrics"]["max_drawdown"]) for window in windows]
    return {
        "train_years": train_years,
        "test_years": test_years,
        "step_years": step_years,
        "windows": windows,
        "summary": {
            "window_count": len(windows),
            "median_cagr": round(float(np.median(cagrs)), 6) if cagrs else None,
            "worst_cagr": round(min(cagrs), 6) if cagrs else None,
            "worst_max_drawdown": round(min(drawdowns), 6) if drawdowns else None,
            "positive_window_rate": (
                round(sum(value > 0 for value in cagrs) / len(cagrs), 6) if cagrs else None
            ),
        },
    }


def build_stress_tests(
    prepared: PreparedMarketReplay,
    actual_run: MarketReplayRun,
    *,
    data_version: str,
    score_version: str,
    portfolio_config: PortfolioConfig,
) -> dict[str, Any]:
    doubled_costs = replace(
        portfolio_config,
        initial_fx_cost=portfolio_config.initial_fx_cost * 2,
        us_buy_cost=portfolio_config.trade_cost("USD", "BUY") * 2,
        us_sell_cost=portfolio_config.trade_cost("USD", "SELL") * 2,
        kr_buy_cost=portfolio_config.trade_cost("KRW", "BUY") * 2,
        kr_sell_cost=portfolio_config.trade_cost("KRW", "SELL") * 2,
    )
    cost_run = simulate_prepared_replay(
        prepared,
        data_version=data_version,
        score_version=score_version,
        portfolio_config=doubled_costs,
        run_id=f"{actual_run.result.run_id}-cost-stress",
        prices_are_split_adjusted=True,
    )
    delayed = replace(
        portfolio_config,
        execution_delay_sessions=min(5, portfolio_config.execution_delay_sessions + 1),
    )
    delayed_run = simulate_prepared_replay(
        prepared,
        data_version=data_version,
        score_version=score_version,
        portfolio_config=delayed,
        run_id=f"{actual_run.result.run_id}-delay-stress",
        prices_are_split_adjusted=True,
    )
    profits = sorted(
        (float(item.net_pnl_krw) for item in actual_run.round_trips if item.net_pnl_krw > 0),
        reverse=True,
    )
    final_value = float(actual_run.equity_values[-1])
    initial = portfolio_config.initial_capital_krw
    return {
        "costs_x2": _run_summary(cost_run),
        "execution_delay_plus_one": _run_summary(delayed_run),
        "winner_concentration": {
            "top_1_pnl_krw": round(sum(profits[:1]), 0),
            "top_3_pnl_krw": round(sum(profits[:3]), 0),
            "return_without_top_1": round((final_value - sum(profits[:1])) / initial - 1, 6),
            "return_without_top_3": round((final_value - sum(profits[:3])) / initial - 1, 6),
            "method": "TRADE_PNL_SUBTRACTION",
        },
    }
