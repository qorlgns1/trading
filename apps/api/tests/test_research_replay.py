import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polars as pl
import pytest
from quant_api import research_replay as replay
from quant_api.research_replay import (
    REPLAY_FEATURE_COLUMNS,
    PartitionedReplayScorer,
    ReplaySignalContext,
    ResearchReplayEngine,
    _completed_schedule,
    _project_scheduled_features,
    _scheduled_features,
)
from quant_api.research_store import ResearchSnapshotStore
from quant_core.config import PortfolioConfig, TrendScoreConfig
from quant_core.enums import DataStatus, PeerGroup, ReviewFrequency, UniverseMode
from quant_core.models import BacktestResult
from quant_core.replay_analysis import ReplayAnalysisBuild


def _feature_row(current: date, asset_id: str, group: PeerGroup) -> dict[str, Any]:
    return {
        "date": current,
        "asset_id": asset_id,
        "symbol": asset_id,
        "name": asset_id,
        "peer_group": group.value,
        "currency": "USD" if group.value.startswith("US_") else "KRW",
        "adjusted_close": 120.0,
        "sma200": 100.0,
        "r126": 0.2,
        "relative_momentum": 0.1,
        "vol60": 0.2,
        "adv60": 20_000_000.0,
        "data_eligible": True,
        "peer_count": 30,
        "benchmark_close": 120.0,
        "benchmark_sma200": 100.0,
        "long_term_trend_unit": 1.0,
        "absolute_momentum_unit": 1.0,
        "relative_strength_unit": 1.0,
        "high_proximity_unit": 1.0,
        "volatility_stability_unit": 1.0,
        "trading_activity_unit": 1.0,
    }


def _write_features(
    root: Path,
    group: PeerGroup,
    year: int,
    rows: list[dict[str, Any]],
    *,
    filename: str = "scores.parquet",
) -> Path:
    output = root / f"peer_group={group.value}" / f"year={year}" / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).select(REPLAY_FEATURE_COLUMNS).write_parquet(output)
    return output


def _write_schedule_dates(root: Path, group: PeerGroup, dates: list[date]) -> Path:
    output = root / f"peer_group={group.value}" / "year=2024" / "scores.parquet"
    output.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"date": dates}).write_parquet(output)
    return output


def _backtest_run(run_id: str, dates: list[date]) -> SimpleNamespace:
    values = [50_000_000.0 + index for index in range(len(dates))]
    result = BacktestResult(
        run_id=run_id,
        data_version="fixture-v1",
        score_version="fixture-score",
        portfolio_version="fixture-portfolio",
        config_hash="fixture-hash",
        started_on=dates[0],
        ended_on=dates[-1],
        metrics={"total_return": 0.01},
        equity_curve=[
            {
                "date": current.isoformat(),
                "portfolio": values[index],
                "benchmark": values[index],
            }
            for index, current in enumerate(dates)
        ],
        drawdown_curve=[],
        trades=[],
        final_positions=[],
        warnings=[
            "현재 상장 종목 기준 과거 재생으로 생존편향이 포함됩니다.",
            "기본 경고",
        ],
    )
    return SimpleNamespace(
        result=result,
        equity_values=values,
        benchmark_values=values,
        daily_ledger=[],
        review_ledger=[],
        round_trips=[],
        position_counts=[],
    )


