from datetime import UTC, datetime
from pathlib import Path

import pytest
from quant_api.database import Base, ResearchSyncRunModel
from quant_api.research import ResearchService
from quant_api.research_pipeline import PRICE_PIPELINE_VERSION, determine_collection_mode
from quant_api.research_repository import ResearchRepository
from quant_api.research_store import ResearchSnapshotStore
from quant_api.research_sync import ResearchSyncManager
from quant_api.settings import Settings
from quant_core.enums import ResearchCollectionMode, RunStatus, SyncTrigger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


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
