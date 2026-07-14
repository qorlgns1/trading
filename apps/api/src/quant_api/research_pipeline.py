import hashlib
import json
import math
import os
import shutil
import threading
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import polars as pl
from quant_core.enums import DataStatus, PeerGroup, ResearchCollectionMode
from quant_core.providers import YFINANCE_PROVIDER_VERSION, UniverseAsset

from quant_api.research_store import ResearchSnapshotStore, file_sha256
from quant_api.universe import UniverseSnapshot

ProgressCallback = Callable[[str, int, int, list[dict[str, Any]]], None]
PRICE_PIPELINE_VERSION = "price-pipeline-v2.0.0"
PRICE_CHECKPOINT_COLUMNS = frozenset(
    {
        "date",
        "asset_id",
        "symbol",
        "name",
        "peer_group",
        "currency",
        "open",
        "close",
        "adjusted_close",
        "volume",
        "split_ratio",
        "dividend",
        "is_suspended",
        "is_supported",
        "benchmark_close",
        "fx_krw_per_usd",
        "delisted",
        "recovery_value",
        "provider_repaired",
    }
)


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


@dataclass(frozen=True)
class AssetHistoryCoverage:
    first_date: date
    last_date: date
    rows: int


@dataclass(frozen=True)
class PriceDownloadJob:
    index: int
    phase: str
    group: PeerGroup
    assets: list[UniverseAsset]
    start: date
    end: date
    expected_history: dict[str, AssetHistoryCoverage] | None = None
    bypass_checkpoint: bool = False


