from datetime import date

import numpy as np
import pytest
from quant_core.config import PortfolioConfig
from quant_core.enums import PeerGroup, Sleeve
from quant_core.market_portfolio import MarketReplayRun, PreparedMarketReplay, RoundTrip
from quant_core.models import BacktestResult
from quant_core.replay_validation import (
    _add_years,
    build_stress_tests,
    build_validation,
    build_walk_forward,
    period_metrics,
)


def _result(
    run_id: str = "actual",
    *,
    dates: list[date] | None = None,
    metrics: dict[str, float] | None = None,
) -> BacktestResult:
    dates = dates or [date(2025, 1, 2), date(2025, 1, 3)]
    return BacktestResult(
        run_id=run_id,
        data_version="data-v1",
        score_version="score-v2",
        portfolio_version="portfolio-v2",
        config_hash="fixture",
        started_on=dates[0],
        ended_on=dates[-1],
        metrics=metrics or {"cagr": 0.0, "max_drawdown": 0.0},
        equity_curve=[],
        drawdown_curve=[],
        trades=[],
        final_positions=[],
    )


def _prepared(dates: list[date] | None = None) -> PreparedMarketReplay:
    dates = dates or [date(2025, 1, 2), date(2025, 1, 3)]
    empty = np.empty((len(dates), 0))
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
        benchmark_indexes={sleeve: np.ones(len(dates)) for sleeve in Sleeve},
    )


def _run(
    run_id: str = "actual",
    *,
    dates: list[date] | None = None,
    equity_values: list[float] | None = None,
    metrics: dict[str, float] | None = None,
    round_trips: list[RoundTrip] | None = None,
) -> MarketReplayRun:
    dates = dates or [date(2025, 1, 2), date(2025, 1, 3)]
    equity_values = equity_values or [50_000_000.0] * len(dates)
    return MarketReplayRun(
        result=_result(run_id, dates=dates, metrics=metrics),
        daily_ledger=[],
        review_ledger=[],
        round_trips=round_trips or [],
        position_counts=[],
        equity_values=equity_values,
        benchmark_values=[50_000_000.0] * len(dates),
    )


