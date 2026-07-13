import polars as pl
from quant_core.config import TrendScoreConfig
from quant_core.enums import CandidateState, PeerGroup
from quant_core.scoring import (
    compute_trend_features,
    explain_result,
    project_trend_scores,
    score_trends,
    warning_codes,
)
from quant_core.synthetic import DEMO_DATA_VERSION, generate_demo_market


def _latest_scores() -> pl.DataFrame:
    scores = score_trends(generate_demo_market(), data_version=DEMO_DATA_VERSION)
    latest = scores.get_column("date").max()
    return scores.filter(pl.col("date") == latest)


def test_components_add_up_and_ranks_stay_inside_peer_group() -> None:
    latest = _latest_scores()
    component_sum = pl.sum_horizontal(
        "long_term_trend_score",
        "absolute_momentum_score",
        "relative_strength_score",
        "high_proximity_score",
        "volatility_score",
        "activity_score",
    ).round(1)
    mismatch = latest.filter(component_sum != pl.col("trend_score"))
    assert mismatch.is_empty()
    assert latest.group_by("peer_group").len().get_column("len").to_list() == [40] * 6
    assert latest.get_column("relative_strength_rank").drop_nulls().min() >= 0
    assert latest.get_column("relative_strength_rank").drop_nulls().max() <= 1


def test_unsupported_and_low_liquidity_assets_are_excluded() -> None:
    latest = _latest_scores()
    unsupported = latest.filter(pl.col("symbol").str.ends_with("040"))
    illiquid = latest.filter(pl.col("symbol").str.ends_with("039"))
    assert set(unsupported.get_column("candidate_state")) == {CandidateState.NOT_AVAILABLE.value}
    assert set(illiquid.get_column("candidate_state")) == {CandidateState.EXCLUDED.value}


def test_explanations_are_deterministic_and_do_not_change_score() -> None:
    row = (
        _latest_scores()
        .filter((pl.col("peer_group") == PeerGroup.US_STOCK.value) & pl.col("candidate_eligible"))
        .sort("trend_score", descending=True)
        .row(0, named=True)
    )
    before = row["trend_score"]
    first = explain_result(row)
    second = explain_result(row)
    assert first == second
    assert len(first["reasons"]) <= 3
    assert len(first["warnings"]) <= 2
    assert warning_codes(row)
    assert row["trend_score"] == before


def test_fixed_features_can_be_reprojected_with_strategy_weights() -> None:
    features = compute_trend_features(generate_demo_market())
    config = TrendScoreConfig(
        component_weights_bps={
            "long_term_trend": 10_000,
            "absolute_momentum": 0,
            "relative_strength": 0,
            "high_proximity": 0,
            "volatility_stability": 0,
            "trading_activity": 0,
        },
        require_above_sma200=False,
        require_positive_six_month=False,
        require_absolute_liquidity=False,
        require_order_size_liquidity=False,
    )

    projected = project_trend_scores(features, config)
    latest = projected.filter(pl.col("date") == projected.get_column("date").max())

    assert latest.filter(pl.col("absolute_momentum_score") != 0).is_empty()
    assert latest.filter(pl.col("relative_strength_score") != 0).is_empty()
    assert latest.filter(
        pl.col("trend_score") != (pl.col("long_term_trend_unit") * 100).round(1)
    ).is_empty()
