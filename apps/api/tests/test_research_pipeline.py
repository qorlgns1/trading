import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from quant_api.research_pipeline import (
    PriceBuildResult,
    PriceDownloadJob,
    PriceDownloadResult,
    PriceFetcher,
    PriceSnapshotBuilder,
    _legacy_checkpoint_key,
)
from quant_api.research_store import ResearchSnapshotStore
from quant_api.universe import UniverseSnapshot
from quant_core.enums import PeerGroup
from quant_core.providers import DEFAULT_BENCHMARK, UniverseAsset


def _assets() -> list[UniverseAsset]:
    return [
        UniverseAsset(
            ticker=f"TEST{index}",
            name=f"Test {group.value}",
            peer_group=group,
            currency="USD" if group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF} else "KRW",
            benchmark_ticker=DEFAULT_BENCHMARK[group],
        )
        for index, group in enumerate(PeerGroup)
    ]


def _universe(tmp_path: Path) -> UniverseSnapshot:
    assets = _assets()
    path = tmp_path / "source-universe.csv"
    pl.DataFrame(
        {
            "asset_id": [f"{asset.peer_group.value}:{asset.ticker}" for asset in assets],
            "ticker": [asset.ticker for asset in assets],
            "name": [asset.name for asset in assets],
            "peer_group": [asset.peer_group.value for asset in assets],
            "currency": [asset.currency for asset in assets],
            "benchmark_ticker": [asset.benchmark_ticker for asset in assets],
            "is_supported": [True] * len(assets),
            "data_status": ["READY"] * len(assets),
        }
    ).write_csv(path)
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text("{}")
    return UniverseSnapshot(
        version="universe-test",
        path=path,
        manifest_path=manifest,
        assets=assets,
        sources={"test": True},
        counts={group.value: 1 for group in PeerGroup},
    )


class FakePriceFetcher:
    def __init__(self, empty_group: PeerGroup | None = None) -> None:
        self.calls = 0
        self.empty_group = empty_group

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        del start
        self.calls += 1
        if assets and assets[0].peer_group is self.empty_group:
            return pl.DataFrame()
        dates: list[date] = []
        current = end - timedelta(days=370)
        while len(dates) < 260:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        frames = []
        for asset in assets:
            frames.append(
                pl.DataFrame(
                    {
                        "date": dates,
                        "asset_id": [f"{asset.peer_group.value}:{asset.ticker}"] * len(dates),
                        "symbol": [asset.ticker] * len(dates),
                        "name": [asset.name] * len(dates),
                        "peer_group": [asset.peer_group.value] * len(dates),
                        "currency": [asset.currency] * len(dates),
                        "open": [100.0] * len(dates),
                        "close": [101.0] * len(dates),
                        "adjusted_close": [101.0] * len(dates),
                        "volume": [1_000_000.0] * len(dates),
                        "split_ratio": [1.0] * len(dates),
                        "dividend": [0.0] * len(dates),
                        "is_suspended": [False] * len(dates),
                        "is_supported": [True] * len(dates),
                        "benchmark_close": [100.0] * len(dates),
                        "fx_krw_per_usd": [1_350.0] * len(dates),
                        "delisted": [False] * len(dates),
                        "recovery_value": [None] * len(dates),
                        "provider_repaired": [False] * len(dates),
                    }
                )
            )
        return pl.concat(frames)


class CorporateActionFetcher(FakePriceFetcher):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[tuple[PeerGroup, date]] = []

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        group = assets[0].peer_group
        self.starts.append((group, start))
        frame = super().fetch(assets, start, end)
        if group is not PeerGroup.US_STOCK:
            return frame
        frame = frame.with_columns(
            pl.when(pl.int_range(pl.len()) == pl.len() - 1)
            .then(0.5)
            .otherwise(pl.col("dividend"))
            .alias("dividend")
        )
        return (
            frame
            if start.year >= 2025
            else frame.with_columns(pl.lit(90.0).alias("adjusted_close"))
        )


def _builder(
    root: Path,
    fetcher: PriceFetcher,
    *,
    history_years: int = 10,
    batch_size: int = 2,
    download_workers: int = 1,
    max_retries: int = 2,
    sleeper: Callable[[float], None] | None = None,
) -> PriceSnapshotBuilder:
    return PriceSnapshotBuilder(
        store=ResearchSnapshotStore(root),
        fetcher=fetcher,
        history_years=history_years,
        batch_size=batch_size,
        download_workers=download_workers,
        max_retries=max_retries,
        minimum_group_assets=1,
        clock=lambda: datetime(2026, 7, 10, 8, tzinfo=UTC),
        sleeper=sleeper or (lambda _: None),
    )


def _snapshot_with_assets(
    tmp_path: Path,
    assets: list[UniverseAsset],
    version: str,
) -> UniverseSnapshot:
    path = tmp_path / f"source-{version}.csv"
    pl.DataFrame(
        {
            "asset_id": [f"{asset.peer_group.value}:{asset.ticker}" for asset in assets],
            "ticker": [asset.ticker for asset in assets],
            "name": [asset.name for asset in assets],
            "peer_group": [asset.peer_group.value for asset in assets],
            "currency": [asset.currency for asset in assets],
            "benchmark_ticker": [asset.benchmark_ticker for asset in assets],
            "is_supported": [True] * len(assets),
            "data_status": ["READY"] * len(assets),
        }
    ).write_csv(path)
    manifest = tmp_path / f"source-manifest-{version}.json"
    manifest.write_text("{}")
    return UniverseSnapshot(
        version=version,
        path=path,
        manifest_path=manifest,
        assets=assets,
        sources={"test": True},
        counts={
            group.value: sum(asset.peer_group is group for asset in assets) for group in PeerGroup
        },
    )


