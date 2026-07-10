from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from quant_api.settings import get_settings


class Base(DeclarativeBase):
    pass


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    result_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ArtifactModel(Base):
    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(128))
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    content_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UniverseSnapshotModel(Base):
    __tablename__ = "universe_snapshots"

    version: Mapped[str] = mapped_column(String(96), primary_key=True)
    source_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    manifest_path: Mapped[str] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ResearchSyncRunModel(Base):
    __tablename__ = "research_sync_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), index=True)
    stage: Mapped[str] = mapped_column(String(48))
    completed_batches: Mapped[int] = mapped_column(Integer, default=0)
    total_batches: Mapped[int] = mapped_column(Integer, default=0)
    universe_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    data_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    failed_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


settings = get_settings()
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def create_schema() -> None:
    if settings.database_url.startswith("sqlite"):
        Path("data").mkdir(parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


class RunRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self.session_factory = session_factory

    async def create(self, run_id: str, config_hash: str, request: dict[str, Any]) -> None:
        async with self.session_factory() as session:
            session.add(
                BacktestRunModel(
                    id=run_id,
                    config_hash=config_hash,
                    status="QUEUED",
                    request_json=request,
                )
            )
            await session.commit()

    async def get(self, run_id: str) -> BacktestRunModel | None:
        async with self.session_factory() as session:
            return await session.get(BacktestRunModel, run_id)

    async def find_succeeded(self, config_hash: str) -> BacktestRunModel | None:
        async with self.session_factory() as session:
            statement = (
                select(BacktestRunModel)
                .where(
                    BacktestRunModel.config_hash == config_hash,
                    BacktestRunModel.status == "SUCCEEDED",
                )
                .order_by(BacktestRunModel.created_at.desc())
                .limit(1)
            )
            return (await session.scalars(statement)).first()

    async def set_running(self, run_id: str) -> None:
        await self._update(run_id, status="RUNNING", error_message=None)

    async def set_succeeded(self, run_id: str, summary: dict[str, Any]) -> None:
        await self._update(run_id, status="SUCCEEDED", result_summary=summary)

    async def set_failed(self, run_id: str, message: str) -> None:
        await self._update(run_id, status="FAILED", error_message=message[:2000])

    async def _update(self, run_id: str, **values: Any) -> None:
        async with self.session_factory() as session:
            model = await session.get(BacktestRunModel, run_id)
            if model is None:
                raise KeyError(run_id)
            for key, value in values.items():
                setattr(model, key, value)
            await session.commit()

    async def add_artifact(
        self,
        run_id: str,
        name: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                ArtifactModel(
                    run_id=run_id,
                    name=name,
                    object_key=object_key,
                    content_type=content_type,
                    size_bytes=size_bytes,
                )
            )
            await session.commit()

    async def artifacts(self, run_id: str) -> list[ArtifactModel]:
        async with self.session_factory() as session:
            statement = select(ArtifactModel).where(ArtifactModel.run_id == run_id)
            return list((await session.scalars(statement)).all())
