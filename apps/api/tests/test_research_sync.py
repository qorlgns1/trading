import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from quant_api.database import Base, ResearchSyncRunModel
from quant_api.research import ResearchService
from quant_api.research_pipeline import (
    PRICE_PIPELINE_VERSION,
    PriceBuildResult,
    determine_collection_mode,
)
from quant_api.research_repository import ResearchRepository
from quant_api.research_store import ResearchSnapshotStore
from quant_api.research_sync import ResearchSyncManager
from quant_api.settings import Settings
from quant_api.universe import UniverseSnapshot
from quant_core.enums import ResearchCollectionMode, RunStatus, SnapshotState, SyncTrigger
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


async def _repository(tmp_path: Path, name: str) -> tuple[AsyncEngine, ResearchRepository]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, ResearchRepository(async_sessionmaker(engine, expire_on_commit=False))


def _manager(
    tmp_path: Path,
    repository: ResearchRepository,
    *,
    app_mode: str = "local_research",
    auto_sync: bool = False,
) -> ResearchSyncManager:
    root = tmp_path / "research"
    settings = Settings(
        app_mode=app_mode,
        research_root=root,
        research_auto_sync=auto_sync,
        _env_file=None,
    )
    store = ResearchSnapshotStore(root)
    return ResearchSyncManager(
        settings=settings,
        service=ResearchService(settings, store),
        repository=repository,
        store=store,
    )


@pytest.mark.parametrize(
    ("manifest", "expected"),
    [
        (None, ResearchCollectionMode.FULL),
        ({"price_pipeline_version": "price-pipeline-v1.0.0"}, ResearchCollectionMode.FULL),
        (
            {"price_pipeline_version": PRICE_PIPELINE_VERSION},
            ResearchCollectionMode.INCREMENTAL,
        ),
    ],
)
def test_collection_mode_matches_snapshot_state(
    manifest: dict[str, str] | None,
    expected: ResearchCollectionMode,
) -> None:
    assert determine_collection_mode(manifest) is expected


