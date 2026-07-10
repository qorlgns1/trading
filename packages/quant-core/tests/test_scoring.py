import polars as pl
from quant_core.enums import CandidateState, PeerGroup
from quant_core.scoring import explain_result, score_trends, warning_codes
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
        .filter(
            (pl.col("peer_group") == PeerGroup.US_STOCK.value)
            & pl.col("candidate_eligible")
        )
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
