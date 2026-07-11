from datetime import date
from pathlib import Path

import pytest
from quant_api.database import Base, RunRepository
from quant_api.forward_repository import ForwardLedgerRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_forward_account_is_unique_until_archived(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'forward.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ForwardLedgerRepository(session_factory)
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

    archived = await repository.archive_account(first.id)
    second = await repository.create_account(
        weights=weights,
        baseline_data_version="data-v2",
        baseline_as_of=date(2026, 7, 17),
        market_dates={"US": "2026-07-16", "KR": "2026-07-17"},
    )

    assert archived.status == "ARCHIVED"
    assert archived.active_slot is None
    assert second.active_slot == "CURRENT"
    await engine.dispose()


@pytest.mark.asyncio
async def test_candidate_snapshot_retry_is_idempotent(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ForwardLedgerRepository(session_factory)
    event = {
        "as_of": date(2026, 7, 10),
        "event_type": "BASELINE",
        "asset_id": "US_STOCK:TEST",
        "symbol": "TEST",
        "name": "Test",
        "peer_group": "US_STOCK",
        "score": 80.0,
        "previous_score": None,
        "details_json": {},
    }

    first = await repository.save_candidate_snapshot(
        data_version="data-v1",
        as_of=date(2026, 7, 10),
        artifact_path="/tmp/candidates.parquet",
        artifact_sha256="a" * 64,
        counts={"candidates": 1},
        events=[event],
    )
    retried = await repository.save_candidate_snapshot(
        data_version="data-v1",
        as_of=date(2026, 7, 10),
        artifact_path="/tmp/candidates.parquet",
        artifact_sha256="a" * 64,
        counts={"candidates": 1},
        events=[event],
    )
    total, rows = await repository.candidate_history(page=1, page_size=10)

    assert retried.id == first.id
    assert total == 1
    assert rows[0]["event_type"] == "BASELINE"
    await engine.dispose()


@pytest.mark.asyncio
async def test_interrupted_replay_is_failed_after_restart(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = RunRepository(session_factory)
    await repository.create(
        "replay-run",
        "config-hash",
        {},
        run_kind="REAL_REPLAY",
        data_version="data-v1",
    )
    await repository.set_running("replay-run")

    interrupted = await repository.fail_interrupted(run_kind="REAL_REPLAY")
    model = await repository.get("replay-run")

    assert interrupted == [("replay-run", "data-v1")]
    assert model is not None
    assert model.status == "FAILED"
    assert model.stage == "INTERRUPTED"
    await engine.dispose()
