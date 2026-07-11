import math
from datetime import date

import numpy as np


def calculate_metrics(
    dates: list[date],
    equity: np.ndarray,
    benchmark: np.ndarray,
    *,
    total_trade_notional_krw: float,
    average_exposure: float,
    trade_count: int,
    initial_equity: float | None = None,
) -> tuple[dict[str, float], np.ndarray]:
    if len(dates) < 2 or len(equity) != len(dates):
        raise ValueError("성과 계산에는 두 개 이상의 동일 길이 관측치가 필요합니다.")
    performance_start = float(initial_equity) if initial_equity is not None else float(equity[0])
    if performance_start <= 0:
        raise ValueError("성과 계산의 초기 자금은 0보다 커야 합니다.")
    return_values = (
        np.concatenate((np.asarray([performance_start]), equity))
        if initial_equity is not None
        else equity
    )
    returns = np.diff(return_values) / return_values[:-1]
    years = max((dates[-1] - dates[0]).days / 365.25, 1 / 365.25)
    cagr = (equity[-1] / performance_start) ** (1 / years) - 1
    volatility = float(np.std(returns, ddof=1) * math.sqrt(252)) if len(returns) > 1 else 0.0
    mean_return = float(np.mean(returns)) if len(returns) else 0.0
    std_return = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = mean_return / std_return * math.sqrt(252) if std_return > 0 else 0.0
    downside = returns[returns < 0]
    downside_deviation = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sortino = mean_return / downside_deviation * math.sqrt(252) if downside_deviation > 0 else 0.0
    running_max = np.maximum.accumulate(
        np.concatenate((np.asarray([performance_start]), equity))
    )[1:]
    drawdown = equity / running_max - 1
    max_drawdown = float(np.min(drawdown))
    calmar = cagr / abs(max_drawdown) if max_drawdown < 0 else 0.0
    benchmark_cagr = (benchmark[-1] / benchmark[0]) ** (1 / years) - 1
    average_equity = float(np.mean(equity))
    metrics = {
        "cagr": round(float(cagr), 6),
        "annual_volatility": round(volatility, 6),
        "sharpe": round(float(sharpe), 4),
        "sortino": round(float(sortino), 4),
        "max_drawdown": round(max_drawdown, 6),
        "calmar": round(float(calmar), 4),
        "turnover": round(total_trade_notional_krw / average_equity, 4),
        "trade_count": float(trade_count),
        "average_exposure": round(average_exposure, 6),
        "final_value_krw": round(float(equity[-1]), 0),
        "total_return": round(float(equity[-1] / performance_start - 1), 6),
        "benchmark_cagr": round(float(benchmark_cagr), 6),
        "benchmark_total_return": round(float(benchmark[-1] / benchmark[0] - 1), 6),
    }
    return metrics, drawdown