def _universe_with_us_assets(tmp_path: Path, count: int) -> UniverseSnapshot:
    base_assets = _assets()
    us_template = base_assets[0]
    us_assets = [
        us_template
        if index == 0
        else replace(
            us_template,
            ticker=f"US_EXTRA_{index}",
            name=f"US extra {index}",
        )
        for index in range(count)
    ]
    return _snapshot_with_assets(
        tmp_path,
        us_assets + base_assets[1:],
        f"universe-{count}-us",
    )


def _price_frame(
    assets: list[UniverseAsset],
    end: date,
    *,
    actions: dict[str, tuple[float, float]] | None = None,
    adjusted_close: float = 101.0,
) -> pl.DataFrame:
    dates: list[date] = []
    current = end - timedelta(days=370)
    while len(dates) < 260:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    actions = actions or {}
    frames: list[pl.DataFrame] = []
    for asset in assets:
        dividend, split_ratio = actions.get(asset.ticker, (0.0, 1.0))
        frames.append(
            pl.DataFrame(
                {
                    "date": dates,
                    "asset_id": [f"{asset.peer_group.value}:{asset.ticker}"] * len(dates),
                    "symbol": [asset.ticker] * len(dates),
                    "name": [asset.name] * len(dates),
                    "peer_group": [asset.peer_group.value] * len(dates),
                    "currency": [asset.currency] * len(dates),
                    "open": [100.0] * len(dates),
                    "close": [101.0] * len(dates),
                    "adjusted_close": [adjusted_close] * len(dates),
                    "volume": [1_000_000.0] * len(dates),
                    "split_ratio": [1.0] * (len(dates) - 1) + [split_ratio],
                    "dividend": [0.0] * (len(dates) - 1) + [dividend],
                    "is_suspended": [False] * len(dates),
                    "is_supported": [True] * len(dates),
                    "benchmark_close": [100.0] * len(dates),
                    "fx_krw_per_usd": [1_350.0] * len(dates),
                    "delisted": [False] * len(dates),
                    "recovery_value": [None] * len(dates),
                    "provider_repaired": [False] * len(dates),
                }
            )
        )
    return pl.concat(frames) if frames else pl.DataFrame()


class EventComparisonFetcher:
    def __init__(
        self,
        actions: dict[str, tuple[float, float]] | None = None,
        *,
        full_adjusted_close: float = 101.0,
    ) -> None:
        self.actions = actions or {}
        self.full_adjusted_close = full_adjusted_close
        self.calls: list[tuple[tuple[str, ...], PeerGroup, date]] = []

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        self.calls.append((tuple(asset.ticker for asset in assets), assets[0].peer_group, start))
        return (
            _price_frame(
                assets,
                end,
                actions=self.actions,
                adjusted_close=self.full_adjusted_close if start.year == 2016 else 101.0,
            )
            .filter((pl.col("date") >= pl.lit(start)) & (pl.col("date") < pl.lit(end)))
        )

    def full_calls(self, group: PeerGroup | None = None) -> list[tuple[str, ...]]:
        return [
            tickers
            for tickers, call_group, start in self.calls
            if start.year == 2016 and (group is None or call_group is group)
        ]


def _activate(root: Path, result: PriceBuildResult) -> None:
    ResearchSnapshotStore(root).activate(result.staging_path, result.manifest)


def _checkpoint_files(root: Path, group: PeerGroup) -> list[Path]:
    return sorted((root / "checkpoints" / group.value).glob("*.parquet"))


def _install_universe_provenance(root: Path, universe: UniverseSnapshot) -> None:
    path = root / "universes" / universe.version / "universe.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(universe.path.read_bytes())


def _move_canonical_checkpoints_to_legacy(
    root: Path,
    universe: UniverseSnapshot,
) -> None:
    _install_universe_provenance(root, universe)
    start = date(2016, 7, 11)
    end = date(2026, 7, 11)
    for group in PeerGroup:
        canonical_files = _checkpoint_files(root, group)
        assert len(canonical_files) == 1
        assets = [asset for asset in universe.assets if asset.peer_group is group]
        legacy_dir = root / "checkpoints" / universe.version / group.value
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy = legacy_dir / f"{_legacy_checkpoint_key(assets, start, end)}.parquet"
        canonical_files[0].replace(legacy)


def test_initial_price_build_is_partitioned_and_checkpoints_are_reused(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    fetcher = FakePriceFetcher()
    result = _builder(tmp_path / "research", fetcher).build("run-1", universe)

    assert fetcher.calls == 6
    assert len(list((result.staging_path / "bars").rglob("bars.parquet"))) >= 6
    assert all(result.manifest["coverage"][group.value]["ready_assets"] == 1 for group in PeerGroup)

    second_fetcher = FakePriceFetcher()
    second = _builder(tmp_path / "research", second_fetcher).build("run-2", universe)
    assert second_fetcher.calls == 0
    assert second.manifest["bars_sha256"] == result.manifest["bars_sha256"]


def test_checkpoint_reuse_is_independent_of_universe_version(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)

    second_fetcher = FakePriceFetcher()
    second = _builder(root, second_fetcher).build(
        "run-2",
        replace(universe, version="universe-changed"),
    )

    assert second_fetcher.calls == 0
    assert second.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup)


