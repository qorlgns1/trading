from datetime import date
from pathlib import Path

import polars as pl
import pytest
from quant_api.research_quality import QualityGateError, ResearchQualityValidator
from quant_api.universe import UniverseSnapshot
from quant_core.enums import CandidateState, DataStatus, PeerGroup
from quant_core.providers import DEFAULT_BENCHMARK, UniverseAsset


def _universe(tmp_path: Path, assets_per_group: int = 1) -> UniverseSnapshot:
    assets = [
        UniverseAsset(
            ticker=f"{group.value[:3]}{index:03d}",
            name=f"{group.value} {index}",
            peer_group=group,
            currency=(
                "USD"
                if group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF}
                else "KRW"
            ),
            benchmark_ticker=DEFAULT_BENCHMARK[group],
        )
        for group in PeerGroup
        for index in range(assets_per_group)
    ]
    rows = [
        {
            "asset_id": f"{asset.peer_group.value}:{asset.ticker}",
            "ticker": asset.ticker,
            "symbol": asset.ticker,
            "name": asset.name,
            "peer_group": asset.peer_group.value,
            "currency": asset.currency,
            "benchmark_ticker": asset.benchmark_ticker,
            "is_supported": asset.is_supported,
            "data_status": asset.data_status.value,
        }
        for asset in assets
    ]
    path = tmp_path / "universe.csv"
    pl.DataFrame(rows).write_csv(path)
    manifest_path = tmp_path / "universe-manifest.json"
    manifest_path.write_text("{}\n", encoding="utf-8")
    return UniverseSnapshot(
        version="universe-quality-test",
        path=path,
        manifest_path=manifest_path,
        assets=assets,
        sources={"fixture": True},
        counts={group.value: assets_per_group for group in PeerGroup},
    )


def _asset_id(asset: UniverseAsset) -> str:
    return f"{asset.peer_group.value}:{asset.ticker}"


def _bars(universe: UniverseSnapshot) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "date": date(2026, 7, 9),
                "asset_id": _asset_id(asset),
                "symbol": asset.ticker,
                "name": asset.name,
                "peer_group": asset.peer_group.value,
                "currency": asset.currency,
                "open": 99.0,
                "close": 100.0,
                "adjusted_close": 100.0,
                "volume": 1_000_000.0,
                "dividend": 0.0,
                "split_ratio": 1.0,
                "benchmark_close": 100.0,
                "fx_krw_per_usd": 1_370.0,
                "provider_repaired": False,
            }
            for asset in universe.assets
        ]
    )


def _write_bars(staging: Path, bars: pl.DataFrame) -> None:
    path = staging / "bars" / "peer_group=fixture" / "year=2026" / "bars.parquet"
    path.parent.mkdir(parents=True)
    bars.write_parquet(path)


def _manifest(universe: UniverseSnapshot) -> dict[str, object]:
    return {
        "data_version": "yf-quality-test",
        "universe_version": universe.version,
        "history_start": "2016-07-10",
        "requested_end": "2026-07-10",
    }


def _latest(
    universe: UniverseSnapshot, invalid_asset_ids: set[str] | None = None
) -> pl.DataFrame:
    invalid_asset_ids = invalid_asset_ids or set()
    rows = []
    for asset in universe.assets:
        asset_id = _asset_id(asset)
        invalid = asset_id in invalid_asset_ids
        rows.append(
            {
                "date": date(2026, 7, 9),
                "asset_id": asset_id,
                "symbol": asset.ticker,
                "name": asset.name,
                "peer_group": asset.peer_group.value,
                "trend_score": None if invalid else 100.0,
                "candidate_state": (
                    CandidateState.NOT_AVAILABLE.value
                    if invalid
                    else CandidateState.STRONG_CANDIDATE.value
                ),
                "candidate_eligible": not invalid,
                "official_candidate": not invalid,
                "data_status": (
                    DataStatus.INVALID_DATA.value if invalid else DataStatus.READY.value
                ),
                "status_reason": "데이터 품질 검사 실패" if invalid else None,
                "relative_strength_rank": None if invalid else 0.5,
                "long_term_trend_score": None if invalid else 20.0,
                "absolute_momentum_score": None if invalid else 20.0,
                "relative_strength_score": None if invalid else 15.0,
                "high_proximity_score": None if invalid else 15.0,
                "volatility_score": None if invalid else 15.0,
                "activity_score": None if invalid else 15.0,
            }
        )
    return pl.DataFrame(rows)


