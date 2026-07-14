import asyncio
import logging
import shutil
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
from quant_core.calendar import latest_completed_trading_date
from quant_core.enums import (
    DataSource,
    PeerGroup,
    ResearchCollectionMode,
    RunStatus,
    SnapshotState,
    SyncTrigger,
)
from quant_core.providers import YFinanceProvider

from quant_api.database import ResearchSyncRunModel
from quant_api.research import ResearchService, research_service
from quant_api.research_pipeline import (
    PRICE_PIPELINE_VERSION,
    PriceBuildResult,
    PriceSnapshotBuilder,
    determine_collection_mode,
)
from quant_api.research_quality import ResearchQualityValidator
from quant_api.research_repository import ResearchRepository
from quant_api.research_scoring import ResearchScorer
from quant_api.research_store import ResearchSnapshotStore
from quant_api.schemas import (
    PeerCoverage,
    ResearchStatusResponse,
    ResearchSyncResponse,
)
from quant_api.settings import Settings, get_settings
from quant_api.universe import ExchangeUniverseClient, UniverseSnapshot, build_universe_snapshot

logger = logging.getLogger(__name__)


class ResearchSyncManager:
    def __init__(
        self,
        *,
        settings: Settings,
        service: ResearchService,
        repository: ResearchRepository | None = None,
        store: ResearchSnapshotStore | None = None,
    ) -> None:
        self.settings = settings
        self.service = service
        self.repository = repository or ResearchRepository()
        self.store = store or ResearchSnapshotStore(settings.research_root)
        self.quality = ResearchQualityValidator(
            settings.research_root,
            minimum_group_assets=settings.research_minimum_group_assets,
        )
        self._request_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._scheduler: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        await self.repository.fail_interrupted_syncs()
        if self.settings.app_mode != "local_research" or not self.settings.research_auto_sync:
            return
        await self._launch_if_needed(SyncTrigger.STARTUP)
        self._scheduler = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.cancel()
        for task in list(self._tasks):
            task.cancel()
        pending = [task for task in [self._scheduler, *self._tasks] if task is not None]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    async def request(self, trigger: SyncTrigger) -> tuple[ResearchSyncRunModel, bool]:
        if self.settings.app_mode != "local_research":
            raise PermissionError("실데이터 동기화는 local_research 모드에서만 사용할 수 있습니다.")
        async with self._request_lock:
            active = await self.repository.active_sync()
            if active is not None:
                return active, True
            run = await self.repository.create_sync(trigger, self.collection_mode())
            self._launch(run.id)
            return run, False

    def _launch(self, run_id: str) -> None:
        task = asyncio.create_task(self.run(run_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _launch_if_needed(self, trigger: SyncTrigger) -> None:
        if not self.is_stale():
            return
        async with self._request_lock:
            if not self.is_stale():
                return
            active = await self.repository.active_sync()
            if active is None:
                run = await self.repository.create_sync(trigger, self.collection_mode())
                self._launch(run.id)

    def collection_mode(self) -> ResearchCollectionMode:
        return determine_collection_mode(self.store.current_manifest())

    async def _scheduler_loop(self) -> None:
        while True:
            await asyncio.sleep(self.settings.research_poll_seconds)
            await self._launch_if_needed(SyncTrigger.SCHEDULED)

    def is_stale(self, now: datetime | None = None) -> bool:
        manifest = self.store.current_manifest()
        if manifest is None:
            return True
        if manifest.get("price_pipeline_version") != PRICE_PIPELINE_VERSION:
            return True
        current = now or datetime.now(UTC)
        coverage = manifest.get("coverage", {})
        for group in PeerGroup:
            market = "US" if group in {PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF} else "KR"
            expected = latest_completed_trading_date(current, market)
            value = coverage.get(group.value, {}).get("as_of")
            if value is None or date.fromisoformat(value) < expected:
                return True
        return False

    async def run(self, run_id: str) -> None:
        loop = asyncio.get_running_loop()

        def progress(
            stage: str,
            completed: int,
            total: int,
            failed: list[dict[str, Any]],
        ) -> None:
            future = asyncio.run_coroutine_threadsafe(
                self.repository.update_sync(
                    run_id,
                    stage=stage,
                    completed_batches=completed,
                    total_batches=total,
                    failed_json=failed,
                ),
                loop,
            )
            future.result(timeout=30)

        try:
            await self.repository.update_sync(
                run_id,
                status=RunStatus.RUNNING.value,
                stage="UNIVERSE",
                error_message="",
            )
            universe, build, _manifest, final_path = await asyncio.to_thread(
                self._execute, run_id, progress
            )
            await self.repository.activate_snapshot(
                version=build.data_version,
                sources=universe.sources,
                counts=universe.counts,
                manifest_path=str(final_path / "manifest.json"),
            )
            await self.repository.update_sync(
                run_id,
                status=RunStatus.SUCCEEDED.value,
                stage="SUCCEEDED",
                universe_version=universe.version,
                data_version=build.data_version,
                failed_json=build.failed,
            )
            self.service.reload()
            try:
                from quant_api.forward import forward_service

                await forward_service.process_snapshot(build.data_version)
            except Exception:
                logger.exception(
                    "Forward processing failed after activating %s", build.data_version
                )
        except asyncio.CancelledError:
            await self.repository.update_sync(
                run_id,
                status=RunStatus.FAILED.value,
                stage="CANCELLED",
                error_message="동기화 작업이 중단되었습니다. 다시 실행하면 이어받습니다.",
            )
            raise
        except Exception as error:
            shutil.rmtree(self.store.snapshots_root / f".staging-{run_id}", ignore_errors=True)
            await self.repository.update_sync(
                run_id,
                status=RunStatus.FAILED.value,
                stage="FAILED",
                error_message=str(error),
            )

    def _execute(
        self,
        run_id: str,
        progress: Any,
    ) -> tuple[UniverseSnapshot, PriceBuildResult, dict[str, Any], Any]:
        previous_manifest = self.store.current_manifest()
        krx_id = (
            self.settings.krx_id.get_secret_value() if self.settings.krx_id is not None else None
        )
        krx_password = (
            self.settings.krx_pw.get_secret_value() if self.settings.krx_pw is not None else None
        )
        universe_client = ExchangeUniverseClient(
            krx_id=krx_id,
            krx_password=krx_password,
        )
        try:
            universe = build_universe_snapshot(
                self.settings.research_root,
                client=universe_client,
                stock_csv=self.settings.research_krx_stock_csv,
                etf_csv=self.settings.research_krx_etf_csv,
            )
        finally:
            universe_client.client.close()
        builder = PriceSnapshotBuilder(
            store=self.store,
            fetcher=YFinanceProvider(self.settings.app_mode),
            history_years=self.settings.research_history_years,
            batch_size=self.settings.research_batch_size,
            download_workers=self.settings.research_download_workers,
            max_retries=self.settings.research_max_retries,
            minimum_group_assets=self.settings.research_minimum_group_assets,
        )
        build = builder.build(run_id, universe, progress)
        progress("VALIDATE_RAW", 0, 1, build.failed)
        initial_quality = self.quality.inspect_raw(
            build.staging_path, universe, build.manifest
        )
        if initial_quality.blockers:
            report, issues = self.quality.blocked_report(
                initial_quality, build.manifest
            )
            self.quality.write_report(run_id, report, issues)
            self.quality.ensure_passable(report)

        repair_ids = initial_quality.repair_asset_ids
        if repair_ids:
            progress("REPAIR", 0, len(repair_ids), build.failed)
            assets_by_id = {
                f"{asset.peer_group.value}:{asset.ticker}": asset
                for asset in universe.assets
            }
            builder.refresh_assets(
                build,
                universe,
                [assets_by_id[asset_id] for asset_id in sorted(repair_ids)],
            )
            progress("REPAIR", len(repair_ids), len(repair_ids), build.failed)

        raw_quality = self.quality.inspect_raw(
            build.staging_path, universe, build.manifest
        )
        if raw_quality.blockers:
            report, issues = self.quality.blocked_report(raw_quality, build.manifest)
            self.quality.write_report(run_id, report, issues)
            self.quality.ensure_passable(report)

        quarantined = raw_quality.quarantine_reasons()
        progress("SCORE", build.manifest.get("latest_score_rows", 0), 0, build.failed)
        scorer = ResearchScorer(
            store=self.store,
            lookback_sessions=self.settings.research_score_lookback_sessions,
        )
        manifest = scorer.score(build, universe, quarantined)
        progress("VALIDATE_SCORE", 0, 1, build.failed)
        latest_path = build.staging_path / "scores" / "latest.parquet"
        report, issues = self.quality.finalize(
            raw_quality,
            initial_repair_ids=repair_ids,
            latest=pl.read_parquet(latest_path),
            score_history_path=build.staging_path / "scores" / "history.parquet",
            universe=universe,
            manifest=manifest,
            previous_manifest=previous_manifest,
        )
        manifest["quality"] = report
        self.quality.write_report(
            run_id, report, issues, staging=build.staging_path
        )
        self.quality.ensure_passable(report)
        progress("VALIDATE_SCORE", 1, 1, build.failed)
        progress("ACTIVATE", 1, 1, build.failed)
        final_path = self.store.activate(build.staging_path, manifest)
        return universe, build, manifest, final_path

    async def get_sync(self, run_id: str) -> ResearchSyncResponse | None:
        model = await self.repository.get_sync(run_id)
        return self._sync_response(model) if model is not None else None

    async def status(self) -> ResearchStatusResponse:
        manifest = self.store.current_manifest()
        latest = await self.repository.latest_sync()
        if self.settings.app_mode != "local_research":
            return ResearchStatusResponse(
                app_mode=self.settings.app_mode,
                data_source=DataSource.SYNTHETIC,
                snapshot_state=SnapshotState.READY,
                data_version=self.service.data_version,
                universe_version=DEMO_UNIVERSE_VERSION,
                coverage=self.service.coverage(),
                last_sync=None,
                can_sync=False,
            )
        active = await self.repository.active_sync()
        if manifest is None:
            snapshot_state = (
                SnapshotState.PREPARING if active is not None else SnapshotState.MISSING
            )
            coverage: list[PeerCoverage] = []
            created_at = None
        else:
            snapshot_state = SnapshotState.STALE if self.is_stale() else SnapshotState.READY
            coverage = self.service.coverage()
            created_at = datetime.fromisoformat(str(manifest["created_at"]))
        return ResearchStatusResponse(
            app_mode=self.settings.app_mode,
            data_source=DataSource.YFINANCE,
            snapshot_state=snapshot_state,
            data_version=str(manifest["data_version"]) if manifest else None,
            universe_version=str(manifest["universe_version"]) if manifest else None,
            last_success_at=created_at,
            coverage=coverage,
            last_sync=self._sync_response(latest) if latest else None,
            can_sync=True,
        )

    def _sync_response(self, model: ResearchSyncRunModel) -> ResearchSyncResponse:
        total = model.total_batches
        progress = 0.0 if total <= 0 else min(100.0, model.completed_batches / total * 100)
        return ResearchSyncResponse(
            sync_id=model.id,
            trigger=SyncTrigger(model.trigger),
            status=RunStatus(model.status),
            stage=model.stage,
            collection_mode=(
                ResearchCollectionMode(model.collection_mode)
                if model.collection_mode is not None
                else None
            ),
            completed_batches=model.completed_batches,
            total_batches=total,
            progress_percent=round(progress, 1),
            universe_version=model.universe_version,
            data_version=model.data_version,
            failed_tickers=model.failed_json or [],
            error_message=model.error_message or None,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )


DEMO_UNIVERSE_VERSION = "demo-universe-v1.0.0"
settings = get_settings()
research_sync_manager = ResearchSyncManager(settings=settings, service=research_service)