def test_benchmark_change_invalidates_checkpoint_request_identity(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    changed_assets = [
        replace(asset, benchmark_ticker="^DJI")
        if asset.peer_group is PeerGroup.US_STOCK
        else asset
        for asset in universe.assets
    ]

    fetcher = FakePriceFetcher()
    result = _builder(root, fetcher).build(
        "run-2",
        _snapshot_with_assets(tmp_path, changed_assets, "universe-new-benchmark"),
    )

    assert fetcher.calls == 1
    assert result.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup) - 1


def test_cached_prices_rehydrate_current_name_and_currency(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    changed_assets = [
        replace(asset, name="Renamed asset", currency="EUR")
        if asset.peer_group is PeerGroup.US_STOCK
        else asset
        for asset in universe.assets
    ]

    fetcher = FakePriceFetcher()
    result = _builder(root, fetcher).build(
        "run-2",
        _snapshot_with_assets(tmp_path, changed_assets, "universe-new-metadata"),
    )

    assert fetcher.calls == 0
    bars = pl.scan_parquet(
        str(result.staging_path / "bars" / "peer_group=US_STOCK" / "year=*" / "bars.parquet")
    ).collect()
    assert bars.get_column("name").unique().to_list() == ["Renamed asset"]
    assert bars.get_column("currency").unique().to_list() == ["EUR"]


def test_legacy_universe_checkpoint_is_validated_and_promoted(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    _move_canonical_checkpoints_to_legacy(root, universe)

    second_fetcher = FakePriceFetcher()
    second = _builder(root, second_fetcher).build(
        "run-2",
        replace(universe, version="universe-changed"),
    )

    assert second_fetcher.calls == 0
    assert second.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup)
    assert all(len(_checkpoint_files(root, group)) == 1 for group in PeerGroup)


@pytest.mark.parametrize(
    "corruption",
    ["missing-schema", "wrong-identity", "duplicate-date", "outside-date-range"],
)
def test_invalid_legacy_checkpoint_is_not_promoted(
    tmp_path: Path,
    corruption: str,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    _move_canonical_checkpoints_to_legacy(root, universe)
    legacy = next(
        (root / "checkpoints" / universe.version / PeerGroup.US_STOCK.value).glob("*.parquet")
    )
    frame = pl.read_parquet(legacy)
    if corruption == "missing-schema":
        frame = frame.drop("is_suspended")
    elif corruption == "wrong-identity":
        frame = frame.with_columns(pl.lit("WRONG").alias("symbol"))
    elif corruption == "duplicate-date":
        frame = pl.concat([frame, frame.head(1)])
    else:
        frame = frame.with_row_index("_row").with_columns(
            pl.when(pl.col("_row") == 0)
            .then(pl.lit(date(2015, 7, 10)))
            .otherwise(pl.col("date"))
            .alias("date")
        ).drop("_row")
    frame.write_parquet(legacy)

    fetcher = FakePriceFetcher()
    result = _builder(root, fetcher).build(
        "run-2",
        replace(universe, version="universe-changed"),
    )

    assert fetcher.calls == 1
    assert result.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup) - 1
    assert len(_checkpoint_files(root, PeerGroup.US_STOCK)) == 1


def test_invalid_canonical_checkpoint_is_replaced_by_fresh_fetch(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    checkpoint = _checkpoint_files(root, PeerGroup.US_STOCK)[0]
    pl.read_parquet(checkpoint).drop("delisted").write_parquet(checkpoint)

    fetcher = FakePriceFetcher()
    result = _builder(root, fetcher).build("run-2", universe)

    assert fetcher.calls == 1
    assert result.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup) - 1
    assert "delisted" in pl.read_parquet(checkpoint).columns


def test_old_ticker_only_canonical_checkpoint_is_not_reused(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    _builder(root, FakePriceFetcher()).build("run-1", universe)
    canonical = _checkpoint_files(root, PeerGroup.US_STOCK)[0]
    asset = next(asset for asset in universe.assets if asset.peer_group is PeerGroup.US_STOCK)
    old_key = _legacy_checkpoint_key(
        [asset],
        date(2016, 7, 11),
        date(2026, 7, 11),
    )
    canonical.replace(canonical.with_name(f"{old_key}.parquet"))

    fetcher = FakePriceFetcher()
    result = _builder(root, fetcher).build("run-2", universe)

    assert fetcher.calls == 1
    assert result.manifest["download_stats"]["checkpoint_hits"] == len(PeerGroup) - 1


def test_legacy_checkpoint_symlink_outside_root_is_ignored(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    asset = next(asset for asset in universe.assets if asset.peer_group is PeerGroup.US_STOCK)
    start = date(2016, 7, 11)
    end = date(2026, 7, 11)
    outside = tmp_path / "outside.parquet"
    _price_frame([asset], end).write_parquet(outside)
    legacy = (
        root
        / "checkpoints"
        / "universe-unsafe"
        / PeerGroup.US_STOCK.value
        / f"{_legacy_checkpoint_key([asset], start, end)}.parquet"
    )
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.symlink_to(outside)
    builder = _builder(root, FakePriceFetcher())

    assert builder._legacy_checkpoint_files(
        PeerGroup.US_STOCK,
        [asset],
        start,
        end,
    ) == []


def test_concurrent_checkpoint_writers_use_unique_atomic_temps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    asset = universe.assets[0]
    builder = _builder(root, FakePriceFetcher())
    path = root / "checkpoints" / PeerGroup.US_STOCK.value / "concurrent.parquet"
    frames = [
        _price_frame([asset], date(2026, 7, 11), adjusted_close=value)
        for value in (101.0, 202.0)
    ]
    barrier = threading.Barrier(2)
    original_write = pl.DataFrame.write_parquet

    def synchronized_write(frame: pl.DataFrame, file: Any, *args: Any, **kwargs: Any) -> None:
        original_write(frame, file, *args, **kwargs)
        barrier.wait(timeout=5)

    monkeypatch.setattr(pl.DataFrame, "write_parquet", synchronized_write)
    errors: list[BaseException] = []

    def write(frame: pl.DataFrame) -> None:
        try:
            builder._write_checkpoint(path, frame)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(frame,)) for frame in frames]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    stored = pl.read_parquet(path)
    assert stored.height == frames[0].height
    assert stored.get_column("adjusted_close").unique().to_list()[0] in {101.0, 202.0}
    assert list(path.parent.glob(f".{path.name}.*.tmp.parquet")) == []


def test_legacy_promotion_does_not_overwrite_concurrent_fresh_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    asset = universe.assets[0]
    builder = _builder(root, EventComparisonFetcher(full_adjusted_close=202.0))
    start = date(2016, 7, 11)
    end = date(2026, 7, 11)
    job = PriceDownloadJob(
        0,
        "DOWNLOAD",
        PeerGroup.US_STOCK,
        [asset],
        start,
        end,
    )
    path = builder._checkpoint_file(PeerGroup.US_STOCK, [asset], start, end)
    legacy = _price_frame([asset], date(2026, 7, 11), adjusted_close=101.0)
    _install_universe_provenance(root, universe)
    legacy_path = (
        root
        / "checkpoints"
        / universe.version
        / PeerGroup.US_STOCK.value
        / f"{_legacy_checkpoint_key([asset], start, end)}.parquet"
    )
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_parquet(legacy_path)
    link_started = threading.Event()
    allow_link = threading.Event()
    original_link = os.link

    def blocked_link(source: Any, destination: Any) -> None:
        link_started.set()
        assert allow_link.wait(timeout=5)
        original_link(source, destination)

    monkeypatch.setattr(os, "link", blocked_link)
    errors: list[BaseException] = []
    results: list[PriceDownloadResult] = []

    def read_legacy() -> None:
        try:
            results.append(builder._execute_download_job(job, root / "legacy-updates"))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=read_legacy)
    thread.start()
    assert link_started.wait(timeout=5)
    fresh_result = builder._execute_download_job(
        replace(job, index=1, bypass_checkpoint=True),
        root / "fresh-updates",
    )
    allow_link.set()
    thread.join(timeout=5)

    assert errors == []
    assert len(results) == 1
    legacy_result = results[0]
    assert fresh_result.frame.get_column("adjusted_close").unique().to_list() == [202.0]
    assert legacy_result.frame.get_column("adjusted_close").unique().to_list() == [202.0]
    assert legacy_result.data_path is not None
    assert (
        pl.read_parquet(legacy_result.data_path)
        .get_column("adjusted_close")
        .unique()
        .to_list()
        == [202.0]
    )
    assert pl.read_parquet(path).get_column("adjusted_close").unique().to_list() == [202.0]


