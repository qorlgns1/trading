from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
import pytest_asyncio
from quant_api.database import (
    RunRepository,
    SessionFactory,
    UniverseSnapshotModel,
    engine,
)
from quant_api.forward_repository import ForwardLedgerRepository
from quant_api.provider_admin import ProviderConnectionRepository
from quant_api.research_repository import ResearchRepository
from quant_api.schemas import ProviderConnectionState, ProviderId
from quant_core.enums import ForwardAccountType, RunStatus, SyncTrigger
from sqlalchemy import inspect, select, text

pytestmark = pytest.mark.integration

APPLICATION_TABLES = {
    "alembic_version",
    "artifacts",
    "backtest_runs",
    "research_sync_runs",
    "universe_snapshots",
    "candidate_snapshots",
    "candidate_events",
    "paper_accounts",
    "paper_reviews",
    "paper_orders",
    "paper_trades",
    "paper_positions",
    "paper_cash",
    "paper_valuations",
    "provider_connection_status",
    "replay_experiments",
    "replay_experiment_runs",
}


async def _truncate_application_tables() -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE artifacts, backtest_runs, research_sync_runs, "
                "universe_snapshots, candidate_snapshots, paper_accounts, "
                "provider_connection_status, replay_experiments "
                "RESTART IDENTITY CASCADE"
            )
        )


@pytest_asyncio.fixture(autouse=True)
async def clean_database() -> AsyncIterator[None]:
    await _truncate_application_tables()
    try:
        yield
    finally:
        await _truncate_application_tables()
        await engine.dispose()


@pytest.mark.asyncio
async def test_alembic_schema_is_current_on_postgresql() -> None:
    async with engine.connect() as connection:
        tables = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
        revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()

    assert engine.dialect.name == "postgresql"
    assert tables >= APPLICATION_TABLES
    assert revision == "20260712_0005"


@pytest.mark.asyncio
async def test_backtest_metadata_round_trips_through_postgresql() -> None:
    repository = RunRepository()
    await repository.create(
        "postgres-run",
        "config-hash",
        {"weights": {"US_STOCK": 25}, "capital": 50_000_000},
    )
    await repository.set_running("postgres-run")
    await repository.set_succeeded("postgres-run", {"cagr": 0.123, "trades": 42})
    await repository.add_artifact(
        "postgres-run",
        "report.html",
        "backtests/postgres-run/report.html",
        "text/html",
        512,
    )

    run = await repository.get("postgres-run")
    artifacts = await repository.artifacts("postgres-run")

    assert run is not None
    assert run.status == RunStatus.SUCCEEDED.value
    assert run.request_json["capital"] == 50_000_000
    assert run.result_summary == {"cagr": 0.123, "trades": 42}
    assert artifacts[0].object_key == "backtests/postgres-run/report.html"


@pytest.mark.asyncio
async def test_research_state_and_active_snapshot_are_transactional() -> None:
    repository = ResearchRepository()
    sync = await repository.create_sync(SyncTrigger.MANUAL)
    await repository.update_sync(
        sync.id,
        status=RunStatus.RUNNING.value,
        stage="DOWNLOAD",
        completed_batches=3,
        total_batches=10,
        failed_json=[{"ticker": "MISSING", "reason": "fixture"}],
        error_message="x" * 2_100,
    )
    await repository.activate_snapshot(
        version="universe-v1",
        sources={"krx": "fixture"},
        counts={"KR_KOSPI": 100},
        manifest_path="snapshots/v1/manifest.json",
    )
    await repository.activate_snapshot(
        version="universe-v2",
        sources={"krx": "fixture-2"},
        counts={"KR_KOSPI": 101},
        manifest_path="snapshots/v2/manifest.json",
    )

    stored_sync = await repository.get_sync(sync.id)
    active = await repository.active_snapshot()
    async with SessionFactory() as session:
        snapshots = list(
            (
                await session.scalars(
                    select(UniverseSnapshotModel).order_by(UniverseSnapshotModel.version)
                )
            ).all()
        )

    assert stored_sync is not None
    assert stored_sync.completed_batches == 3
    assert stored_sync.failed_json[0]["ticker"] == "MISSING"
    assert len(stored_sync.error_message or "") == 2_000
    assert active is not None
    assert active.version == "universe-v2"
    assert [snapshot.is_active for snapshot in snapshots] == [False, True]


@pytest.mark.asyncio
async def test_forward_account_slot_is_enforced_on_postgresql() -> None:
    repository = ForwardLedgerRepository()
    weights = {
        "US_STOCK": 2500,
        "KR_STOCK": 2500,
        "US_ETF": 2500,
        "KR_ETF": 2500,
    }
    first = await repository.create_account(
        weights=weights,
        baseline_data_version="data-v1",
        baseline_as_of=date(2026, 7, 10),
        market_dates={"US": "2026-07-09", "KR": "2026-07-10"},
    )
    with pytest.raises(ValueError, match="한 개"):
        await repository.create_account(
            weights=weights,
            baseline_data_version="data-v1",
            baseline_as_of=date(2026, 7, 10),
            market_dates={"US": "2026-07-09", "KR": "2026-07-10"},
        )
    await repository.archive_account(first.id)
    second = await repository.create_account(
        weights=weights,
        baseline_data_version="data-v2",
        baseline_as_of=date(2026, 7, 17),
        market_dates={"US": "2026-07-16", "KR": "2026-07-17"},
    )
    assert second.active_slot == "BASELINE"
    experiments = [
        await repository.create_account(
            weights=weights,
            baseline_data_version="data-v2",
            baseline_as_of=date(2026, 7, 17),
            market_dates={"US": "2026-07-16", "KR": "2026-07-17"},
            account_type=ForwardAccountType.EXPERIMENT,
            name=f"실험 {index}",
        )
        for index in range(1, 4)
    ]
    assert {item.active_slot for item in experiments} == {
        "EXPERIMENT_1",
        "EXPERIMENT_2",
        "EXPERIMENT_3",
    }
    with pytest.raises(ValueError, match="세 개"):
        await repository.create_account(
            weights=weights,
            baseline_data_version="data-v2",
            baseline_as_of=date(2026, 7, 17),
            market_dates={"US": "2026-07-16", "KR": "2026-07-17"},
            account_type=ForwardAccountType.EXPERIMENT,
            name="초과",
        )


@pytest.mark.asyncio
async def test_provider_connection_status_round_trips_through_postgresql() -> None:
    repository = ProviderConnectionRepository()
    await repository.save(
        provider=ProviderId.TOSS,
        status=ProviderConnectionState.AVAILABLE,
        checked_at=datetime(2026, 7, 11, 9, 30, tzinfo=UTC),
        latency_ms=187,
        error_code=None,
        message="국내·미국 대표 종목 조회 성공",
    )

    restored = await ProviderConnectionRepository().get(ProviderId.TOSS)

    assert restored is not None
    assert restored.status == "AVAILABLE"
    assert restored.latency_ms == 187