def _round_trip(index: int, net_pnl_krw: float) -> RoundTrip:
    return RoundTrip(
        asset_id=f"US_STOCK:{index}",
        symbol=f"S{index}",
        name=f"Asset {index}",
        peer_group=PeerGroup.US_STOCK,
        sleeve=Sleeve.US_STOCK,
        currency="USD",
        status="CLOSED",
        entry_date=date(2025, 1, 2),
        exit_date=date(2025, 1, 3),
        entry_score=75.0,
        exit_score=60.0,
        quantity=1,
        entry_price=100.0,
        exit_price=110.0,
        entry_notional_krw=1_000.0,
        exit_value_krw=1_000.0 + net_pnl_krw,
        dividends_krw=0.0,
        costs_krw=0.0,
        net_pnl_krw=net_pnl_krw,
        net_return=net_pnl_krw / 1_000.0,
        holding_days=1,
        exit_reason="EXIT_RULE",
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


def test_period_metrics_uses_initial_capital_for_first_period() -> None:
    metrics = period_metrics(
        [105.0, 110.0],
        [date(2025, 1, 2), date(2025, 1, 3)],
        start_index=0,
        end_index=2,
        initial_capital=100.0,
    )

    assert metrics["start_value_krw"] == 100.0
    assert metrics["end_value_krw"] == 110.0
    assert metrics["total_return"] == pytest.approx(0.1)
    assert metrics["max_drawdown"] == 0.0


def test_period_metrics_rejects_period_with_fewer_than_two_evaluation_days() -> None:
    with pytest.raises(ValueError, match="두 개 이상의 평가일"):
        period_metrics(
            [100.0, 101.0],
            [date(2025, 1, 2), date(2025, 1, 3)],
            start_index=1,
            end_index=2,
            initial_capital=100.0,
        )


def test_build_validation_resolves_split_and_runs_independent_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = [date(2025, 1, day) for day in range(2, 7)]
    prepared = _prepared(dates)
    actual = _run(
        dates=dates,
        equity_values=[100.0, 110.0, 121.0, 115.0, 126.0],
    )
    calls: list[tuple[list[date], str]] = []

    def fake_simulate(
        independent: PreparedMarketReplay,
        *,
        data_version: str,
        score_version: str,
        portfolio_config: PortfolioConfig,
        run_id: str,
        prices_are_split_adjusted: bool,
    ) -> MarketReplayRun:
        assert data_version == "data-v1"
        assert score_version == "score-v2"
        assert portfolio_config.initial_capital_krw == 100.0
        assert prices_are_split_adjusted is True
        calls.append((independent.dates, run_id))
        return _run(
            run_id,
            dates=independent.dates,
            equity_values=[100.0, 105.0, 110.0],
            metrics={"cagr": 0.20, "max_drawdown": -0.03},
        )

    monkeypatch.setattr("quant_core.replay_validation.simulate_prepared_replay", fake_simulate)

    payload, independent = build_validation(
        prepared,
        actual,
        split_date=date(2025, 1, 4),
        data_version="data-v1",
        score_version="score-v2",
        portfolio_config=PortfolioConfig(initial_capital_krw=100.0),
    )

    assert calls == [(dates[2:], "actual-validation")]
    assert independent.result.run_id == "actual-validation"
    assert payload["requested_split_date"] == "2025-01-04"
    assert payload["resolved_split_date"] == "2025-01-04"
    assert payload["training"]["total_return"] == pytest.approx(0.1)
    assert payload["continuous_validation"]["start_value_krw"] == 110.0
    assert payload["independent_validation"] == {
        "started_on": "2025-01-04",
        "ended_on": "2025-01-06",
        "metrics": {"cagr": 0.20, "max_drawdown": -0.03},
        "trade_count": 0,
    }


@pytest.mark.parametrize(
    "split_date",
    [date(2025, 1, 1), date(2025, 1, 6), date(2025, 1, 7)],
)
def test_build_validation_rejects_unsplittable_period(split_date: date) -> None:
    dates = [date(2025, 1, day) for day in range(2, 7)]

    with pytest.raises(ValueError, match="나눌 수 있는 평가일이 부족"):
        build_validation(
            _prepared(dates),
            _run(dates=dates, equity_values=[100.0] * len(dates)),
            split_date=split_date,
            data_version="data-v1",
            score_version="score-v2",
            portfolio_config=PortfolioConfig(initial_capital_krw=100.0),
        )


def test_add_years_normalizes_leap_day_only_when_target_year_is_not_leap() -> None:
    assert _add_years(date(2020, 2, 29), 1) == date(2021, 2, 28)
    assert _add_years(date(2020, 2, 29), 4) == date(2024, 2, 29)


def test_walk_forward_builds_windows_and_aggregate_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    final_date = date(2023, 7, 1)
    dates = [
        current
        for year in range(2020, 2024)
        for month in range(1, 13)
        if (current := date(year, month, 1)) <= final_date
    ]
    metric_rows = [
        {"cagr": 0.10, "max_drawdown": -0.10},
        {"cagr": -0.05, "max_drawdown": -0.20},
        {"cagr": 0.20, "max_drawdown": -0.15},
    ]
    run_ids: list[str] = []

    def fake_simulate(
        window: PreparedMarketReplay,
        *,
        data_version: str,
        score_version: str,
        portfolio_config: PortfolioConfig,
        run_id: str,
        prices_are_split_adjusted: bool,
    ) -> MarketReplayRun:
        assert data_version == "data-v1"
        assert score_version == "score-v2"
        assert portfolio_config.initial_capital_krw == 100.0
        assert prices_are_split_adjusted is True
        metrics = metric_rows[len(run_ids)]
        run_ids.append(run_id)
        return _run(run_id, dates=window.dates, metrics=metrics)

    monkeypatch.setattr("quant_core.replay_validation.simulate_prepared_replay", fake_simulate)

    payload = build_walk_forward(
        _prepared(dates),
        data_version="data-v1",
        score_version="score-v2",
        portfolio_config=PortfolioConfig(initial_capital_krw=100.0),
        train_years=1,
        test_years=1,
        step_years=1,
    )

    assert run_ids == [
        "walk-forward-2021-01-01",
        "walk-forward-2022-01-01",
        "walk-forward-2023-01-01",
    ]
    assert payload["windows"][0] == {
        "train_start": "2020-01-01",
        "train_end": "2020-12-31",
        "test_start": "2021-01-01",
        "test_end": "2021-12-01",
        "metrics": metric_rows[0],
    }
    assert payload["windows"][-1]["test_end"] == "2023-07-01"
    assert payload["summary"] == {
        "window_count": 3,
        "median_cagr": 0.10,
        "worst_cagr": -0.05,
        "worst_max_drawdown": -0.20,
        "positive_window_rate": 0.666667,
    }


def test_walk_forward_returns_empty_summary_when_test_window_cannot_be_sliced() -> None:
    payload = build_walk_forward(
        _prepared([date(2020, 1, 1), date(2021, 6, 1), date(2023, 1, 1)]),
        data_version="data-v1",
        score_version="score-v2",
        portfolio_config=PortfolioConfig(),
        train_years=1,
        test_years=1,
        step_years=1,
    )

    assert payload["windows"] == []
    assert payload["summary"] == {
        "window_count": 0,
        "median_cagr": None,
        "worst_cagr": None,
        "worst_max_drawdown": None,
        "positive_window_rate": None,
    }


def test_cost_stress_doubles_every_configured_cost_exactly(monkeypatch: pytest.MonkeyPatch) -> None:
    actual = _run()
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


def test_stress_tests_cap_delay_and_measure_profitable_trade_concentration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = PortfolioConfig(initial_capital_krw=1_000.0, execution_delay_sessions=5)
    actual = _run(
        equity_values=[1_000.0, 1_100.0],
        round_trips=[
            _round_trip(1, 100.0),
            _round_trip(2, 60.0),
            _round_trip(3, 40.0),
            _round_trip(4, 20.0),
            _round_trip(5, -30.0),
        ],
    )
    captured: list[PortfolioConfig] = []

    def fake_simulate(
        *args: object, portfolio_config: PortfolioConfig, **kwargs: object
    ) -> MarketReplayRun:
        del args, kwargs
        captured.append(portfolio_config)
        return _run(f"stress-{len(captured)}")

    monkeypatch.setattr("quant_core.replay_validation.simulate_prepared_replay", fake_simulate)

    payload = build_stress_tests(
        _prepared(),
        actual,
        data_version="data-v1",
        score_version="score-v2",
        portfolio_config=config,
    )

    assert captured[1].execution_delay_sessions == 5
    assert payload["winner_concentration"] == {
        "top_1_pnl_krw": 100.0,
        "top_3_pnl_krw": 200.0,
        "return_without_top_1": 0.0,
        "return_without_top_3": -0.1,
        "method": "TRADE_PNL_SUBTRACTION",
    }
