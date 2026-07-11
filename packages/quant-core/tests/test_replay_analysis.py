from datetime import date

import numpy as np
from quant_core.enums import PeerGroup, Sleeve
from quant_core.market_portfolio import (
    DailyLedgerRow,
    MarketReplayRun,
    PreparedMarketReplay,
    RoundTrip,
)
from quant_core.models import BacktestResult
from quant_core.replay_analysis import ReplayInvariantError, analyze_replay

DATES = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]


def _prepared() -> PreparedMarketReplay:
    empty = np.empty((len(DATES), 0))
    indexes = np.asarray([1.0, 1.1, 1.21])
    return PreparedMarketReplay(
        signals_by_review={},
        review_dates=[],
        dates=DATES,
        candidate_ids=[],
        metadata={},
        asset_index={},
        open_prices=empty,
        close_prices=empty,
        split_ratios=empty,
        dividends=empty,
        recovery_values=empty,
        fx_by_date={current: 1_350.0 for current in DATES},
        benchmark_indexes={sleeve: indexes for sleeve in Sleeve},
    )


def _result(values: list[float], benchmark: list[float]) -> BacktestResult:
    return BacktestResult(
        run_id="fixture",
        data_version="data-v1",
        score_version="score-v1",
        portfolio_version="portfolio-v1",
        config_hash="fixture",
        started_on=DATES[0],
        ended_on=DATES[-1],
        metrics={"average_exposure": 0.25},
        equity_curve=[
            {"date": current.isoformat(), "portfolio": values[index], "benchmark": benchmark[index]}
            for index, current in enumerate(DATES)
        ],
        drawdown_curve=[],
        trades=[],
        final_positions=[],
    )


def _run(
    values: list[float],
    *,
    exposures: dict[Sleeve, float],
    negative_cash: bool = False,
) -> MarketReplayRun:
    benchmark = [50_000_000.0, 55_000_000.0, 60_500_000.0]
    daily: list[DailyLedgerRow] = []
    for index, current in enumerate(DATES):
        remaining = values[index]
        for sleeve_index, sleeve in enumerate(Sleeve):
            sleeve_equity = (
                remaining if sleeve_index == len(Sleeve) - 1 else values[index] / len(Sleeve)
            )
            remaining -= sleeve_equity
            exposure = exposures[sleeve]
            position = sleeve_equity * exposure
            cash = sleeve_equity - position
            if negative_cash and index == 1 and sleeve is Sleeve.US_STOCK:
                cash = -1.0
                position = sleeve_equity + 1.0
            daily.append(
                DailyLedgerRow(
                    date=current,
                    sleeve=sleeve,
                    cash_krw=cash,
                    position_value_krw=position,
                    equity_krw=sleeve_equity,
                    exposure=exposure,
                    positions_count=int(exposure > 0),
                    transaction_cost_krw=0.0,
                    dividend_krw=0.0,
                    fx_krw_per_usd=1_350.0,
                )
            )
    counts = [
        {
            "date": current,
            "total": 0,
            **{group.value: 0 for group in PeerGroup},
        }
        for current in DATES
    ]
    return MarketReplayRun(
        result=_result(values, benchmark),
        daily_ledger=daily,
        review_ledger=[],
        round_trips=[],
        position_counts=counts,
        equity_values=values,
        benchmark_values=benchmark,
    )


def test_exposure_matched_benchmark_and_gap_bridge_reconcile() -> None:
    actual = _run(
        [50_000_000.0, 52_000_000.0, 55_000_000.0],
        exposures={sleeve: 1.0 for sleeve in Sleeve},
    )
    no_cost = _run(
        [50_000_000.0, 52_500_000.0, 56_000_000.0],
        exposures={sleeve: 1.0 for sleeve in Sleeve},
    )

    build = analyze_replay(_prepared(), actual, no_cost)

    assert np.allclose(build.exposure_matched_curve, actual.benchmark_values)
    gap = build.analysis["gap_analysis"]
    assert abs(gap["reconciliation_error"]) <= 0.0001
    assert sum(row["pnl_krw"] for row in build.analysis["sleeve_attribution"]) == 5_000_000


def test_zero_exposure_keeps_exposure_matched_benchmark_flat() -> None:
    actual = _run(
        [50_000_000.0, 50_000_000.0, 50_000_000.0],
        exposures={sleeve: 0.0 for sleeve in Sleeve},
    )

    build = analyze_replay(_prepared(), actual, actual)

    assert build.exposure_matched_curve == [50_000_000.0] * len(DATES)


def test_negative_cash_fails_replay_integrity() -> None:
    actual = _run(
        [50_000_000.0, 52_000_000.0, 55_000_000.0],
        exposures={sleeve: 0.5 for sleeve in Sleeve},
        negative_cash=True,
    )

    try:
        analyze_replay(_prepared(), actual, actual)
    except ReplayInvariantError as error:
        assert "음수 현금" in str(error)
    else:
        raise AssertionError("음수 현금은 무결성 검사를 실패해야 합니다.")


def test_trade_quality_includes_fx_dividend_and_cost_adjusted_round_trip() -> None:
    actual = _run(
        [50_000_000.0, 52_000_000.0, 55_000_000.0],
        exposures={sleeve: 0.5 for sleeve in Sleeve},
    )
    actual.round_trips.append(
        RoundTrip(
            asset_id="US_STOCK:A",
            symbol="A",
            name="Asset A",
            peer_group=PeerGroup.US_STOCK,
            sleeve=Sleeve.US_STOCK,
            currency="USD",
            status="CLOSED",
            entry_date=DATES[0],
            exit_date=DATES[-1],
            entry_score=75.0,
            exit_score=59.0,
            quantity=10,
            entry_price=100.0,
            exit_price=110.0,
            entry_notional_krw=1_300_000.0,
            exit_value_krw=1_480_000.0,
            dividends_krw=10_000.0,
            costs_krw=5_000.0,
            net_pnl_krw=185_000.0,
            net_return=0.1423,
            holding_days=4,
            exit_reason="EXIT_RULE",
        )
    )

    build = analyze_replay(_prepared(), actual, actual)
    trade = build.analysis["trade_analysis"]

    assert trade["overall"]["win_rate"] == 1.0
    assert trade["overall"]["net_pnl_krw"] == 185_000
    assert trade["by_entry_score"][1]["band"] == "70-79"
