from pathlib import Path

from quant_api.research_scoring import ResearchScorer, _official_state
from quant_api.research_store import ResearchSnapshotStore
from quant_core.config import TrendScoreConfig
from quant_core.enums import CandidateState, DataStatus, PeerGroup

from .test_research_pipeline import FakePriceFetcher, _builder, _universe


def test_candidate_hysteresis_uses_60_for_existing_candidates() -> None:
    row = {
        "candidate_state": CandidateState.WATCH.value,
        "candidate_eligible": True,
        "trend_score": 62.0,
    }
    assert _official_state(row, was_candidate=True) == CandidateState.CANDIDATE.value
    assert _official_state(row, was_candidate=False) == CandidateState.WATCH.value


def test_scorer_writes_latest_status_for_every_peer_group(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    build = _builder(root, FakePriceFetcher()).build("score", universe)
    scorer = ResearchScorer(
        store=ResearchSnapshotStore(root),
        config=TrendScoreConfig(minimum_peer_count=1),
    )
    manifest = scorer.score(build, universe)
    store = ResearchSnapshotStore(root)
    final = store.activate(build.staging_path, manifest)
    latest = store.latest_scores(final)

    assert latest.height == len(PeerGroup)
    assert set(latest.get_column("data_status")) == {DataStatus.READY.value}
    assert (final / "scores" / "history.parquet").is_file()
    assert manifest["latest_score_rows"] == len(PeerGroup)