def test_valid_snapshot_passes_all_quality_checks(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    staging = tmp_path / "staging"
    _write_bars(staging, _bars(universe))
    validator = ResearchQualityValidator(tmp_path / "research", minimum_group_assets=1)

    raw = validator.inspect_raw(staging, universe, _manifest(universe))
    report, issues = validator.finalize(
        raw,
        initial_repair_ids=set(),
        latest=_latest(universe),
        score_history_path=staging / "scores" / "history.parquet",
        universe=universe,
        manifest=_manifest(universe),
        previous_manifest=None,
    )

    assert raw.repair_asset_ids == set()
    assert issues == []
    assert report["status"] == "PASS"
    validator.ensure_passable(report)


def test_bad_asset_is_refetched_then_quarantined_without_blocking_group(
    tmp_path: Path,
) -> None:
    universe = _universe(tmp_path, assets_per_group=2)
    invalid_id = _asset_id(universe.assets[0])
    bars = _bars(universe).with_columns(
        pl.when(pl.col("asset_id") == invalid_id)
        .then(-1.0)
        .otherwise(pl.col("adjusted_close"))
        .alias("adjusted_close")
    )
    staging = tmp_path / "staging"
    _write_bars(staging, bars)
    validator = ResearchQualityValidator(tmp_path / "research", minimum_group_assets=1)

    raw = validator.inspect_raw(staging, universe, _manifest(universe))
    report, issues = validator.finalize(
        raw,
        initial_repair_ids={invalid_id},
        latest=_latest(universe, {invalid_id}),
        score_history_path=staging / "scores" / "history.parquet",
        universe=universe,
        manifest=_manifest(universe),
        previous_manifest=None,
    )

    assert raw.repair_asset_ids == {invalid_id}
    assert raw.quarantine_reasons()[invalid_id].startswith("데이터 품질 검사 실패")
    assert any(issue["resolution"] == "QUARANTINED" for issue in issues)
    assert report["status"] == "WARN"
    assert report["totals"]["quarantined_assets"] == 1
    validator.ensure_passable(report)


def test_duplicate_asset_date_blocks_the_entire_snapshot(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    bars = _bars(universe)
    staging = tmp_path / "staging"
    _write_bars(staging, pl.concat([bars, bars.head(1)]))
    validator = ResearchQualityValidator(tmp_path / "research", minimum_group_assets=1)

    raw = validator.inspect_raw(staging, universe, _manifest(universe))
    report, _ = validator.blocked_report(raw, _manifest(universe))

    assert any(issue["check_id"] == "UNIQUE_ASSET_DATE" for issue in raw.blockers)
    assert report["status"] == "FAIL"
    with pytest.raises(QualityGateError, match="종목·날짜 중복"):
        validator.ensure_passable(report)


def test_quarantine_limit_blocks_snapshot_activation(tmp_path: Path) -> None:
    universe = _universe(tmp_path, assets_per_group=6)
    invalid_ids = {
        _asset_id(asset)
        for asset in universe.assets
        if asset.peer_group is PeerGroup.US_STOCK
    }
    bars = _bars(universe).with_columns(
        pl.when(pl.col("asset_id").is_in(invalid_ids))
        .then(-1.0)
        .otherwise(pl.col("adjusted_close"))
        .alias("adjusted_close")
    )
    staging = tmp_path / "staging"
    _write_bars(staging, bars)
    validator = ResearchQualityValidator(tmp_path / "research", minimum_group_assets=0)
    raw = validator.inspect_raw(staging, universe, _manifest(universe))

    report, issues = validator.finalize(
        raw,
        initial_repair_ids=invalid_ids,
        latest=_latest(universe, invalid_ids),
        score_history_path=staging / "scores" / "history.parquet",
        universe=universe,
        manifest=_manifest(universe),
        previous_manifest=None,
    )

    assert any(issue["check_id"] == "QUARANTINE_LIMIT" for issue in issues)
    assert report["status"] == "FAIL"


def test_twenty_point_coverage_drop_blocks_snapshot_activation(tmp_path: Path) -> None:
    universe = _universe(tmp_path, assets_per_group=4)
    invalid_id = next(
        _asset_id(asset)
        for asset in universe.assets
        if asset.peer_group is PeerGroup.US_STOCK
    )
    staging = tmp_path / "staging"
    _write_bars(staging, _bars(universe))
    validator = ResearchQualityValidator(tmp_path / "research", minimum_group_assets=0)
    raw = validator.inspect_raw(staging, universe, _manifest(universe))
    previous_manifest = {
        "quality": {
            "groups": [
                {"peer_group": group.value, "ready_rate": 1.0} for group in PeerGroup
            ]
        }
    }

    report, issues = validator.finalize(
        raw,
        initial_repair_ids=set(),
        latest=_latest(universe, {invalid_id}),
        score_history_path=staging / "scores" / "history.parquet",
        universe=universe,
        manifest=_manifest(universe),
        previous_manifest=previous_manifest,
    )

    assert any(
        issue["check_id"] == "COVERAGE_REGRESSION"
        and issue["resolution"] == "BLOCKED"
        for issue in issues
    )
    assert report["status"] == "FAIL"