@dataclass
class PriceDownloadResult:
    frame: pl.DataFrame
    failures: list[dict[str, Any]]
    data_path: Path | None
    checkpoint_hit: bool
    rate_limited: bool


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
    request_assets = sorted(
        (
            _asset_id(asset),
            asset.ticker,
            asset.peer_group.value,
            asset.benchmark_ticker,
        )
        for asset in assets
    )
    payload = json.dumps(
        {
            "pipeline": PRICE_PIPELINE_VERSION,
            "provider": YFINANCE_PROVIDER_VERSION,
            "assets": request_assets,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:20]


def _legacy_checkpoint_key(assets: list[UniverseAsset], start: date, end: date) -> str:
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
        batch_size: int = 40,
        download_workers: int = 3,
        max_retries: int = 3,
        minimum_group_assets: int = 30,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.fetcher = fetcher
        self.history_years = history_years
        self.batch_size = batch_size
        self.download_workers = max(1, min(download_workers, 4))
        self.max_retries = max_retries
        self.minimum_group_assets = minimum_group_assets
        self.clock = clock
        self.sleeper = sleeper
        self._rate_limit_detected = threading.Event()
        self._rate_limit_state_lock = threading.Lock()
        self._legacy_universe_lock = threading.Lock()
        self._legacy_universe_benchmarks: dict[str, dict[str, str] | None] = {}

    def build(
        self,
        run_id: str,
        universe: UniverseSnapshot,
        progress: ProgressCallback | None = None,
        *,
        force_full: bool = False,
    ) -> PriceBuildResult:
        with self._rate_limit_state_lock:
            self._rate_limit_detected.clear()
        started_at = time.monotonic()
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
        supported_asset_ids = {_asset_id(asset) for asset in supported}
        stale_asset_ids = previous_assets - supported_asset_ids
        if stale_asset_ids:
            self._remove_assets(staging, stale_asset_ids)
        job_specs: list[tuple[PeerGroup, list[UniverseAsset], date, date]] = []
        for group in PeerGroup:
            group_assets = [asset for asset in supported if asset.peer_group is group]
            if initial:
                job_specs.extend(
                    (group, chunk, full_start, end)
                    for chunk in _chunks(group_assets, self.batch_size)
                )
                continue
            existing = [asset for asset in group_assets if _asset_id(asset) in previous_assets]
            new = [asset for asset in group_assets if _asset_id(asset) not in previous_assets]
            job_specs.extend(
                (group, chunk, incremental_start, end)
                for chunk in _chunks(existing, self.batch_size)
            )
            job_specs.extend(
                (group, chunk, full_start, end) for chunk in _chunks(new, self.batch_size)
            )

        jobs = [
            PriceDownloadJob(index, "DOWNLOAD", group, assets, start, job_end)
            for index, (group, assets, start, job_end) in enumerate(job_specs)
        ]
        total = len(jobs)
        completed = 0
        failed: list[dict[str, Any]] = []
        primary_update_files: list[Path] = []
        action_update_files: list[Path] = []
        checkpoint_hits = 0
        action_candidates: set[str] = set()
        action_events_detected: set[tuple[str, date, float, float]] = set()
        unchanged_action_assets: set[str] = set()
        changed_action_assets: set[str] = set()
        refreshed_for_actions: set[str] = set()
        previous_action_signatures = (
            self._previous_action_signatures(incremental_start) if not initial else {}
        )
        updates_root = staging / ".updates"

        def handle_primary(job: PriceDownloadJob, result: PriceDownloadResult) -> None:
            nonlocal checkpoint_hits, completed
            failed.extend(result.failures)
            if result.data_path is not None:
                primary_update_files.append(result.data_path)
            checkpoint_hits += int(result.checkpoint_hit)

            if job.start > full_start and result.frame.height:
                received_ids = {
                    str(value) for value in result.frame.get_column("asset_id").unique().to_list()
                }
                incoming = self._corporate_action_signatures(result.frame)
                action_events_detected.update(
                    (asset_id, action_date, split_ratio, dividend)
                    for asset_id, signature in incoming.items()
                    for action_date, split_ratio, dividend in signature
                )
                for asset in job.assets:
                    asset_id = _asset_id(asset)
                    if asset_id not in received_ids:
                        continue
                    previous_signature = previous_action_signatures.get(asset_id, frozenset())
                    incoming_signature = incoming.get(asset_id, frozenset())
                    if not previous_signature and not incoming_signature:
                        continue
                    action_candidates.add(asset_id)
                    if previous_signature == incoming_signature:
                        unchanged_action_assets.add(asset_id)
                    else:
                        changed_action_assets.add(asset_id)

            completed += 1
            if progress:
                progress("DOWNLOAD", completed, total, self._sorted_failures(failed))

        rate_limit_fallback = self._run_download_jobs(
            jobs,
            updates_root,
            handle_primary,
        )

        action_specs: list[tuple[PeerGroup, list[UniverseAsset], date, date]] = []
        for group in PeerGroup:
            group_assets = [
                asset
                for asset in supported
                if asset.peer_group is group and _asset_id(asset) in changed_action_assets
            ]
            action_specs.extend(
                (group, chunk, full_start, end) for chunk in _chunks(group_assets, self.batch_size)
            )
        action_history = self._asset_history_coverage(
            staging,
            changed_action_assets,
            full_start,
            end,
        )
        action_jobs = [
            PriceDownloadJob(
                index,
                "REFRESH_ACTIONS",
                group,
                assets,
                start,
                job_end,
                expected_history=action_history,
                bypass_checkpoint=True,
            )
            for index, (group, assets, start, job_end) in enumerate(action_specs)
        ]
        action_completed = 0
        if action_jobs and progress:
            progress("REFRESH_ACTIONS", 0, len(action_jobs), self._sorted_failures(failed))

        def handle_action(job: PriceDownloadJob, result: PriceDownloadResult) -> None:
            nonlocal action_completed, checkpoint_hits
            del job
            failed.extend(result.failures)
            checkpoint_hits += int(result.checkpoint_hit)
            if result.data_path is not None:
                action_update_files.append(result.data_path)
            if result.frame.height:
                refreshed_for_actions.update(
                    str(value) for value in result.frame.get_column("asset_id").unique().to_list()
                )
            action_completed += 1
            if progress:
                progress(
                    "REFRESH_ACTIONS",
                    action_completed,
                    len(action_jobs),
                    self._sorted_failures(failed),
                )

        if action_jobs:
            rate_limit_fallback = (
                self._run_download_jobs(
                    action_jobs,
                    updates_root,
                    handle_action,
                )
                or rate_limit_fallback
            )

        if progress:
            progress("MATERIALIZE", completed, total, self._sorted_failures(failed))
        self._materialize(
            staging,
            primary_update_files + action_update_files,
            refreshed_for_actions,
        )
        self._trim_date_range(staging, full_start, end)
        shutil.rmtree(updates_root, ignore_errors=True)
        failed = self._sorted_failures(failed)
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
            "download_stats": {
                "batch_size": self.batch_size,
                "workers_configured": self.download_workers,
                "primary_batches": len(jobs),
                "action_refresh_batches": len(action_jobs),
                "checkpoint_hits": checkpoint_hits,
                "incremental_assets": sum(
                    len(job.assets) for job in jobs if job.start > full_start
                ),
                "new_assets_full_history": sum(
                    len(job.assets) for job in jobs if not initial and job.start == full_start
                ),
                "stale_assets_removed": len(stale_asset_ids),
                "corporate_action_candidates": len(action_candidates),
                "corporate_action_events_detected": len(action_events_detected),
                "corporate_action_changed_assets": len(changed_action_assets),
                "corporate_action_unchanged_skips": len(unchanged_action_assets),
                "corporate_action_refetches": len(refreshed_for_actions),
                "rate_limit_detected": self._rate_limit_detected.is_set(),
                "rate_limit_fallback": rate_limit_fallback,
                "elapsed_seconds": round(time.monotonic() - started_at, 3),
            },
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
        job_specs = [
            (group, chunk)
            for group in PeerGroup
            for chunk in _chunks(
                [asset for asset in assets if asset.peer_group is group], self.batch_size
            )
        ]
        expected_history = self._asset_history_coverage(
            result.staging_path,
            {_asset_id(asset) for asset in assets},
            start,
            end,
        )
        jobs = [
            PriceDownloadJob(
                index,
                "REPAIR",
                group,
                chunk,
                start,
                end,
                expected_history=expected_history,
                bypass_checkpoint=True,
            )
            for index, (group, chunk) in enumerate(job_specs)
        ]
        checkpoint_hits = 0
        updates_root = result.staging_path / ".updates-quality"

        def handle_result(job: PriceDownloadJob, download: PriceDownloadResult) -> None:
            nonlocal checkpoint_hits
            del job
            failures.extend(download.failures)
            checkpoint_hits += int(download.checkpoint_hit)
            if download.data_path is not None:
                update_files.append(download.data_path)
            if download.frame.height:
                successful_ids.update(
                    str(value) for value in download.frame.get_column("asset_id").unique().to_list()
                )

        rate_limit_fallback = self._run_download_jobs(
            jobs,
            updates_root,
            handle_result,
        )
        if update_files:
            self._materialize(result.staging_path, update_files, successful_ids)
        shutil.rmtree(updates_root, ignore_errors=True)
        failures = self._sorted_failures(failures)
        result.failed.extend(failures)
        result.failed = self._sorted_failures(result.failed)
        result.manifest["failed_tickers"] = result.failed
        result.manifest["quality_refetches"] = sorted(successful_ids)
        stats = result.manifest.setdefault("download_stats", {})
        stats["quality_refresh_batches"] = len(jobs)
        stats["checkpoint_hits"] = int(stats.get("checkpoint_hits", 0)) + checkpoint_hits
        stats["rate_limit_detected"] = bool(
            stats.get("rate_limit_detected", False) or self._rate_limit_detected.is_set()
        )
        stats["rate_limit_fallback"] = bool(
            stats.get("rate_limit_fallback", False) or rate_limit_fallback
        )
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
            universe = self.store.universe()
            if "is_supported" in universe.columns:
                universe = universe.filter(pl.col("is_supported").fill_null(False))
            return set(universe.get_column("asset_id").to_list())
        except (FileNotFoundError, RuntimeError):
            return set()

    def _asset_history_coverage(
        self,
        staging: Path,
        asset_ids: set[str],
        start: date,
        end: date,
    ) -> dict[str, AssetHistoryCoverage]:
        paths = sorted((staging / "bars").rglob("*.parquet"))
        if not paths or not asset_ids:
            return {}
        summary = (
            pl.scan_parquet([str(path) for path in paths])
            .filter(
                pl.col("asset_id").is_in(asset_ids)
                & (pl.col("date") >= pl.lit(start))
                & (pl.col("date") < pl.lit(end))
            )
            .group_by("asset_id")
            .agg(
                pl.col("date").min().alias("first_date"),
                pl.col("date").max().alias("last_date"),
                pl.col("date").n_unique().alias("rows"),
            )
            .collect()
        )
        return {
            str(row["asset_id"]): AssetHistoryCoverage(
                first_date=cast(date, row["first_date"]),
                last_date=cast(date, row["last_date"]),
                rows=int(row["rows"]),
            )
            for row in summary.iter_rows(named=True)
        }

    def _checkpoint_file(
        self,
        group: PeerGroup,
        assets: list[UniverseAsset],
        start: date,
        end: date,
    ) -> Path:
        key = _checkpoint_key(assets, start, end)
        return self.store.checkpoints_root / group.value / f"{key}.parquet"

    def _legacy_checkpoint_files(
        self,
        group: PeerGroup,
        assets: list[UniverseAsset],
        start: date,
        end: date,
    ) -> list[Path]:
        key = _legacy_checkpoint_key(assets, start, end)
        root = self.store.checkpoints_root.resolve()
        candidates: list[Path] = []
        for path in sorted(
            self.store.checkpoints_root.glob(f"*/{group.value}/{key}.parquet"),
            reverse=True,
        ):
            try:
                relative = path.relative_to(self.store.checkpoints_root)
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (FileNotFoundError, OSError, ValueError):
                continue
            if len(relative.parts) != 3 or path.is_symlink():
                continue
            candidates.append(path)
        return candidates

    def _legacy_benchmarks(self, universe_version: str) -> dict[str, str] | None:
        with self._legacy_universe_lock:
            if universe_version in self._legacy_universe_benchmarks:
                return self._legacy_universe_benchmarks[universe_version]
            path = self.store.root / "universes" / universe_version / "universe.csv"
            try:
                frame = pl.read_csv(path, columns=["asset_id", "benchmark_ticker"])
                valid = (
                    frame.height > 0
                    and frame.get_column("asset_id").null_count() == 0
                    and frame.get_column("benchmark_ticker").null_count() == 0
                    and frame.get_column("asset_id").n_unique() == frame.height
                )
                benchmarks = (
                    {
                        str(row["asset_id"]): str(row["benchmark_ticker"])
                        for row in frame.iter_rows(named=True)
                    }
                    if valid
                    else None
                )
            except (FileNotFoundError, OSError, pl.exceptions.PolarsError):
                benchmarks = None
            self._legacy_universe_benchmarks[universe_version] = benchmarks
            return benchmarks

    def _legacy_benchmark_matches(self, path: Path, job: PriceDownloadJob) -> bool:
        try:
            universe_version = path.relative_to(self.store.checkpoints_root).parts[0]
        except (IndexError, ValueError):
            return False
        benchmarks = self._legacy_benchmarks(universe_version)
        return benchmarks is not None and all(
            benchmarks.get(_asset_id(asset)) == asset.benchmark_ticker
            for asset in job.assets
        )

    def _read_valid_checkpoint(
        self,
        path: Path,
        job: PriceDownloadJob,
    ) -> pl.DataFrame | None:
        if not path.is_file():
            return None
        try:
            frame = pl.read_parquet(path)
        except (OSError, pl.exceptions.PolarsError):
            return None
        if frame.is_empty() or not PRICE_CHECKPOINT_COLUMNS.issubset(frame.columns):
            return None
        try:
            frame = frame.with_columns(pl.col("date").cast(pl.Date, strict=False))
        except pl.exceptions.PolarsError:
            return None
        if frame.get_column("date").null_count():
            return None
        expected_identity = {
            (_asset_id(asset), asset.ticker, asset.peer_group.value) for asset in job.assets
        }
        received_identity = {
            (str(asset_id), str(symbol), str(peer_group))
            for asset_id, symbol, peer_group in frame.select(
                "asset_id", "symbol", "peer_group"
            )
            .unique()
            .iter_rows()
        }
        if received_identity != expected_identity:
            return None
        if frame.select("asset_id", "date").unique().height != frame.height:
            return None
        first_date, last_date = frame.select(
            pl.col("date").min().alias("first_date"),
            pl.col("date").max().alias("last_date"),
        ).row(0)
        if first_date < job.start or last_date >= job.end:
            return None
        validated, failures = self._validate_expected_history(frame, job)
        return None if failures else validated

    @staticmethod
    def _rehydrate_checkpoint(
        frame: pl.DataFrame,
        assets: list[UniverseAsset],
    ) -> pl.DataFrame:
        metadata = pl.DataFrame(
            {
                "asset_id": [_asset_id(asset) for asset in assets],
                "name": [asset.name for asset in assets],
                "currency": [asset.currency for asset in assets],
                "is_supported": [asset.is_supported for asset in assets],
            }
        )
        columns = frame.columns
        return (
            frame.drop("name", "currency", "is_supported")
            .join(metadata, on="asset_id", how="left", validate="m:1")
            .select(columns)
        )

    @staticmethod
    def _checkpoint_temp(path: Path) -> Path:
        return path.with_name(f".{path.name}.{uuid4().hex}.tmp.parquet")

    def _durable_checkpoint_temp(self, path: Path, frame: pl.DataFrame) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._checkpoint_temp(path)
        try:
            frame.write_parquet(temp, compression="zstd")
            with temp.open("rb") as stream:
                os.fsync(stream.fileno())
            return temp
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _write_checkpoint(self, path: Path, frame: pl.DataFrame) -> None:
        temp = self._durable_checkpoint_temp(path, frame)
        try:
            os.replace(temp, path)
            self._fsync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)

    def _promote_checkpoint(self, path: Path, frame: pl.DataFrame) -> bool:
        temp = self._durable_checkpoint_temp(path, frame)
        try:
            try:
                os.link(temp, path)
            except FileExistsError:
                return False
            self._fsync_directory(path.parent)
            return True
        finally:
            temp.unlink(missing_ok=True)

    def _previous_action_signatures(
        self, start: date
    ) -> dict[str, frozenset[tuple[date, float, float]]]:
        try:
            frame = (
                self.store.scan_bars(start=start)
                .select("asset_id", "date", "split_ratio", "dividend")
                .filter(
                    (pl.col("split_ratio").fill_null(1.0) != 1.0)
                    | (pl.col("dividend").fill_null(0.0) != 0.0)
                )
                .collect()
            )
        except (FileNotFoundError, RuntimeError):
            return {}
        return self._corporate_action_signatures(frame)

    def _corporate_action_signatures(
        self, frame: pl.DataFrame
    ) -> dict[str, frozenset[tuple[date, float, float]]]:
        required = {"asset_id", "date", "split_ratio", "dividend"}
        if not required.issubset(frame.columns):
            return {}
        signatures: dict[str, set[tuple[date, float, float]]] = {}
        for row in frame.select(sorted(required)).iter_rows(named=True):
            split_ratio = self._action_value(row["split_ratio"], 1.0)
            dividend = self._action_value(row["dividend"], 0.0)
            if split_ratio == 1.0 and dividend == 0.0:
                continue
            action_date = row["date"]
            if isinstance(action_date, datetime):
                action_date = action_date.date()
            elif not isinstance(action_date, date):
                action_date = date.fromisoformat(str(action_date))
            signatures.setdefault(str(row["asset_id"]), set()).add(
                (action_date, split_ratio, dividend)
            )
        return {asset_id: frozenset(values) for asset_id, values in signatures.items()}

    @staticmethod
    def _action_value(value: object, default: float) -> float:
        if value is None:
            return default
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return default
        return round(number, 10) if math.isfinite(number) else default

    @staticmethod
    def _sorted_failures(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            failures,
            key=lambda item: (
                str(item.get("peer_group", "")),
                str(item.get("ticker", "")),
                str(item.get("reason", "")),
            ),
        )

    def _run_download_jobs(
        self,
        jobs: list[PriceDownloadJob],
        updates_root: Path,
        on_result: Callable[[PriceDownloadJob, PriceDownloadResult], None],
    ) -> bool:
        if not jobs:
            return False

        def execute(job: PriceDownloadJob) -> PriceDownloadResult:
            return self._execute_download_job(job, updates_root)

        rate_was_already_detected = self._rate_limit_detected.is_set()
        if self.download_workers == 1 or rate_was_already_detected:
            if rate_was_already_detected:
                self._enable_fetcher_serial_mode()
            fallback_used = rate_was_already_detected
            rate_limited = rate_was_already_detected
            for index, job in enumerate(jobs):
                result = execute(job)
                detected_now = result.rate_limited or self._rate_limit_detected.is_set()
                if detected_now and not rate_limited and index + 1 < len(jobs):
                    fallback_used = True
                rate_limited = rate_limited or detected_now
                on_result(job, result)
                if rate_limited:
                    self._enable_fetcher_serial_mode()
            return fallback_used

        rate_limited = False
        fallback_used = False
        next_index = 0
        with ThreadPoolExecutor(
            max_workers=self.download_workers,
            thread_name_prefix="research-download",
        ) as executor:
            futures: dict[Future[PriceDownloadResult], PriceDownloadJob] = {}

            def fill_slots() -> None:
                nonlocal next_index
                while next_index < len(jobs) and len(futures) < self.download_workers:
                    with self._rate_limit_state_lock:
                        if self._rate_limit_detected.is_set():
                            break
                        job = jobs[next_index]
                        next_index += 1
                        futures[executor.submit(execute, job)] = job

            fill_slots()
            while futures:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in sorted(done, key=lambda item: futures[item].index):
                    job = futures.pop(future)
                    result = future.result()
                    rate_limited = (
                        rate_limited or result.rate_limited or self._rate_limit_detected.is_set()
                    )
                    on_result(job, result)
                rate_limited = rate_limited or self._rate_limit_detected.is_set()
                if not rate_limited:
                    fill_slots()

        if rate_limited and next_index < len(jobs):
            fallback_used = True
            self._enable_fetcher_serial_mode()
            for job in jobs[next_index:]:
                result = execute(job)
                on_result(job, result)
        return fallback_used

    def _execute_download_job(
        self,
        job: PriceDownloadJob,
        updates_root: Path,
    ) -> PriceDownloadResult:
        checkpoint = self._checkpoint_file(job.group, job.assets, job.start, job.end)
        if not job.bypass_checkpoint:
            candidates = [checkpoint]
            if not checkpoint.is_file():
                candidates.extend(
                    self._legacy_checkpoint_files(
                        job.group,
                        job.assets,
                        job.start,
                        job.end,
                    )
                )
            for candidate in candidates:
                if (
                    candidate != checkpoint
                    and not self._legacy_benchmark_matches(candidate, job)
                ):
                    continue
                cached = self._read_valid_checkpoint(candidate, job)
                if cached is None:
                    continue
                cached = self._rehydrate_checkpoint(cached, job.assets)
                if candidate != checkpoint and not self._promote_checkpoint(
                    checkpoint, cached
                ):
                    winner = self._read_valid_checkpoint(checkpoint, job)
                    if winner is None:
                        break
                    cached = self._rehydrate_checkpoint(winner, job.assets)
                cache_update = (
                    updates_root / "CACHE" / job.phase / f"{job.index:06d}.parquet"
                )
                self._write_checkpoint(cache_update, cached)
                return PriceDownloadResult(
                    frame=cached,
                    failures=[],
                    data_path=cache_update,
                    checkpoint_hit=True,
                    rate_limited=False,
                )

        frame, failures, rate_limited = self._fetch_batch(job.assets, job.start, job.end)
        frame, history_failures = self._validate_expected_history(frame, job)
        failed_tickers = {str(item["ticker"]) for item in failures}
        failures.extend(
            failure for failure in history_failures if str(failure["ticker"]) not in failed_tickers
        )
        data_path: Path | None = None
        if frame.height:
            data_path = checkpoint
            if failures:
                data_path = updates_root / job.phase / f"{job.index:06d}.parquet"
            self._write_checkpoint(data_path, frame)
        return PriceDownloadResult(
            frame=frame,
            failures=failures,
            data_path=data_path,
            checkpoint_hit=False,
            rate_limited=rate_limited,
        )

    def _validate_expected_history(
        self,
        frame: pl.DataFrame,
        job: PriceDownloadJob,
    ) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
        if not job.expected_history:
            return frame, []
        if frame.is_empty():
            actual: dict[str, AssetHistoryCoverage] = {}
        else:
            summary = frame.group_by("asset_id").agg(
                pl.col("date").min().alias("first_date"),
                pl.col("date").max().alias("last_date"),
                pl.col("date").n_unique().alias("rows"),
            )
            actual = {
                str(row["asset_id"]): AssetHistoryCoverage(
                    first_date=cast(date, row["first_date"]),
                    last_date=cast(date, row["last_date"]),
                    rows=int(row["rows"]),
                )
                for row in summary.iter_rows(named=True)
            }
        invalid_ids: set[str] = set()
        failures: list[dict[str, Any]] = []
        for asset in job.assets:
            asset_id = _asset_id(asset)
            expected = job.expected_history.get(asset_id)
            if expected is None:
                continue
            received = actual.get(asset_id)
            allowed_missing = max(5, math.ceil(expected.rows * 0.01))
            complete = (
                received is not None
                and received.rows >= max(1, expected.rows - allowed_missing)
                and received.first_date <= expected.first_date + timedelta(days=7)
                and received.last_date >= expected.last_date - timedelta(days=7)
            )
            if complete:
                continue
            invalid_ids.add(asset_id)
            observed = (
                "응답 없음"
                if received is None
                else (
                    f"{received.rows}행 "
                    f"({received.first_date.isoformat()}~{received.last_date.isoformat()})"
                )
            )
            failures.append(
                {
                    "ticker": asset.ticker,
                    "peer_group": asset.peer_group.value,
                    "status": DataStatus.DOWNLOAD_FAILED.value,
                    "reason": (
                        "전체 이력 응답이 기존 범위를 충족하지 않습니다: "
                        f"기대 {expected.rows}행 "
                        f"({expected.first_date.isoformat()}~{expected.last_date.isoformat()}), "
                        f"수신 {observed}"
                    ),
                }
            )
        if invalid_ids and frame.height:
            frame = frame.filter(~pl.col("asset_id").is_in(invalid_ids))
        return frame, failures

    def _fetch_batch(
        self, assets: list[UniverseAsset], start: date, end: date
    ) -> tuple[pl.DataFrame, list[dict[str, Any]], bool]:
        remaining = assets
        frames: list[pl.DataFrame] = []
        last_error: str | None = None
        rate_limited = False
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
                detected = self._is_rate_limit_error(error)
                rate_limited = rate_limited or detected
                if detected:
                    self._mark_rate_limit_detected()
            signaled = self._consume_rate_limit_signal()
            rate_limited = rate_limited or signaled
            if signaled:
                self._mark_rate_limit_detected()
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
            return pl.DataFrame(), failures, rate_limited
        return (
            pl.concat(frames, how="diagonal_relaxed")
            .unique(subset=["asset_id", "date"], keep="last")
            .sort(["date", "asset_id"]),
            failures,
            rate_limited,
        )

    @staticmethod
    def _is_rate_limit_error(error: Exception) -> bool:
        current: BaseException | None = error
        while current is not None:
            message = str(current).lower()
            if (
                current.__class__.__name__ == "YFRateLimitError"
                or "rate limit" in message
                or "too many requests" in message
                or "http 429" in message
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    def _consume_rate_limit_signal(self) -> bool:
        consume = getattr(self.fetcher, "consume_rate_limit_signal", None)
        return bool(consume()) if callable(consume) else False

    def _enable_fetcher_serial_mode(self) -> None:
        enable = getattr(self.fetcher, "enable_serial_mode", None)
        if callable(enable):
            enable()

    def _mark_rate_limit_detected(self) -> None:
        with self._rate_limit_state_lock:
            self._rate_limit_detected.set()
        self._enable_fetcher_serial_mode()

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
            for (group, year), part in (
                frame.with_columns(pl.col("date").dt.year().alias("_year"))
                .partition_by(["peer_group", "_year"], as_dict=True)
                .items()
            ):
                updates.setdefault((str(group), int(year)), []).append(part.drop("_year"))
        for (group, year), parts in updates.items():
            path = staging / "bars" / f"peer_group={group}" / f"year={year}" / "bars.parquet"
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
            frame = pl.read_parquet(path)
            if not frame.get_column("asset_id").is_in(asset_ids).any():
                continue
            retained = frame.filter(~pl.col("asset_id").is_in(asset_ids))
            if retained.is_empty():
                path.unlink()
                continue
            temp = path.with_suffix(".tmp.parquet")
            retained.write_parquet(temp, compression="zstd", statistics=True)
            os.replace(temp, path)

    def _trim_date_range(self, staging: Path, start: date, end: date) -> None:
        for path in (staging / "bars").rglob("*.parquet"):
            try:
                year = int(path.parent.name.removeprefix("year="))
            except ValueError:
                year = start.year
            if year < start.year or year > end.year:
                path.unlink()
                continue
            if start.year < year < end.year:
                continue
            frame = pl.read_parquet(path)
            retained = frame.filter(
                (pl.col("date") >= pl.lit(start)) & (pl.col("date") < pl.lit(end))
            )
            if retained.height == frame.height:
                continue
            if retained.is_empty():
                path.unlink()
                continue
            temp = path.with_suffix(".tmp.parquet")
            retained.write_parquet(temp, compression="zstd", statistics=True)
            os.replace(temp, path)

    def _coverage(self, staging: Path, universe: list[UniverseAsset]) -> dict[str, dict[str, Any]]:
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
