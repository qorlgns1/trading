import uuid
from typing import Any

from quant_core.enums import RunStatus, SyncTrigger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quant_api.database import (
    ResearchSyncRunModel,
    SessionFactory,
    UniverseSnapshotModel,
)


class ResearchRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self.session_factory = session_factory

    async def create_sync(self, trigger: SyncTrigger) -> ResearchSyncRunModel:
        run = ResearchSyncRunModel(
            id=str(uuid.uuid4()),
            trigger=trigger.value,
            status=RunStatus.QUEUED.value,
            stage="QUEUED",
            failed_json=[],
        )
        async with self.session_factory() as session:
            session.add(run)
            await session.commit()
            await session.refresh(run)
        return run

    async def get_sync(self, run_id: str) -> ResearchSyncRunModel | None:
        async with self.session_factory() as session:
            return await session.get(ResearchSyncRunModel, run_id)

    async def active_sync(self) -> ResearchSyncRunModel | None:
        async with self.session_factory() as session:
            statement = (
                select(ResearchSyncRunModel)
                .where(
                    ResearchSyncRunModel.status.in_(
                        [RunStatus.QUEUED.value, RunStatus.RUNNING.value]
                    )
                )
                .order_by(ResearchSyncRunModel.created_at.desc())
                .limit(1)
            )
            return (await session.scalars(statement)).first()

    async def latest_sync(self) -> ResearchSyncRunModel | None:
        async with self.session_factory() as session:
            statement = (
                select(ResearchSyncRunModel)
                .order_by(ResearchSyncRunModel.created_at.desc())
                .limit(1)
            )
            return (await session.scalars(statement)).first()

    async def update_sync(self, run_id: str, **values: Any) -> None:
        values["error_message"] = (
            str(values["error_message"])[:2000]
            if values.get("error_message") is not None
            else values.get("error_message")
        )
        values = {key: value for key, value in values.items() if value is not None}
        async with self.session_factory() as session:
            await session.execute(
                update(ResearchSyncRunModel)
                .where(ResearchSyncRunModel.id == run_id)
                .values(**values)
            )
            await session.commit()

    async def fail_interrupted_syncs(self) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ResearchSyncRunModel)
                .where(ResearchSyncRunModel.status == RunStatus.RUNNING.value)
                .values(
                    status=RunStatus.FAILED.value,
                    stage="INTERRUPTED",
                    error_message=(
                        "API 재시작으로 동기화가 중단되었습니다. "
                        "다시 실행하면 이어받습니다."
                    ),
                )
            )
            await session.commit()

    async def activate_snapshot(
        self,
        *,
        version: str,
        sources: dict[str, Any],
        counts: dict[str, Any],
        manifest_path: str,
    ) -> None:
        async with self.session_factory() as session:
            await session.execute(update(UniverseSnapshotModel).values(is_active=False))
            model = await session.get(UniverseSnapshotModel, version)
            if model is None:
                model = UniverseSnapshotModel(
                    version=version,
                    source_json=sources,
                    counts_json=counts,
                    manifest_path=manifest_path,
                    is_active=True,
                )
                session.add(model)
            else:
                model.source_json = sources
                model.counts_json = counts
                model.manifest_path = manifest_path
                model.is_active = True
            await session.commit()

    async def active_snapshot(self) -> UniverseSnapshotModel | None:
        async with self.session_factory() as session:
            statement = (
                select(UniverseSnapshotModel)
                .where(UniverseSnapshotModel.is_active.is_(True))
                .order_by(UniverseSnapshotModel.created_at.desc())
                .limit(1)
            )
            return (await session.scalars(statement)).first()