def test_failed_build_does_not_replace_current_pointer(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    result = _builder(root, FakePriceFetcher()).build("good", universe)
    (result.staging_path / "scores" / "latest.parquet").parent.mkdir(exist_ok=True)
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(
        result.staging_path / "scores" / "latest.parquet"
    )
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(
        result.staging_path / "scores" / "history.parquet"
    )
    store = ResearchSnapshotStore(root)
    store.activate(result.staging_path, result.manifest)
    pointer_before = json.loads(store.pointer_path.read_text())
    for checkpoint in _checkpoint_files(root, PeerGroup.US_STOCK):
        checkpoint.unlink()

    with pytest.raises(RuntimeError, match="최소 데이터 종목 수"):
        _builder(root, FakePriceFetcher(PeerGroup.US_STOCK)).build(
            "failed", replace(universe, version="universe-test-failed"), force_full=True
        )

    assert json.loads(store.pointer_path.read_text()) == pointer_before


def test_corporate_action_refetches_and_replaces_full_asset_history(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, FakePriceFetcher()).build("initial", universe)
    score_root = initial.staging_path / "scores"
    score_root.mkdir(exist_ok=True)
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(score_root / "latest.parquet")
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(score_root / "history.parquet")
    store = ResearchSnapshotStore(root)
    store.activate(initial.staging_path, initial.manifest)

    fetcher = CorporateActionFetcher()
    updated_universe = replace(universe, version="universe-test-next")
    updated = _builder(root, fetcher).build("incremental", updated_universe)

    asset_id = "US_STOCK:TEST0"
    assert updated.manifest["corporate_action_refetches"] == [asset_id]
    us_starts = [start for group, start in fetcher.starts if group is PeerGroup.US_STOCK]
    assert len(us_starts) == 2
    assert min(us_starts).year == 2016
    bars = pl.concat(
        [
            pl.read_parquet(path)
            for path in (updated.staging_path / "bars" / "peer_group=US_STOCK").rglob(
                "bars.parquet"
            )
        ]
    ).filter(pl.col("asset_id") == asset_id)
    assert bars.get_column("adjusted_close").unique().to_list() == [90.0]


@pytest.mark.parametrize("action", [(0.5, 1.0), (0.0, 2.0)])
def test_unchanged_corporate_action_skips_full_history_request(
    tmp_path: Path,
    action: tuple[float, float],
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, EventComparisonFetcher({"TEST0": action})).build("initial", universe)
    _activate(root, initial)

    fetcher = EventComparisonFetcher({"TEST0": action}, full_adjusted_close=77.0)
    result = _builder(root, fetcher).build(
        "incremental", replace(universe, version="universe-unchanged-action")
    )

    assert fetcher.full_calls(PeerGroup.US_STOCK) == []
    assert result.manifest["corporate_action_refetches"] == []
    assert result.manifest["download_stats"]["corporate_action_candidates"] == 1
    assert result.manifest["download_stats"]["corporate_action_unchanged_skips"] == 1
    assert result.manifest["download_stats"]["corporate_action_refetches"] == 0


@pytest.mark.parametrize(
    ("initial_actions", "incoming_actions"),
    [
        ({}, {"TEST0": (0.5, 1.0)}),
        ({"TEST0": (0.5, 1.0)}, {"TEST0": (0.75, 1.0)}),
        ({"TEST0": (0.0, 2.0)}, {}),
    ],
    ids=["added", "amount-changed", "removed"],
)
def test_changed_corporate_action_refetches_once_and_replaces_asset_history(
    tmp_path: Path,
    initial_actions: dict[str, tuple[float, float]],
    incoming_actions: dict[str, tuple[float, float]],
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, EventComparisonFetcher(initial_actions)).build("initial", universe)
    _activate(root, initial)

    fetcher = EventComparisonFetcher(incoming_actions, full_adjusted_close=77.0)
    result = _builder(root, fetcher).build(
        "incremental", replace(universe, version="universe-changed-action")
    )

    assert fetcher.full_calls(PeerGroup.US_STOCK) == [("TEST0",)]
    assert result.manifest["corporate_action_refetches"] == ["US_STOCK:TEST0"]
    assert result.manifest["download_stats"]["corporate_action_candidates"] == 1
    assert result.manifest["download_stats"]["corporate_action_unchanged_skips"] == 0
    assert result.manifest["download_stats"]["corporate_action_refetches"] == 1
    bars = pl.concat(
        [
            pl.read_parquet(path)
            for path in (result.staging_path / "bars" / "peer_group=US_STOCK").rglob("bars.parquet")
        ]
    ).filter(pl.col("asset_id") == "US_STOCK:TEST0")
    assert bars.get_column("adjusted_close").unique().to_list() == [77.0]


def test_changed_action_assets_are_batched_by_peer_group(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe_with_us_assets(tmp_path, 3)
    initial = _builder(root, EventComparisonFetcher(), batch_size=10).build("initial", universe)
    _activate(root, initial)
    changed_tickers = {
        asset.ticker: (0.5, 1.0)
        for asset in universe.assets
        if asset.peer_group is PeerGroup.US_STOCK
    }

    fetcher = EventComparisonFetcher(changed_tickers, full_adjusted_close=77.0)
    result = _builder(root, fetcher, batch_size=10).build(
        "incremental", replace(universe, version="universe-batched-actions")
    )

    assert fetcher.full_calls(PeerGroup.US_STOCK) == [tuple(changed_tickers)]
    stats = result.manifest["download_stats"]
    assert stats["action_refresh_batches"] == 1
    assert stats["corporate_action_candidates"] == 3
    assert stats["corporate_action_refetches"] == 3


class ConcurrentFetcher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        del start
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 3:
                self.release.set()
        try:
            self.release.wait(timeout=2)
            return _price_frame(assets, end)
        finally:
            with self.lock:
                self.active -= 1


def test_download_concurrency_is_capped_at_three(tmp_path: Path) -> None:
    fetcher = ConcurrentFetcher()
    result = _builder(
        tmp_path / "research",
        fetcher,
        batch_size=1,
        download_workers=3,
    ).build("initial", _universe(tmp_path))

    assert fetcher.max_active == 3
    assert result.manifest["download_stats"]["workers_configured"] == 3


class YFRateLimitError(RuntimeError):
    pass


class RateLimitFallbackFetcher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rate_seen = threading.Event()
        self.rate_raised = False
        self.tail_active = 0
        self.tail_max_active = 0

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        del start
        ticker = assets[0].ticker
        if ticker == "TEST0":
            with self.lock:
                if not self.rate_raised:
                    self.rate_raised = True
                    self.rate_seen.set()
                    raise YFRateLimitError("rate limit")
        elif ticker in {"TEST1", "TEST2"}:
            self.rate_seen.wait(timeout=2)
            time.sleep(0.03)

        is_tail = ticker in {"TEST3", "TEST4", "TEST5"}
        if is_tail:
            with self.lock:
                self.tail_active += 1
                self.tail_max_active = max(self.tail_max_active, self.tail_active)
            time.sleep(0.01)
        try:
            return _price_frame(assets, end)
        finally:
            if is_tail:
                with self.lock:
                    self.tail_active -= 1


def test_rate_limit_switches_not_yet_submitted_jobs_to_serial_mode(tmp_path: Path) -> None:
    fetcher = RateLimitFallbackFetcher()
    result = _builder(
        tmp_path / "research",
        fetcher,
        batch_size=1,
        download_workers=3,
        max_retries=2,
    ).build("initial", _universe(tmp_path))

    assert fetcher.rate_raised is True
    assert fetcher.tail_max_active == 1
    assert result.manifest["download_stats"]["rate_limit_fallback"] is True


def test_progress_advances_after_action_comparison_and_is_monotonic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, EventComparisonFetcher()).build("initial", universe)
    _activate(root, initial)
    builder = _builder(root, EventComparisonFetcher({"TEST0": (0.5, 1.0)}))
    comparison_calls = 0
    original = builder._corporate_action_signatures

    def record_comparison(
        frame: pl.DataFrame,
    ) -> dict[str, frozenset[tuple[date, float, float]]]:
        nonlocal comparison_calls
        comparison_calls += 1
        return original(frame)

    monkeypatch.setattr(builder, "_corporate_action_signatures", record_comparison)
    updates: list[tuple[str, int, int, int]] = []

    def progress(
        stage: str,
        completed: int,
        total: int,
        failed: list[dict[str, Any]],
    ) -> None:
        del failed
        updates.append((stage, completed, total, comparison_calls))

    builder.build(
        "incremental",
        replace(universe, version="universe-progress"),
        progress,
    )

    download = [update for update in updates if update[0] == "DOWNLOAD"]
    assert [completed for _, completed, _, _ in download] == list(range(1, len(download) + 1))
    assert all(comparison_count >= completed + 1 for _, completed, _, comparison_count in download)
    refresh_actions = [
        (completed, total) for stage, completed, total, _ in updates if stage == "REFRESH_ACTIONS"
    ]
    assert refresh_actions == [(0, 1), (1, 1)]


class PartialBatchFetcher(EventComparisonFetcher):
    def __init__(self, partial_us: bool) -> None:
        super().__init__()
        self.partial_us = partial_us

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        self.calls.append((tuple(asset.ticker for asset in assets), assets[0].peer_group, start))
        selected = assets
        if self.partial_us and assets[0].peer_group is PeerGroup.US_STOCK:
            selected = assets[:-1]
        return _price_frame(selected, end).filter(
            (pl.col("date") >= pl.lit(start)) & (pl.col("date") < pl.lit(end))
        )


def test_partial_batch_is_not_checkpointed_and_is_retried(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe_with_us_assets(tmp_path, 2)
    initial = _builder(root, EventComparisonFetcher(), batch_size=10).build("initial", universe)
    _activate(root, initial)
    updated_universe = replace(universe, version="universe-partial-retry")
    initial_us_checkpoints = set(_checkpoint_files(root, PeerGroup.US_STOCK))

    partial_fetcher = PartialBatchFetcher(partial_us=True)
    first = _builder(
        root,
        partial_fetcher,
        batch_size=10,
        max_retries=1,
    ).build("partial", updated_universe)

    assert [item["ticker"] for item in first.failed] == ["US_EXTRA_1"]
    assert first.manifest["download_stats"]["primary_batches"] == 6
    assert first.manifest["download_stats"]["incremental_assets"] == 7
    assert set(_checkpoint_files(root, PeerGroup.US_STOCK)) == initial_us_checkpoints

    retry_fetcher = PartialBatchFetcher(partial_us=False)
    retried = _builder(root, retry_fetcher, batch_size=10, max_retries=1).build(
        "retry", updated_universe
    )

    assert retry_fetcher.calls == [(("TEST0", "US_EXTRA_1"), PeerGroup.US_STOCK, date(2026, 5, 19))]
    assert retried.failed == []
    assert retried.manifest["download_stats"]["checkpoint_hits"] == 5


class HistoryBoundaryFetcher:
    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        frame = _price_frame(assets, end)
        boundary_rows = (
            frame.group_by("asset_id", maintain_order=True)
            .first()
            .with_columns(pl.lit(start).cast(pl.Date).alias("date"))
        )
        return pl.concat([boundary_rows, frame], how="diagonal_relaxed")


def test_incremental_build_removes_rows_before_new_history_start(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(
        root,
        HistoryBoundaryFetcher(),
        history_years=11,
    ).build("initial", universe)
    _activate(root, initial)

    updated = _builder(
        root,
        HistoryBoundaryFetcher(),
        history_years=10,
    ).build(
        "incremental",
        replace(universe, version="universe-shorter-history"),
    )

    history_start = date.fromisoformat(str(updated.manifest["history_start"]))
    bars = pl.scan_parquet(
        str(updated.staging_path / "bars" / "peer_group=*" / "year=*" / "bars.parquet"),
        hive_partitioning=True,
    ).collect()
    minimum_date = bars.select(pl.col("date").min()).item()
    assert isinstance(minimum_date, date)
    assert minimum_date >= history_start


@pytest.mark.parametrize("universe_change", ["removed", "peer-group-changed"])
def test_incremental_build_removes_stale_asset_ids_not_in_current_universe(
    tmp_path: Path,
    universe_change: str,
) -> None:
    root = tmp_path / "research"
    initial_universe = _universe_with_us_assets(tmp_path, 2)
    initial = _builder(root, EventComparisonFetcher(), batch_size=10).build(
        "initial", initial_universe
    )
    _activate(root, initial)

    if universe_change == "removed":
        updated_assets = [
            asset for asset in initial_universe.assets if asset.ticker != "US_EXTRA_1"
        ]
    else:
        updated_assets = [
            replace(
                asset,
                peer_group=PeerGroup.US_EQUITY_ETF,
                benchmark_ticker=DEFAULT_BENCHMARK[PeerGroup.US_EQUITY_ETF],
            )
            if asset.ticker == "US_EXTRA_1"
            else asset
            for asset in initial_universe.assets
        ]
    updated_universe = _snapshot_with_assets(
        tmp_path,
        updated_assets,
        f"universe-{universe_change}",
    )

    updated = _builder(root, EventComparisonFetcher(), batch_size=10).build(
        "incremental", updated_universe
    )

    expected_groups = {
        f"{asset.peer_group.value}:{asset.ticker}": asset.peer_group.value
        for asset in updated_assets
        if asset.is_supported
    }
    bars = pl.scan_parquet(
        str(updated.staging_path / "bars" / "peer_group=*" / "year=*" / "bars.parquet"),
        hive_partitioning=True,
    ).collect()
    stored_universe = pl.read_csv(updated.staging_path / "universe.csv")
    actual_groups = {
        str(asset_id): str(peer_group)
        for asset_id, peer_group in bars.select("asset_id", "peer_group").unique().iter_rows()
    }
    assert set(stored_universe.get_column("asset_id").to_list()) == set(expected_groups)
    assert actual_groups == expected_groups


class ShortActionHistoryFetcher(EventComparisonFetcher):
    def __init__(self) -> None:
        super().__init__({"TEST0": (0.5, 1.0)})

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        self.calls.append((tuple(asset.ticker for asset in assets), assets[0].peer_group, start))
        is_full_action_fetch = assets[0].peer_group is PeerGroup.US_STOCK and start.year == 2016
        frame = _price_frame(
            assets,
            end,
            actions=self.actions,
            adjusted_close=55.0 if is_full_action_fetch else 102.0,
        )
        return frame.tail(20 if is_full_action_fetch else 30)


def test_short_action_full_history_is_rejected_without_replacing_existing_data(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, EventComparisonFetcher()).build("initial", universe)
    _activate(root, initial)
    previous = (
        ResearchSnapshotStore(root)
        .scan_bars(peer_group=PeerGroup.US_STOCK)
        .filter(pl.col("asset_id") == "US_STOCK:TEST0")
        .collect()
    )
    previous_first_date = previous.select(pl.col("date").min()).item()

    fetcher = ShortActionHistoryFetcher()
    updated_universe = replace(universe, version="universe-short-action-history")
    updated = _builder(root, fetcher).build("incremental", updated_universe)

    failure = next(item for item in updated.failed if item["ticker"] == "TEST0")
    assert "전체 이력 응답이 기존 범위를 충족하지 않습니다" in failure["reason"]
    assert updated.manifest["corporate_action_refetches"] == []
    bars = (
        pl.scan_parquet(
            str(updated.staging_path / "bars" / "peer_group=US_STOCK" / "year=*" / "bars.parquet")
        )
        .filter(pl.col("asset_id") == "US_STOCK:TEST0")
        .collect()
    )
    assert bars.height == previous.height
    assert bars.select(pl.col("date").min()).item() == previous_first_date
    assert 55.0 not in bars.get_column("adjusted_close").unique().to_list()
    checkpoint_files = _checkpoint_files(root, PeerGroup.US_STOCK)
    assert sorted(pl.read_parquet(path).height for path in checkpoint_files) == [30, 260]


def test_quality_repair_bypasses_matching_download_checkpoint(tmp_path: Path) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    fetcher = EventComparisonFetcher()
    builder = _builder(root, fetcher)
    result = builder.build("initial", universe)
    checkpoint_files = _checkpoint_files(root, PeerGroup.US_STOCK)
    assert len(checkpoint_files) == 1
    fetcher.calls.clear()
    fetcher.full_adjusted_close = 88.0

    failures = builder.refresh_assets(
        result,
        universe,
        [universe.assets[0]],
    )

    assert failures == []
    assert fetcher.full_calls(PeerGroup.US_STOCK) == [("TEST0",)]
    assert result.manifest["quality_refetches"] == ["US_STOCK:TEST0"]
    bars = (
        pl.scan_parquet(
            str(result.staging_path / "bars" / "peer_group=US_STOCK" / "year=*" / "bars.parquet")
        )
        .filter(pl.col("asset_id") == "US_STOCK:TEST0")
        .collect()
    )
    assert bars.get_column("adjusted_close").unique().to_list() == [88.0]


class RateLimitDuringBackoffFetcher:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.rate_raised = threading.Event()
        self.retry_release = threading.Event()
        self.backoff_active = threading.Event()
        self.timer_started = False
        self.first_rate_attempt = True
        self.tail_active = 0
        self.tail_max_active = 0
        self.tail_started_during_backoff = False
        self.serial_mode_calls = 0

    def sleeper(self, _: float) -> None:
        self.backoff_active.set()
        self.retry_release.wait(timeout=2)
        self.backoff_active.clear()

    def enable_serial_mode(self) -> None:
        with self.lock:
            self.serial_mode_calls += 1

    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        del start
        ticker = assets[0].ticker
        if ticker == "TEST0":
            with self.lock:
                if self.first_rate_attempt:
                    self.first_rate_attempt = False
                    self.rate_raised.set()
                    raise YFRateLimitError("rate limit during retry backoff")
        elif ticker in {"TEST1", "TEST2"}:
            self.rate_raised.wait(timeout=2)
            self.backoff_active.wait(timeout=2)
            with self.lock:
                if not self.timer_started:
                    self.timer_started = True
                    threading.Timer(0.1, self.retry_release.set).start()

        is_tail = ticker in {"TEST3", "TEST4", "TEST5"}
        if is_tail:
            with self.lock:
                self.tail_active += 1
                self.tail_max_active = max(self.tail_max_active, self.tail_active)
                self.tail_started_during_backoff = (
                    self.tail_started_during_backoff or self.backoff_active.is_set()
                )
            if self.backoff_active.is_set():
                self.retry_release.wait(timeout=2)
        try:
            return _price_frame(assets, end)
        finally:
            if is_tail:
                with self.lock:
                    self.tail_active -= 1


def test_rate_limit_signal_stops_tail_submission_during_retry_backoff(
    tmp_path: Path,
) -> None:
    fetcher = RateLimitDuringBackoffFetcher()
    result = _builder(
        tmp_path / "research",
        fetcher,
        batch_size=1,
        download_workers=3,
        max_retries=2,
        sleeper=fetcher.sleeper,
    ).build("initial", _universe(tmp_path))

    assert fetcher.rate_raised.is_set()
    assert fetcher.tail_started_during_backoff is False
    assert fetcher.tail_max_active == 1
    assert fetcher.serial_mode_calls >= 1
    assert result.manifest["download_stats"]["rate_limit_fallback"] is True


def test_action_refresh_revalidates_stale_full_checkpoint_with_same_universe_version(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(root, EventComparisonFetcher()).build("initial", universe)
    full_checkpoint_files = _checkpoint_files(root, PeerGroup.US_STOCK)
    assert len(full_checkpoint_files) == 1
    full_checkpoint = full_checkpoint_files[0]
    assert pl.read_parquet(full_checkpoint).get_column("dividend").max() == 0.0
    _activate(root, initial)

    fetcher = EventComparisonFetcher({"TEST0": (0.5, 1.0)})
    updated = _builder(root, fetcher).build("incremental", universe)

    assert fetcher.full_calls(PeerGroup.US_STOCK) == [("TEST0",)]
    assert updated.failed == []
    assert updated.manifest["corporate_action_refetches"] == ["US_STOCK:TEST0"]
    bars = (
        pl.scan_parquet(
            str(updated.staging_path / "bars" / "peer_group=US_STOCK" / "year=*" / "bars.parquet")
        )
        .filter(pl.col("asset_id") == "US_STOCK:TEST0")
        .collect()
    )
    dividends = bars.filter(pl.col("dividend") != 0.0).get_column("dividend").to_list()
    assert dividends == [0.5]
    checkpoint_dividends = (
        pl.read_parquet(full_checkpoint)
        .filter(pl.col("dividend") != 0.0)
        .get_column("dividend")
        .to_list()
    )
    assert checkpoint_dividends == [0.5]


class RepairedDividendFetcher(EventComparisonFetcher):
    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame:
        self.calls.append((tuple(asset.ticker for asset in assets), assets[0].peer_group, start))
        is_full = start.year == 2016
        actions = {"TEST0": (0.1 if is_full else 10.0, 1.0)}
        frame = _price_frame(
            assets,
            end,
            actions=actions,
            adjusted_close=77.0 if is_full else 102.0,
        )
        if not is_full and assets[0].peer_group is PeerGroup.US_STOCK:
            frame = frame.with_columns(
                pl.when(pl.col("date") == pl.col("date").max())
                .then(True)
                .otherwise(pl.col("provider_repaired"))
                .alias("provider_repaired")
            )
        return frame


def test_fresh_full_history_is_authoritative_over_changed_recent_repair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research"
    universe = _universe(tmp_path)
    initial = _builder(
        root,
        EventComparisonFetcher({"TEST0": (0.1, 1.0)}),
    ).build("initial", universe)
    full_checkpoint_files = _checkpoint_files(root, PeerGroup.US_STOCK)
    assert len(full_checkpoint_files) == 1
    _activate(root, initial)

    fetcher = RepairedDividendFetcher()
    updated = _builder(root, fetcher).build("incremental", universe)

    assert fetcher.full_calls(PeerGroup.US_STOCK) == [("TEST0",)]
    assert updated.failed == []
    assert updated.manifest["corporate_action_refetches"] == ["US_STOCK:TEST0"]
    bars = (
        pl.scan_parquet(
            str(updated.staging_path / "bars" / "peer_group=US_STOCK" / "year=*" / "bars.parquet")
        )
        .filter(pl.col("asset_id") == "US_STOCK:TEST0")
        .collect()
    )
    dividends = bars.filter(pl.col("dividend") != 0.0).get_column("dividend").to_list()
    assert dividends == [0.1]
    assert 10.0 not in dividends
    assert bars.get_column("adjusted_close").unique().to_list() == [77.0]
