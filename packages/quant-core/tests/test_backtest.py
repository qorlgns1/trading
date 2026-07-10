from functools import lru_cache

from quant_core.backtest import run_reference_backtest
from quant_core.scoring import score_trends
from quant_core.synthetic import DEMO_DATA_VERSION, generate_demo_market


@lru_cache(maxsize=1)
def _result():  # type: ignore[no-untyped-def]
    bars = generate_demo_market()
    scores = score_trends(bars, data_version=DEMO_DATA_VERSION)
    return run_reference_backtest(
        bars,
        data_version=DEMO_DATA_VERSION,
        scored_bars=scores,
    )


def test_backtest_obeys_position_and_integer_share_constraints() -> None:
    result = _result()
    assert len(result.final_positions) <= 12
    assert all(position.quantity == int(position.quantity) for position in result.final_positions)
    assert all(position.score > 0 for position in result.final_positions)
    assert all(trade.quantity >= 1 for trade in result.trades)
    assert result.metrics["trade_count"] == len(result.trades)


def test_signals_are_not_filled_on_same_weekly_close() -> None:
    result = _result()
    assert result.trades
    # The synthetic calendar is Monday-Friday; weekly signals are generated on Friday.
    entries = [trade for trade in result.trades if trade.reason == "WEEKLY_ENTRY"]
    assert all(trade.date.weekday() != 4 for trade in entries)


def test_result_contains_reproducibility_and_risk_metrics() -> None:
    result = _result()
    assert result.data_version == DEMO_DATA_VERSION
    assert len(result.config_hash) == 16
    assert result.metrics["final_value_krw"] > 0
    assert result.metrics["max_drawdown"] <= 0
    assert len(result.equity_curve) > 2500
    assert result.warnings
