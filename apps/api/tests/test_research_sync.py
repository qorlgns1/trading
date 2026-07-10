from datetime import UTC, datetime
from pathlib import Path

from quant_api.research import ResearchService
from quant_api.research_store import ResearchSnapshotStore
from quant_api.research_sync import ResearchSyncManager
from quant_api.settings import Settings


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
