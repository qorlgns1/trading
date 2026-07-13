from datetime import date

import numpy as np
import pytest
from quant_core.config import PortfolioConfig
from quant_core.enums import Sleeve
from quant_core.market_portfolio import MarketReplayRun, PreparedMarketReplay
from quant_core.models import BacktestResult
from quant_core.replay_validation import build_stress_tests, period_metrics


def _result(run_id: str = "actual") -> BacktestResult:
    dates = [date(2025, 1, 2), date(2025, 1, 3)]
    return BacktestResult(
        run_id=run_id,
        data_version="data-v1",
        score_version="score-v2",
        portfolio_version="portfolio-v2",
        config_hash="fixture",
        started_on=dates[0],
        ended_on=dates[-1],
        metrics={"cagr": 0.0, "max_drawdown": 0.0},
        equity_curve=[],
        drawdown_curve=[],
        trades=[],
        final_positions=[],
    )


def _prepared() -> PreparedMarketReplay:
    dates = [date(2025, 1, 2), date(2025, 1, 3)]
    empty = np.empty((2, 0))
    return PreparedMarketReplay(
        signals_by_review={},
        review_dates=[],
        dates=dates,
        candidate_ids=[],
        metadata={},
        asset_index={},
        open_prices=empty,
        close_prices=empty,
        split_ratios=empty,
        dividends=empty,
        recovery_values=empty,
        fx_by_date={current: 1_350.0 for current in dates},
        benchmark_indexes={sleeve: np.ones(2) for sleeve in Sleeve},
    )


def test_period_metrics_uses_previous_value_at_validation_boundary() -> None:
    metrics = period_metrics(
        [100.0, 110.0, 99.0, 108.9],
        [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6), date(2025, 1, 7)],
        start_index=2,
        end_index=4,
        initial_capital=100.0,
    )

    assert metrics["start_value_krw"] == 110.0
    assert metrics["total_return"] == pytest.approx(-0.01)
    assert metrics["max_drawdown"] == pytest.approx(-0.1)


def test_cost_stress_doubles_every_configured_cost_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    actual = MarketReplayRun(
        result=_result(),
        daily_ledger=[],
        review_ledger=[],
        round_trips=[],
        position_counts=[],
        equity_values=[50_000_000.0, 50_000_000.0],
        benchmark_values=[50_000_000.0, 50_000_000.0],
    )
    captured: list[PortfolioConfig] = []

    def fake_simulate(
        *args: object, portfolio_config: PortfolioConfig, **kwargs: object
    ) -> MarketReplayRun:
        del args, kwargs
        captured.append(portfolio_config)
        return MarketReplayRun(
            result=_result(f"stress-{len(captured)}"),
            daily_ledger=[],
            review_ledger=[],
            round_trips=[],
            position_counts=[],
            equity_values=[50_000_000.0, 50_000_000.0],
            benchmark_values=[50_000_000.0, 50_000_000.0],
        )

    monkeypatch.setattr("quant_core.replay_validation.simulate_prepared_replay", fake_simulate)
    config = PortfolioConfig(
        initial_fx_cost=0.01,
        us_buy_cost=0.01,
        us_sell_cost=0.008,
        kr_buy_cost=0.006,
        kr_sell_cost=0.004,
    )

    build_stress_tests(
        _prepared(),
        actual,
        data_version="data-v1",
        score_version="score-v2",
        portfolio_config=config,
    )

    doubled = captured[0]
    assert doubled.initial_fx_cost == 0.02
    assert doubled.us_buy_cost == 0.02
    assert doubled.us_sell_cost == 0.016
    assert doubled.kr_buy_cost == 0.012
    assert doubled.kr_sell_cost == 0.008
    assert captured[1].execution_delay_sessions == 2
