from datetime import date

import numpy as np
from quant_core.metrics import calculate_metrics


def test_metrics_use_initial_capital_before_initial_cost() -> None:
    dates = [date(2025, 1, 3), date(2026, 1, 3)]
    equity = np.asarray([49_900_000.0, 55_000_000.0])
    benchmark = np.asarray([50_000_000.0, 55_000_000.0])

    metrics, drawdown = calculate_metrics(
        dates,
        equity,
        benchmark,
        total_trade_notional_krw=0,
        average_exposure=0,
        trade_count=0,
        initial_equity=50_000_000.0,
    )

    assert metrics["total_return"] == 0.1
    assert np.isclose(drawdown[0], -0.002)


def test_metrics_keep_legacy_first_observation_baseline_when_initial_is_omitted() -> None:
    dates = [date(2025, 1, 3), date(2026, 1, 3)]
    equity = np.asarray([49_900_000.0, 55_000_000.0])

    metrics, drawdown = calculate_metrics(
        dates,
        equity,
        equity,
        total_trade_notional_krw=0,
        average_exposure=0,
        trade_count=0,
    )

    assert metrics["total_return"] == round(55_000_000 / 49_900_000 - 1, 6)
    assert drawdown[0] == 0