def _stub_run_dependencies(
    engine: ResearchReplayEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    dates: list[date],
    *,
    repaired_assets: int = 0,
) -> list[dict[str, Any]]:
    context = ReplaySignalContext(
        snapshot=tmp_path,
        manifest={"quality": {"totals": {"repaired_assets": repaired_assets}}},
        score_root=tmp_path,
        cache_key="fixture-cache",
        cache_hit=True,
        features_by_group={},
    )
    monkeypatch.setattr(engine, "signal_context", lambda **_kwargs: context)
    monkeypatch.setattr(
        engine,
        "project_context",
        lambda *_args, **_kwargs: (
            pl.DataFrame(
                {
                    "asset_id": ["US_STOCK:A"],
                    "review_date": [dates[0]],
                }
            ),
            pl.DataFrame({"asset_id": ["US_STOCK:A"]}),
        ),
    )
    prepared = SimpleNamespace(dates=dates)
    monkeypatch.setattr(engine, "prepare_market", lambda **_kwargs: prepared)
    calls: list[dict[str, Any]] = []

    def fake_simulate(_prepared: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        callback = kwargs.get("progress")
        if callback is not None:
            callback(len(dates), len(dates))
        return _backtest_run(str(kwargs["run_id"]), dates)

    monkeypatch.setattr(replay, "simulate_prepared_replay", fake_simulate)
    return calls


def test_partitioned_scorer_builds_cache_then_reuses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(parents=True)
    pl.DataFrame(
        {
            "asset_id": ["US_STOCK:GOOD", "US_STOCK:INVALID", "US_STOCK:UNSUPPORTED"],
            "is_supported": [True, True, False],
        }
    ).write_csv(snapshot / "universe.csv")
    latest = snapshot / "scores" / "latest.parquet"
    latest.parent.mkdir()
    pl.DataFrame(
        {
            "asset_id": ["US_STOCK:GOOD", "US_STOCK:INVALID"],
            "data_status": [DataStatus.READY.value, DataStatus.INVALID_DATA.value],
        }
    ).write_parquet(latest)
    for year, current in ((2023, date(2023, 12, 29)), (2024, date(2024, 1, 2))):
        rows = [
            _feature_row(current, "US_STOCK:GOOD", PeerGroup.US_STOCK),
            _feature_row(current, "US_STOCK:INVALID", PeerGroup.US_STOCK),
        ]
        _write_features(
            snapshot / "bars",
            PeerGroup.US_STOCK,
            year,
            rows,
            filename="bars.parquet",
        )

    scored_heights: list[int] = []

    def fake_compute(frame: pl.DataFrame) -> pl.DataFrame:
        scored_heights.append(frame.height)
        return frame

    monkeypatch.setattr(replay, "compute_trend_features", fake_compute)
    manifest = {"data_version": "fixture-v1", "bars_sha256": "abc123"}
    progress: list[tuple[str, int, int]] = []
    scorer = PartitionedReplayScorer(tmp_path / "research")

    score_root, cache_key, cache_hit = scorer.build(
        snapshot,
        manifest,
        score_config=TrendScoreConfig(),
        progress=lambda stage, completed, total: progress.append((stage, completed, total)),
    )

    assert cache_hit is False
    assert score_root.name == cache_key
    assert scored_heights == [1, 2]
    assert progress == [
        ("SCORE_PARTITIONS", 1, 2),
        ("SCORE_PARTITIONS", 2, 2),
    ]
    assert [
        pl.read_parquet(path).get_column("asset_id").unique().to_list()
        for path in sorted(score_root.rglob("scores.parquet"))
    ] == [["US_STOCK:GOOD"], ["US_STOCK:GOOD"]]
    cache_manifest = json.loads((score_root / "manifest.json").read_text(encoding="utf-8"))
    assert cache_manifest["complete"] is True
    assert cache_manifest["excluded_invalid_assets"] == ["US_STOCK:INVALID"]

    scored_heights.clear()
    reused_root, reused_key, reused = scorer.build(
        snapshot,
        manifest,
        score_config=TrendScoreConfig(),
    )

    assert (reused_root, reused_key, reused) == (score_root, cache_key, True)
    assert scored_heights == []


def test_daily_schedule_uses_every_available_signal_date(tmp_path: Path) -> None:
    us_dates = [date(2024, 1, 4), date(2024, 1, 5)]
    kr_dates = [date(2024, 1, 5)]
    _write_schedule_dates(tmp_path, PeerGroup.US_STOCK, us_dates)
    _write_schedule_dates(tmp_path, PeerGroup.KR_KOSPI, kr_dates)

    schedule = _completed_schedule(
        tmp_path,
        frequency=ReviewFrequency.DAILY,
        enabled_groups={PeerGroup.US_STOCK, PeerGroup.KR_KOSPI},
    )

    assert schedule.to_dicts() == [
        {
            "peer_group": PeerGroup.US_STOCK.value,
            "signal_date": date(2024, 1, 4),
            "review_date": date(2024, 1, 4),
        },
        {
            "peer_group": PeerGroup.KR_KOSPI.value,
            "signal_date": date(2024, 1, 5),
            "review_date": date(2024, 1, 5),
        },
        {
            "peer_group": PeerGroup.US_STOCK.value,
            "signal_date": date(2024, 1, 5),
            "review_date": date(2024, 1, 5),
        },
    ]


@pytest.mark.parametrize(
    ("frequency", "us_date", "kr_date", "next_date"),
    [
        (
            ReviewFrequency.WEEKLY,
            date(2024, 1, 5),
            date(2024, 1, 4),
            date(2024, 1, 8),
        ),
        (
            ReviewFrequency.MONTHLY,
            date(2024, 1, 31),
            date(2024, 1, 30),
            date(2024, 2, 1),
        ),
    ],
)
def test_completed_market_schedule_aligns_us_and_kr_periods(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frequency: ReviewFrequency,
    us_date: date,
    kr_date: date,
    next_date: date,
) -> None:
    _write_schedule_dates(tmp_path, PeerGroup.US_STOCK, [us_date])
    _write_schedule_dates(tmp_path, PeerGroup.KR_KOSPI, [kr_date])
    monkeypatch.setattr(
        "quant_core.calendar.next_trading_date",
        lambda _current, _market: next_date,
    )

    schedule = _completed_schedule(
        tmp_path,
        frequency=frequency,
        enabled_groups={PeerGroup.US_STOCK, PeerGroup.KR_KOSPI},
    )

    assert schedule.get_column("review_date").unique().to_list() == [us_date]
    assert dict(
        zip(
            schedule.get_column("peer_group"),
            schedule.get_column("signal_date"),
            strict=True,
        )
    ) == {
        PeerGroup.KR_KOSPI.value: kr_date,
        PeerGroup.US_STOCK.value: us_date,
    }


@pytest.mark.parametrize(
    "frequency",
    [ReviewFrequency.DAILY, ReviewFrequency.WEEKLY, ReviewFrequency.MONTHLY],
)
def test_schedule_rejects_an_empty_group_set(tmp_path: Path, frequency: ReviewFrequency) -> None:
    message = "완결된 일별 신호가 없습니다" if frequency is ReviewFrequency.DAILY else "완결된 시장"
    with pytest.raises(RuntimeError, match=message):
        _completed_schedule(tmp_path, frequency=frequency, enabled_groups=set())


def test_scheduled_features_validates_point_in_time_membership(tmp_path: Path) -> None:
    group = PeerGroup.US_STOCK
    signal_date = date(2024, 1, 5)
    _write_features(tmp_path, group, 2024, [_feature_row(signal_date, "US_STOCK:A", group)])
    schedule = pl.DataFrame(
        {
            "peer_group": [group.value],
            "signal_date": [signal_date],
            "review_date": [signal_date],
        }
    )

    with pytest.raises(RuntimeError, match="필수 열"):
        _scheduled_features(
            tmp_path,
            schedule,
            enabled_groups={group},
            point_in_time_membership=pl.DataFrame({"asset_id": ["US_STOCK:A"]}),
        )

    overlapping = pl.DataFrame(
        {
            "asset_id": ["US_STOCK:A", "US_STOCK:A"],
            "valid_from": [date(2020, 1, 1), date(2023, 1, 1)],
            "valid_to": [None, date(2025, 1, 1)],
        }
    )
    with pytest.raises(RuntimeError, match="서로 겹칩니다"):
        _scheduled_features(
            tmp_path,
            schedule,
            enabled_groups={group},
            point_in_time_membership=overlapping,
        )

    inactive = pl.DataFrame(
        {
            "asset_id": ["US_STOCK:A"],
            "valid_from": [date(2020, 1, 1)],
            "valid_to": [date(2023, 12, 31)],
        }
    )
    with pytest.raises(RuntimeError, match="시점 기준 종목군 평가 신호"):
        _scheduled_features(
            tmp_path,
            schedule,
            enabled_groups={group},
            point_in_time_membership=inactive,
        )

    active = overlapping.head(1)
    features = _scheduled_features(
        tmp_path,
        schedule,
        enabled_groups={group},
        point_in_time_membership=active,
    )
    assert features[group].get_column("asset_id").to_list() == ["US_STOCK:A"]
    assert features[group].get_column("review_date").to_list() == [signal_date]


def test_scheduled_features_applies_requested_date_window(tmp_path: Path) -> None:
    group = PeerGroup.US_STOCK
    first = date(2024, 1, 5)
    second = date(2024, 1, 12)
    _write_features(
        tmp_path,
        group,
        2024,
        [_feature_row(first, "US_STOCK:A", group), _feature_row(second, "US_STOCK:A", group)],
    )
    schedule = pl.DataFrame(
        {
            "peer_group": [group.value, group.value],
            "signal_date": [first, second],
            "review_date": [first, second],
        }
    )

    features = _scheduled_features(
        tmp_path,
        schedule,
        enabled_groups={group},
        start=second,
        end=second,
    )

    assert features[group].get_column("date").to_list() == [second]

    with pytest.raises(RuntimeError, match="평가 신호가 없습니다"):
        _scheduled_features(
            tmp_path,
            schedule,
            enabled_groups={group},
            start=date(2025, 1, 1),
        )


def test_point_in_time_manifest_and_file_are_required(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(parents=True)

    assert (
        ResearchReplayEngine._point_in_time_membership(
            snapshot,
            {},
            UniverseMode.CURRENT_LISTED,
        )
        is None
    )
    with pytest.raises(RuntimeError, match="지원하지 않습니다"):
        ResearchReplayEngine._point_in_time_membership(
            snapshot,
            {},
            UniverseMode.POINT_IN_TIME,
        )
    with pytest.raises(RuntimeError, match="경로가 manifest에 없습니다"):
        ResearchReplayEngine._point_in_time_membership(
            snapshot,
            {"supports_point_in_time": True},
            UniverseMode.POINT_IN_TIME,
        )
    manifest = {
        "supports_point_in_time": True,
        "point_in_time_membership_path": "membership.parquet",
    }
    with pytest.raises(RuntimeError, match="파일을 찾을 수 없습니다"):
        ResearchReplayEngine._point_in_time_membership(
            snapshot,
            manifest,
            UniverseMode.POINT_IN_TIME,
        )

    pl.DataFrame(
        {
            "asset_id": ["US_STOCK:A"],
            "valid_from": [date(2020, 1, 1)],
            "valid_to": [None],
        }
    ).write_parquet(snapshot / "membership.parquet")
    membership = ResearchReplayEngine._point_in_time_membership(
        snapshot,
        manifest,
        UniverseMode.POINT_IN_TIME,
    )
    assert membership is not None
    assert membership.get_column("asset_id").to_list() == ["US_STOCK:A"]


def test_signal_context_coordinates_cache_schedule_and_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchSnapshotStore(tmp_path / "research")
    snapshot = store.snapshots_root / "fixture-v1"
    snapshot.mkdir(parents=True)
    pl.DataFrame(
        {
            "asset_id": ["US_STOCK:A"],
            "valid_from": [date(2020, 1, 1)],
            "valid_to": [None],
        }
    ).write_parquet(snapshot / "membership.parquet")
    manifest = {
        "data_version": "fixture-v1",
        "supports_point_in_time": True,
        "point_in_time_membership_path": "membership.parquet",
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    score_root = tmp_path / "score-root"
    score_root.mkdir()
    schedule = pl.DataFrame(
        {
            "peer_group": [PeerGroup.US_STOCK.value],
            "signal_date": [date(2024, 1, 5)],
            "review_date": [date(2024, 1, 5)],
        }
    )
    expected_features = {PeerGroup.US_STOCK: pl.DataFrame({"asset_id": ["US_STOCK:A"]})}
    calls: dict[str, Any] = {}
    engine = ResearchReplayEngine(store)

    def fake_build(*args: Any, **kwargs: Any) -> tuple[Path, str, bool]:
        calls["build"] = (args, kwargs)
        return score_root, "cache-key", True

    def fake_schedule(*args: Any, **kwargs: Any) -> pl.DataFrame:
        calls["schedule"] = (args, kwargs)
        return schedule

    def fake_features(*args: Any, **kwargs: Any) -> dict[PeerGroup, pl.DataFrame]:
        calls["features"] = (args, kwargs)
        return expected_features

    monkeypatch.setattr(engine.scorer, "build", fake_build)
    monkeypatch.setattr(replay, "_completed_schedule", fake_schedule)
    monkeypatch.setattr(replay, "_scheduled_features", fake_features)

    def progress(_stage: str, _completed: int, _total: int) -> None:
        return None

    context = engine.signal_context(
        data_version="fixture-v1",
        score_config=TrendScoreConfig(),
        frequency=ReviewFrequency.WEEKLY,
        enabled_groups={PeerGroup.US_STOCK},
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        universe_mode=UniverseMode.POINT_IN_TIME,
        progress=progress,
    )

    assert context.cache_key == "cache-key"
    assert context.cache_hit is True
    assert context.features_by_group is expected_features
    assert calls["build"][0][0] == snapshot
    assert calls["build"][1]["progress"] is progress
    assert calls["schedule"][1] == {
        "frequency": ReviewFrequency.WEEKLY,
        "enabled_groups": {PeerGroup.US_STOCK},
    }
    feature_kwargs = calls["features"][1]
    assert feature_kwargs["start"] == date(2024, 1, 1)
    assert feature_kwargs["end"] == date(2024, 12, 31)
    assert feature_kwargs["point_in_time_membership"].height == 1


def test_project_context_delegates_to_feature_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = (pl.DataFrame({"signal": [1]}), pl.DataFrame({"metadata": [1]}))
    captured: dict[str, Any] = {}

    def fake_project(*args: Any, **kwargs: Any) -> tuple[pl.DataFrame, pl.DataFrame]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return marker

    monkeypatch.setattr(replay, "_project_scheduled_features", fake_project)
    context = ReplaySignalContext(
        snapshot=tmp_path,
        manifest={},
        score_root=tmp_path,
        cache_key="key",
        cache_hit=False,
        features_by_group={PeerGroup.US_STOCK: pl.DataFrame({"asset_id": ["A"]})},
    )
    config = PortfolioConfig()
    score_config = TrendScoreConfig()

    result = ResearchReplayEngine.project_context(
        context,
        score_config=score_config,
        portfolio_config=config,
        enabled_groups={PeerGroup.US_STOCK},
    )

    assert result is marker
    assert captured["args"] == (context.features_by_group,)
    assert captured["kwargs"] == {
        "score_config": score_config,
        "portfolio_config": config,
        "enabled_groups": {PeerGroup.US_STOCK},
    }


def test_feature_projection_rejects_missing_unready_and_ineligible_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    group = PeerGroup.US_STOCK
    with pytest.raises(RuntimeError, match="평가 신호가 없습니다"):
        _project_scheduled_features(
            {},
            score_config=TrendScoreConfig(),
            portfolio_config=PortfolioConfig(),
            enabled_groups={group},
        )

    unready = pl.DataFrame(
        {
            "asset_id": ["US_STOCK:A"],
            "review_date": [date(2024, 1, 5)],
            "data_eligible": [False],
            "benchmark_sma200": [None],
        }
    )
    with pytest.raises(RuntimeError, match="최소 종목 수와 벤치마크가 부족"):
        _project_scheduled_features(
            {group: unready},
            score_config=TrendScoreConfig(),
            portfolio_config=PortfolioConfig(),
            enabled_groups={group},
        )

    ready = pl.DataFrame(
        {
            "asset_id": [f"US_STOCK:{index:02d}" for index in range(30)],
            "review_date": [date(2024, 1, 5)] * 30,
            "data_eligible": [True] * 30,
            "benchmark_sma200": [100.0] * 30,
        }
    )
    monkeypatch.setattr(
        replay,
        "project_trend_scores",
        lambda _features, _config: pl.DataFrame(
            {
                "date": [date(2024, 1, 5)],
                "review_date": [date(2024, 1, 5)],
                "asset_id": ["US_STOCK:A"],
                "symbol": ["A"],
                "name": ["A"],
                "peer_group": [group.value],
                "currency": ["USD"],
                "trend_score": [64.0],
                "relative_momentum": [0.1],
                "vol60": [0.2],
                "data_eligible": [True],
                "candidate_eligible": [True],
                "benchmark_close": [120.0],
                "benchmark_sma200": [100.0],
            }
        ),
    )
    with pytest.raises(RuntimeError, match="진입 가능한 후보가 없습니다"):
        _project_scheduled_features(
            {group: ready},
            score_config=TrendScoreConfig(),
            portfolio_config=PortfolioConfig(),
            enabled_groups={group},
        )


def test_prepare_market_loads_candidates_and_group_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ResearchSnapshotStore(tmp_path / "research")
    snapshot = store.snapshots_root / "fixture-v1"
    snapshot.mkdir(parents=True)
    (snapshot / "manifest.json").write_text("{}", encoding="utf-8")
    score_root = tmp_path / "scores"
    score_path = score_root / "peer_group=US_STOCK" / "year=2024" / "scores.parquet"
    score_path.parent.mkdir(parents=True)
    pl.DataFrame({"asset_id": ["REFERENCE", "REFERENCE", "OTHER"]}).write_parquet(score_path)
    first_review = date(2024, 1, 5)
    end_date = date(2024, 1, 8)
    source = pl.DataFrame(
        {
            "date": [first_review, end_date, first_review, first_review],
            "asset_id": ["CANDIDATE", "CANDIDATE", "REFERENCE", "OTHER"],
            "peer_group": [PeerGroup.US_STOCK.value] * 4,
            "open": [100.0, 101.0, 90.0, 80.0],
            "close": [101.0, 102.0, 91.0, 81.0],
            "split_ratio": [1.0] * 4,
            "dividend": [0.0] * 4,
            "recovery_value": [None] * 4,
            "benchmark_close": [110.0, 111.0, 110.0, 110.0],
            "fx_krw_per_usd": [1_350.0] * 4,
        }
    )
    scan_starts: list[date | None] = []

    def fake_scan_bars(
        *, snapshot_path: Path, start: date | None = None, **_kwargs: Any
    ) -> pl.LazyFrame:
        assert snapshot_path == snapshot
        scan_starts.append(start)
        return source.lazy()

    monkeypatch.setattr(store, "scan_bars", fake_scan_bars)
    captured: dict[str, Any] = {}
    sentinel = SimpleNamespace(dates=[first_review, end_date])

    def fake_prepare(
        bars: pl.DataFrame,
        signals: pl.DataFrame,
        reference: pl.DataFrame,
        **kwargs: Any,
    ) -> SimpleNamespace:
        captured.update(
            bars=bars,
            signals=signals,
            reference=reference,
            kwargs=kwargs,
        )
        return sentinel

    monkeypatch.setattr(replay, "prepare_market_replay", fake_prepare)
    engine = ResearchReplayEngine(store)
    signals = pl.DataFrame(
        {
            "review_date": [first_review],
            "asset_id": ["CANDIDATE"],
        }
    )
    metadata = pl.DataFrame({"asset_id": ["CANDIDATE"]})
    config = PortfolioConfig()

    result = engine.prepare_market(
        data_version="fixture-v1",
        score_root=score_root,
        signals=signals,
        metadata=metadata,
        portfolio_config=config,
        end_date=end_date,
    )

    assert result is sentinel
    assert scan_starts == [first_review, first_review]
    assert captured["bars"].get_column("asset_id").unique().to_list() == ["CANDIDATE"]
    assert captured["reference"].get_column("peer_group").to_list() == [PeerGroup.US_STOCK.value]
    assert captured["signals"] is signals
    assert captured["kwargs"] == {
        "portfolio_config": config,
        "asset_metadata": metadata,
    }

    with pytest.raises(RuntimeError, match="시작 기준일"):
        engine.prepare_market(
            data_version="fixture-v1",
            score_root=score_root,
            signals=pl.DataFrame({"review_date": ["invalid"], "asset_id": ["CANDIDATE"]}),
            metadata=metadata,
            portfolio_config=config,
            end_date=None,
        )


def test_run_without_diagnostics_reuses_actual_run_and_records_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = ResearchReplayEngine(ResearchSnapshotStore(tmp_path / "research"))
    dates = [date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 9)]
    simulation_calls = _stub_run_dependencies(
        engine,
        monkeypatch,
        tmp_path,
        dates,
        repaired_assets=2,
    )
    progress: list[tuple[str, int, int]] = []

    build = engine.run(
        "run-no-diagnostics",
        data_version="fixture-v1",
        portfolio_config=PortfolioConfig(),
        include_diagnostics=False,
        progress=lambda stage, completed, total: progress.append((stage, completed, total)),
    )

    assert len(simulation_calls) == 1
    assert build.actual_run is build.no_cost_run
    assert build.analysis.analysis == {}
    assert build.analysis.exposure_matched_curve == build.actual_run.benchmark_values
    assert build.cache_hit is True
    assert any("복구 이력이 있는 종목 2개" in warning for warning in build.result.warnings)
    assert any("분할 수량을 중복 적용하지 않습니다" in warning for warning in build.result.warnings)
    assert any("현재 상장 종목 기준" in warning for warning in build.result.warnings)
    assert ("BUILD_SIGNALS", 0, 1) in progress
    assert ("BUILD_SIGNALS", 1, 1) in progress
    assert ("LOAD_MARKET_EVENTS", 0, 1) in progress
    assert ("PREPARE_MARKET", 1, 1) in progress
    assert ("SIMULATE_ACTUAL", len(dates), len(dates)) in progress


def test_run_with_diagnostics_builds_validation_and_point_in_time_warnings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = ResearchReplayEngine(ResearchSnapshotStore(tmp_path / "research"))
    start = date(2022, 1, 1)
    dates = [start + timedelta(days=index) for index in range(504)]
    simulation_calls = _stub_run_dependencies(
        engine,
        monkeypatch,
        tmp_path,
        dates,
        repaired_assets=1,
    )
    analysis_calls: list[tuple[Any, Any, Any, PortfolioConfig]] = []

    def fake_analyze(
        prepared: Any,
        actual: Any,
        no_cost: Any,
        *,
        portfolio_config: PortfolioConfig,
    ) -> ReplayAnalysisBuild:
        analysis_calls.append((prepared, actual, no_cost, portfolio_config))
        return ReplayAnalysisBuild(
            analysis={"version": "fixture-analysis"},
            exposure_matched_curve=[49_000_000.0 + index for index in range(len(dates))],
        )

    monkeypatch.setattr(replay, "analyze_replay", fake_analyze)
    monkeypatch.setattr(
        replay,
        "build_validation",
        lambda *_args, **_kwargs: ({"kind": "validation"}, SimpleNamespace()),
    )
    monkeypatch.setattr(
        replay,
        "build_walk_forward",
        lambda *_args, **_kwargs: {"kind": "walk-forward"},
    )
    monkeypatch.setattr(
        replay,
        "build_stress_tests",
        lambda *_args, **_kwargs: {"kind": "stress"},
    )
    progress: list[tuple[str, int, int]] = []
    config = PortfolioConfig()

    build = engine.run(
        "run-with-diagnostics",
        data_version="fixture-v1",
        portfolio_config=config,
        split_date=dates[252],
        universe_mode=UniverseMode.POINT_IN_TIME,
        walk_forward_train_years=2,
        walk_forward_test_years=1,
        walk_forward_step_years=2,
        include_diagnostics=True,
        progress=lambda stage, completed, total: progress.append((stage, completed, total)),
    )

    assert len(simulation_calls) == 2
    assert simulation_calls[0]["run_id"] == "run-with-diagnostics"
    assert simulation_calls[1]["run_id"] == "run-with-diagnostics-no-cost"
    no_cost = simulation_calls[1]["portfolio_config"]
    assert no_cost.us_trade_cost == 0.0
    assert no_cost.kr_trade_cost == 0.0
    assert no_cost.initial_fx_cost == 0.0
    assert no_cost.us_buy_cost == 0.0
    assert no_cost.us_sell_cost == 0.0
    assert no_cost.kr_buy_cost == 0.0
    assert no_cost.kr_sell_cost == 0.0
    assert no_cost.slippage_bps == 0.0
    assert len(analysis_calls) == 1
    assert build.validation == {"kind": "validation"}
    assert build.walk_forward == {"kind": "walk-forward"}
    assert build.stress_tests == {"kind": "stress"}
    assert build.result.equity_curve[0]["exposure_matched_benchmark"] == 49_000_000.0
    assert build.result.equity_curve[0]["no_cost_portfolio"] == 50_000_000.0
    assert not any("현재 상장 종목 기준" in warning for warning in build.result.warnings)
    assert any("시점 기준 종목군" in warning for warning in build.result.warnings)
    assert any("복구 이력이 있는 종목 1개" in warning for warning in build.result.warnings)
    assert [item for item in progress if item[0] == "VALIDATE"] == [
        ("VALIDATE", 0, 3),
        ("VALIDATE", 1, 3),
        ("VALIDATE", 2, 3),
        ("VALIDATE", 3, 3),
    ]
    assert ("SIMULATE_NO_COST", 0, 1) in progress
    assert ("SIMULATE_NO_COST", 1, 1) in progress
    assert ("ANALYZE", 0, 1) in progress
    assert ("ANALYZE", 1, 1) in progress


@pytest.mark.parametrize(
    "split_offset",
    [-1, 251, 252, 600],
)
def test_run_rejects_a_split_without_252_dates_on_each_side(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    split_offset: int,
) -> None:
    engine = ResearchReplayEngine(ResearchSnapshotStore(tmp_path / "research"))
    start = date(2022, 1, 1)
    dates = [start + timedelta(days=index) for index in range(503)]
    calls = _stub_run_dependencies(engine, monkeypatch, tmp_path, dates)
    split_date = start + timedelta(days=split_offset)

    with pytest.raises(RuntimeError, match="각각 최소 252 평가일"):
        engine.run(
            "run-invalid-split",
            data_version="fixture-v1",
            portfolio_config=PortfolioConfig(),
            split_date=split_date,
        )

    assert calls == []
