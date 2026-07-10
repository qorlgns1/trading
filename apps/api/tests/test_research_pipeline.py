import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest
from quant_api.research_pipeline import PriceSnapshotBuilder
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
        if start.year >= 2025:
            return frame.with_columns(
                pl.when(pl.int_range(pl.len()) == pl.len() - 1)
                .then(0.5)
                .otherwise(pl.col("dividend"))
                .alias("dividend")
            )
        return frame.with_columns(pl.lit(90.0).alias("adjusted_close"))


def _builder(root: Path, fetcher: FakePriceFetcher) -> PriceSnapshotBuilder:
    return PriceSnapshotBuilder(
        store=ResearchSnapshotStore(root),
        fetcher=fetcher,
        batch_size=2,
        max_retries=2,
        minimum_group_assets=1,
        clock=lambda: datetime(2026, 7, 10, 8, tzinfo=UTC),
        sleeper=lambda _: None,
    )


def test_initial_price_build_is_partitioned_and_checkpoints_are_reused(tmp_path: Path) -> None:
    universe = _universe(tmp_path)
    fetcher = FakePriceFetcher()
    result = _builder(tmp_path / "research", fetcher).build("run-1", universe)

    assert fetcher.calls == 6
    assert len(list((result.staging_path / "bars").rglob("bars.parquet"))) >= 6
    assert all(
        result.manifest["coverage"][group.value]["ready_assets"] == 1 for group in PeerGroup
    )

    second_fetcher = FakePriceFetcher()
    second = _builder(tmp_path / "research", second_fetcher).build("run-2", universe)
    assert second_fetcher.calls == 0
    assert second.manifest["bars_sha256"] == result.manifest["bars_sha256"]


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
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(
        score_root / "latest.parquet"
    )
    pl.DataFrame({"asset_id": ["placeholder"]}).write_parquet(
        score_root / "history.parquet"
    )
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
            for path in (
                updated.staging_path / "bars" / "peer_group=US_STOCK"
            ).rglob("bars.parquet")
        ]
    ).filter(pl.col("asset_id") == asset_id)
    assert bars.get_column("adjusted_close").unique().to_list() == [90.0]
