import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import polars as pl
from quant_core.enums import DataStatus, PeerGroup, ResearchCollectionMode
from quant_core.providers import YFINANCE_PROVIDER_VERSION, UniverseAsset

from quant_api.research_store import ResearchSnapshotStore, file_sha256
from quant_api.universe import UniverseSnapshot

ProgressCallback = Callable[[str, int, int, list[dict[str, Any]]], None]
PRICE_PIPELINE_VERSION = "price-pipeline-v2.0.0"


def determine_collection_mode(
    current: dict[str, Any] | None,
    *,
    force_full: bool = False,
) -> ResearchCollectionMode:
    if (
        force_full
        or current is None
        or current.get("price_pipeline_version") != PRICE_PIPELINE_VERSION
    ):
        return ResearchCollectionMode.FULL
    return ResearchCollectionMode.INCREMENTAL


class PriceFetcher(Protocol):
    def fetch(self, assets: list[UniverseAsset], start: date, end: date) -> pl.DataFrame: ...


@dataclass
class PriceBuildResult:
    staging_path: Path
    data_version: str
    manifest: dict[str, Any]
    failed: list[dict[str, Any]]


def years_ago(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _asset_id(asset: UniverseAsset) -> str:
    return f"{asset.peer_group.value}:{asset.ticker}"


def _chunks(values: list[UniverseAsset], size: int) -> list[list[UniverseAsset]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _checkpoint_key(assets: list[UniverseAsset], start: date, end: date) -> str:
    tickers = ",".join(sorted(asset.ticker for asset in assets))
    payload = f"{PRICE_PIPELINE_VERSION}|{YFINANCE_PROVIDER_VERSION}|{tickers}|{start}|{end}"
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


class PriceSnapshotBuilder:
    def __init__(
        self,
        *,
        store: ResearchSnapshotStore,
        fetcher: PriceFetcher,
        history_years: int = 10,
        batch_size: int = 20,
        max_retries: int = 3,
        minimum_group_assets: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.history_years = history_years
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.minimum_group_assets = minimum_group_assets
        self.clock = clock
        self.sleeper = sleeper

    def build(
        self,
        run_id: str,
        universe: UniverseSnapshot,
        progress: ProgressCallback | None = None,
        *,
        force_full: bool = False,
    ) -> PriceBuildResult:
        now = self.clock()
        end = now.date() + timedelta(days=1)
        current = self.store.current_manifest()
        collection_mode = determine_collection_mode(current, force_full=force_full)
        initial = collection_mode is ResearchCollectionMode.FULL
        full_start = years_ago(end, self.history_years)
        incremental_start = self._incremental_start(current, end)
        staging = self.store.create_staging(run_id)
        if not initial:
            self.store.clone_current_bars(staging)
        (staging / "universe.csv").write_bytes(universe.path.read_bytes())

        supported = [asset for asset in universe.assets if asset.is_supported]
        previous_assets = self._previous_asset_ids() if not initial else set()
        jobs: list[tuple[PeerGroup, list[UniverseAsset], date, date]] = []
        for group in PeerGroup:
            group_assets = [asset for asset in supported if asset.peer_group is group]
            if initial:
                jobs.extend(
                    (group, chunk, full_start, end)
                    for chunk in _chunks(group_assets, self.batch_size)
                )
                continue
            existing = [asset for asset in group_assets if _asset_id(asset) in previous_assets]
            new = [asset for asset in group_assets if _asset_id(asset) not in previous_assets]
            jobs.extend(
                (group, chunk, incremental_start, end)
                for chunk in _chunks(existing, self.batch_size)
            )
            jobs.extend((group, chunk, full_start, end) for chunk in _chunks(new, self.batch_size))

        total = len(jobs)
        completed = 0
        failed: list[dict[str, Any]] = []
        update_files: list[Path] = []
        refreshed_for_actions: set[str] = set()
        for group, assets, start, job_end in jobs:
            checkpoint = self._checkpoint_file(universe.version, group, assets, start, job_end)
            if checkpoint.is_file():
                update_files.append(checkpoint)
                frame = pl.read_parquet(checkpoint)
                completed += 1
                if progress:
                    progress("DOWNLOAD", completed, total, failed)
            else:
                frame, failures = self._fetch_batch(assets, start, job_end)
                failed.extend(failures)
                if frame.height:
                    self._write_checkpoint(checkpoint, frame)
                    update_files.append(checkpoint)
                completed += 1
                if progress:
                    progress("DOWNLOAD", completed, total, failed)

            if start > full_start and frame.height:
                action_assets = self._assets_with_corporate_actions(frame, assets)
                if action_assets:
                    full_checkpoint = self._checkpoint_file(
                        universe.version,
                        group,
                        action_assets,
                        full_start,
                        job_end,
                    )
                    if full_checkpoint.is_file():
                        full_frame = pl.read_parquet(full_checkpoint)
                        full_failures: list[dict[str, Any]] = []
                    else:
                        full_frame, full_failures = self._fetch_batch(
                            action_assets, full_start, job_end
                        )
                        if full_frame.height:
                            self._write_checkpoint(full_checkpoint, full_frame)
                    failed.extend(full_failures)
                    successful = set(
                        full_frame.get_column("asset_id").unique().to_list()
                    ) if full_frame.height else set()
                    if successful:
                        refreshed_for_actions.update(str(value) for value in successful)
                        update_files.append(full_checkpoint)

        if progress:
            progress("MATERIALIZE", completed, total, failed)
        self._materialize(staging, update_files, refreshed_for_actions)
        coverage = self._coverage(staging, universe.assets)
        deficient = [
            group.value
            for group in PeerGroup
            if coverage.get(group.value, {}).get("ready_assets", 0) < self.minimum_group_assets
        ]
        if deficient:
            raise RuntimeError(
                "비교군별 최소 데이터 종목 수를 충족하지 못했습니다: " + ", ".join(deficient)
            )

        digest = self._dataset_digest(staging)
        data_version = f"yf-{now:%Y%m%dT%H%M%SZ}-{digest[:12]}"
        manifest: dict[str, Any] = {
            "data_version": data_version,
            "data_source": "YFINANCE",
            "price_pipeline_version": PRICE_PIPELINE_VERSION,
            "provider_version": YFINANCE_PROVIDER_VERSION,
            "universe_version": universe.version,
            "created_at": now.isoformat(),
            "history_start": full_start.isoformat(),
            "requested_end": end.isoformat(),
            "mode": collection_mode.value,
            "coverage": coverage,
            "failed_tickers": failed,
            "corporate_action_refetches": sorted(refreshed_for_actions),
            "bars_sha256": digest,
        }
        return PriceBuildResult(staging, data_version, manifest, failed)

    def refresh_assets(
        self,
        result: PriceBuildResult,
        universe: UniverseSnapshot,
        assets: list[UniverseAsset],
    ) -> list[dict[str, Any]]:
        if not assets:
            return []
        start = date.fromisoformat(str(result.manifest["history_start"]))
        end = date.fromisoformat(str(result.manifest["requested_end"]))
        update_files: list[Path] = []
        successful_ids: set[str] = set()
        failures: list[dict[str, Any]] = []
        for chunk in _chunks(assets, self.batch_size):
            frame, chunk_failures = self._fetch_batch(chunk, start, end)
            failures.extend(chunk_failures)
            if frame.is_empty():
                continue
            checkpoint = self._checkpoint_file(
                universe.version, chunk[0].peer_group, chunk, start, end
            )
            self._write_checkpoint(checkpoint, frame)
            update_files.append(checkpoint)
            successful_ids.update(str(value) for value in frame["asset_id"].unique().to_list())
        if update_files:
            self._materialize(result.staging_path, update_files, successful_ids)
        result.failed.extend(failures)
        result.manifest["failed_tickers"] = result.failed
        result.manifest["quality_refetches"] = sorted(successful_ids)
        self.finalize(result, universe)
        return failures

    def finalize(self, result: PriceBuildResult, universe: UniverseSnapshot) -> None:
        coverage = self._coverage(result.staging_path, universe.assets)
        deficient = [
            group.value
            for group in PeerGroup
            if coverage.get(group.value, {}).get("ready_assets", 0) < self.minimum_group_assets
        ]
        if deficient:
            raise RuntimeError(
                "비교군별 최소 데이터 종목 수를 충족하지 못했습니다: " + ", ".join(deficient)
            )
        digest = self._dataset_digest(result.staging_path)
        created_at = datetime.fromisoformat(str(result.manifest["created_at"]))
        result.data_version = f"yf-{created_at:%Y%m%dT%H%M%SZ}-{digest[:12]}"
        result.manifest.update(
            data_version=result.data_version,
            coverage=coverage,
            bars_sha256=digest,
        )

    def _incremental_start(self, current: dict[str, Any] | None, end: date) -> date:
        if current is None:
            return years_ago(end, self.history_years)
        dates = [
            date.fromisoformat(item["as_of"])
            for item in current.get("coverage", {}).values()
            if item.get("as_of")
        ]
        return (min(dates) if dates else end) - timedelta(days=45)

    def _previous_asset_ids(self) -> set[str]:
        try:
            return set(self.store.universe().get_column("asset_id").to_list())
        except (FileNotFoundError, RuntimeError):
            return set()

    def _checkpoint_file(
        self,
        universe_version: str,
        group: PeerGroup,
        assets: list[UniverseAsset],
        start: date,
        end: date,
    ) -> Path:
        key = _checkpoint_key(assets, start, end)
        return self.store.checkpoints_root / universe_version / group.value / f"{key}.parquet"

    def _write_checkpoint(self, path: Path, frame: pl.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp.parquet")
        frame.write_parquet(temp, compression="zstd")
        os.replace(temp, path)

    def _assets_with_corporate_actions(
        self, frame: pl.DataFrame, assets: list[UniverseAsset]
    ) -> list[UniverseAsset]:
        if not {"split_ratio", "dividend"}.issubset(frame.columns):
            return []
        affected = set(
            frame.filter(
                (pl.col("split_ratio").fill_null(1.0) != 1.0)
                | (pl.col("dividend").fill_null(0.0) != 0.0)
            )
            .get_column("asset_id")
            .unique()
            .to_list()
        )
        return [asset for asset in assets if _asset_id(asset) in affected]

    def _fetch_batch(
        self, assets: list[UniverseAsset], start: date, end: date
    ) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
        remaining = assets
        frames: list[pl.DataFrame] = []
        last_error: str | None = None
        for attempt in range(self.max_retries):
            if not remaining:
                break
            try:
                frame = self.fetcher.fetch(remaining, start, end)
                if frame.height:
                    frames.append(frame)
                    received = set(frame.get_column("symbol").unique().to_list())
                    remaining = [asset for asset in remaining if asset.ticker not in received]
                else:
                    last_error = "가격 응답이 비어 있습니다."
            except Exception as error:
                last_error = str(error)
            if remaining and attempt + 1 < self.max_retries:
                self.sleeper(float(2**attempt))
        failures = [
            {
                "ticker": asset.ticker,
                "peer_group": asset.peer_group.value,
                "status": DataStatus.DOWNLOAD_FAILED.value,
                "reason": last_error or "가격 데이터가 없습니다.",
            }
            for asset in remaining
        ]
        if not frames:
            return pl.DataFrame(), failures
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["asset_id", "date"], keep="last")
            .sort(["date", "asset_id"]),
            failures,
        )

    def _materialize(
        self,
        staging: Path,
        update_files: list[Path],
        replace_asset_ids: set[str] | None = None,
    ) -> None:
        if replace_asset_ids:
            self._remove_assets(staging, replace_asset_ids)
        updates: dict[tuple[str, int], list[pl.DataFrame]] = {}
        for path in update_files:
            frame = pl.read_parquet(path).with_columns(pl.col("date").cast(pl.Date))
            for (group, year), part in frame.with_columns(
                pl.col("date").dt.year().alias("_year")
            ).partition_by(["peer_group", "_year"], as_dict=True).items():
                updates.setdefault((str(group), int(year)), []).append(part.drop("_year"))
        for (group, year), parts in updates.items():
            path = (
                staging
                / "bars"
                / f"peer_group={group}"
                / f"year={year}"
                / "bars.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            frames = ([pl.read_parquet(path)] if path.is_file() else []) + parts
            merged = (
                pl.concat(frames, how="diagonal_relaxed")
                .unique(subset=["asset_id", "date"], keep="last", maintain_order=True)
                .sort(["date", "asset_id"])
            )
            temp = path.with_suffix(".tmp.parquet")
            merged.write_parquet(temp, compression="zstd", statistics=True)
            os.replace(temp, path)

    def _remove_assets(self, staging: Path, asset_ids: set[str]) -> None:
        for path in (staging / "bars").rglob("*.parquet"):
            frame = pl.read_parquet(path).filter(~pl.col("asset_id").is_in(asset_ids))
            if frame.is_empty():
                path.unlink()
                continue
            temp = path.with_suffix(".tmp.parquet")
            frame.write_parquet(temp, compression="zstd", statistics=True)
            os.replace(temp, path)

    def _coverage(
        self, staging: Path, universe: list[UniverseAsset]
    ) -> dict[str, dict[str, Any]]:
        universe_by_group = {
            group: [asset for asset in universe if asset.peer_group is group] for group in PeerGroup
        }
        coverage: dict[str, dict[str, Any]] = {}
        for group in PeerGroup:
            paths = list((staging / "bars" / f"peer_group={group.value}").rglob("*.parquet"))
            if paths:
                summary = (
                    pl.scan_parquet([str(path) for path in paths])
                    .group_by("asset_id")
                    .agg(pl.len().alias("sessions"), pl.col("date").max().alias("as_of"))
                    .collect()
                )
                ready = summary.filter(pl.col("sessions") >= 253)
                as_of = cast(date | None, summary.get_column("as_of").max())
                ready_count = ready.height
            else:
                as_of = None
                ready_count = 0
            coverage[group.value] = {
                "listed_assets": len(universe_by_group[group]),
                "supported_assets": sum(asset.is_supported for asset in universe_by_group[group]),
                "ready_assets": ready_count,
                "as_of": as_of.isoformat() if as_of else None,
            }
        return coverage

    def _dataset_digest(self, staging: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted((staging / "bars").rglob("*.parquet")):
            digest.update(str(path.relative_to(staging)).encode())
            digest.update(file_sha256(path).encode())
        return digest.hexdigest()


def manifest_json(result: PriceBuildResult) -> str:
    payload = {**result.manifest, "failed_tickers": result.failed}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