@pytest.mark.asyncio
async def test_collection_mode_survives_failure_and_retry_state_updates(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ResearchRepository(async_sessionmaker(engine, expire_on_commit=False))

    run = await repository.create_sync(SyncTrigger.MANUAL, ResearchCollectionMode.INCREMENTAL)
    await repository.update_sync(
        run.id,
        status=RunStatus.FAILED.value,
        stage="FAILED",
        error_message="retry fixture",
    )
    stored = await repository.get_sync(run.id)

    assert stored is not None
    assert stored.collection_mode == ResearchCollectionMode.INCREMENTAL.value
    await engine.dispose()


def test_outdated_price_pipeline_forces_sync_even_when_snapshot_exists(
    tmp_path: Path,
) -> None:
    root = tmp_path / "research"
    store = ResearchSnapshotStore(root)
    staging = store.create_staging("old-pipeline")
    store.activate(
        staging,
        {
            "data_version": "yf-old-pipeline",
            "created_at": datetime(2026, 7, 10, tzinfo=UTC).isoformat(),
            "price_pipeline_version": "price-pipeline-v1.0.0",
        },
    )
    settings = Settings(
        app_mode="local_research",
        research_root=root,
        research_auto_sync=False,
        _env_file=None,
    )
    service = ResearchService(settings, store)
    manager = ResearchSyncManager(settings=settings, service=service, store=store)

    assert manager.is_stale()
    assert manager.collection_mode() is ResearchCollectionMode.FULL

    now = datetime.now(UTC)
    model = ResearchSyncRunModel(
        id="sync-full",
        trigger=SyncTrigger.SCHEDULED.value,
        status=RunStatus.RUNNING.value,
        stage="DOWNLOAD",
        collection_mode=ResearchCollectionMode.FULL.value,
        completed_batches=3,
        total_batches=10,
        failed_json=[],
        created_at=now,
        updated_at=now,
    )
    response = manager._sync_response(model)

    assert response.collection_mode is ResearchCollectionMode.FULL


@pytest.mark.asyncio
async def test_request_reuses_active_run_and_rejects_public_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "request.db")
    manager = _manager(tmp_path, repository)
    launched: list[str] = []
    monkeypatch.setattr(manager, "_launch", launched.append)

    created, duplicate = await manager.request(SyncTrigger.MANUAL)
    reused, reused_duplicate = await manager.request(SyncTrigger.MANUAL)

    assert duplicate is False
    assert reused_duplicate is True
    assert reused.id == created.id
    assert launched == [created.id]
    public_manager = _manager(tmp_path, repository, app_mode="public_demo")
    with pytest.raises(PermissionError, match="local_research"):
        await public_manager.request(SyncTrigger.MANUAL)
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_launch_if_needed_rechecks_staleness_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "launch.db")
    manager = _manager(tmp_path, repository)
    checks = iter([True, False])
    monkeypatch.setattr(manager, "is_stale", lambda: next(checks))
    create = AsyncMock(wraps=repository.create_sync)
    monkeypatch.setattr(repository, "create_sync", create)

    await manager._launch_if_needed(SyncTrigger.SCHEDULED)

    create.assert_not_awaited()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_startup_and_shutdown_manage_scheduler_and_background_tasks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "startup.db")
    manager = _manager(tmp_path, repository, auto_sync=True)
    launch_if_needed = AsyncMock()
    scheduler_started = asyncio.Event()

    async def scheduler() -> None:
        scheduler_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_launch_if_needed", launch_if_needed)
    monkeypatch.setattr(manager, "_scheduler_loop", scheduler)

    await manager.startup()
    await scheduler_started.wait()
    background = asyncio.create_task(asyncio.Event().wait())
    manager._tasks.add(background)
    await manager.shutdown()

    launch_if_needed.assert_awaited_once_with(SyncTrigger.STARTUP)
    assert manager._scheduler is not None and manager._scheduler.cancelled()
    assert background.cancelled()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_succeeds_even_when_forward_processing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "success.db")
    manager = _manager(tmp_path, repository)
    run = await repository.create_sync(SyncTrigger.MANUAL, ResearchCollectionMode.FULL)
    final_path = tmp_path / "activated"
    final_path.mkdir()
    universe = UniverseSnapshot(
        version="universe-v1",
        path=tmp_path / "universe.csv",
        manifest_path=tmp_path / "universe.json",
        assets=[],
        sources={"fixture": "unit"},
        counts={"total": 0},
    )
    build = PriceBuildResult(
        staging_path=tmp_path / "staging",
        data_version="data-v1",
        manifest={},
        failed=[{"ticker": "FAIL"}],
    )

    def execute_with_progress(
        _: str,
        progress: Callable[[str, int, int, list[dict[str, object]]], None],
    ) -> object:
        progress("DOWNLOAD", 1, 2, [{"ticker": "FAIL"}])
        return universe, build, {}, final_path

    monkeypatch.setattr(manager, "_execute", execute_with_progress)

    async def fail_forward(_: str) -> None:
        raise RuntimeError("forward fixture")

    monkeypatch.setattr("quant_api.forward.forward_service.process_snapshot", fail_forward)

    await manager.run(run.id)

    stored = await repository.get_sync(run.id)
    assert stored is not None
    assert stored.status == RunStatus.SUCCEEDED.value
    assert stored.stage == "SUCCEEDED"
    assert stored.universe_version == "universe-v1"
    assert stored.data_version == "data-v1"
    assert stored.failed_json == [{"ticker": "FAIL"}]
    active = await repository.active_snapshot()
    assert active is not None and active.version == "data-v1"
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_failure_removes_staging_and_records_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "failure.db")
    manager = _manager(tmp_path, repository)
    run = await repository.create_sync(SyncTrigger.MANUAL, ResearchCollectionMode.FULL)
    staging = manager.store.snapshots_root / f".staging-{run.id}"
    staging.mkdir(parents=True)

    def fail_execute(*_: object) -> object:
        raise RuntimeError("download failed")

    monkeypatch.setattr(manager, "_execute", fail_execute)

    await manager.run(run.id)

    stored = await repository.get_sync(run.id)
    assert stored is not None
    assert stored.status == RunStatus.FAILED.value
    assert stored.stage == "FAILED"
    assert stored.error_message == "download failed"
    assert not staging.exists()
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_run_cancellation_records_resumable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, repository = await _repository(tmp_path, "cancel.db")
    manager = _manager(tmp_path, repository)
    run = await repository.create_sync(SyncTrigger.MANUAL, ResearchCollectionMode.INCREMENTAL)

    def cancel_execute(*_: object) -> object:
        raise asyncio.CancelledError

    monkeypatch.setattr(manager, "_execute", cancel_execute)

    with pytest.raises(asyncio.CancelledError):
        await manager.run(run.id)

    stored = await repository.get_sync(run.id)
    assert stored is not None
    assert stored.status == RunStatus.FAILED.value
    assert stored.stage == "CANCELLED"
    assert "이어받습니다" in str(stored.error_message)
    await engine.dispose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_status_reports_public_and_local_missing_states(tmp_path: Path) -> None:
    engine, repository = await _repository(tmp_path, "status.db")
    public = _manager(tmp_path, repository, app_mode="public_demo")
    public_status = await public.status()

    assert public_status.snapshot_state is SnapshotState.READY
    assert public_status.can_sync is False

    local = _manager(tmp_path, repository)
    missing = await local.status()
    assert missing.snapshot_state is SnapshotState.MISSING
    queued = await repository.create_sync(SyncTrigger.MANUAL, ResearchCollectionMode.FULL)
    preparing = await local.status()
    response = await local.get_sync(queued.id)

    assert preparing.snapshot_state is SnapshotState.PREPARING
    assert response is not None
    assert response.progress_percent == 0.0
    assert response.collection_mode is ResearchCollectionMode.FULL
    await engine.dispose()  # type: ignore[attr-defined]
