from collections.abc import AsyncIterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
)
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
    run_kind: Mapped[str] = mapped_column(String(32), default="DEMO_BACKTEST", index=True)
    data_version: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String(48), default="QUEUED")
    completed_units: Mapped[int] = mapped_column(Integer, default=0)
    total_units: Mapped[int] = mapped_column(Integer, default=0)
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


class CandidateSnapshotModel(Base):
    __tablename__ = "candidate_snapshots"
    __table_args__ = (
        UniqueConstraint("data_version", "as_of", name="uq_candidate_snapshot_version_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    data_version: Mapped[str] = mapped_column(String(96), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    artifact_path: Mapped[str] = mapped_column(String(512))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateEventModel(Base):
    __tablename__ = "candidate_events"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "asset_id", "event_type", name="uq_candidate_event_snapshot_asset_type"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("candidate_snapshots.id", ondelete="CASCADE"), index=True
    )
    as_of: Mapped[date] = mapped_column(Date, index=True)
    event_type: Mapped[str] = mapped_column(String(16), index=True)
    asset_id: Mapped[str] = mapped_column(String(160), index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    peer_group: Mapped[str] = mapped_column(String(48), index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PaperAccountModel(Base):
    __tablename__ = "paper_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    active_slot: Mapped[str | None] = mapped_column(String(16), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    initial_capital_krw: Mapped[float] = mapped_column(Float)
    weights_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    score_version: Mapped[str] = mapped_column(String(64))
    portfolio_version: Mapped[str] = mapped_column(String(64))
    baseline_data_version: Mapped[str] = mapped_column(String(96))
    last_data_version: Mapped[str | None] = mapped_column(String(96), nullable=True)
    last_review_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    review_required_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PaperReviewModel(Base):
    __tablename__ = "paper_reviews"
    __table_args__ = (
        UniqueConstraint("account_id", "review_date", name="uq_paper_review_account_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    review_date: Mapped[date] = mapped_column(Date, index=True)
    data_version: Mapped[str] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(32))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PaperOrderModel(Base):
    __tablename__ = "paper_orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    review_id: Mapped[str] = mapped_column(
        ForeignKey("paper_reviews.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(String(160), index=True)
    symbol: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    peer_group: Mapped[str] = mapped_column(String(48))
    sleeve: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    side: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), index=True)
    scheduled_date: Mapped[date] = mapped_column(Date)
    filled_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    notional: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PaperTradeModel(Base):
    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("paper_orders.id", ondelete="CASCADE"), unique=True
    )
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    traded_on: Mapped[date] = mapped_column(Date, index=True)
    asset_id: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(String(64))


class PaperPositionModel(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (
        UniqueConstraint("account_id", "asset_id", name="uq_paper_position_account_asset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[str] = mapped_column(String(160))
    symbol: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(256))
    peer_group: Mapped[str] = mapped_column(String(48))
    sleeve: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[int] = mapped_column(Integer)
    average_cost: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float] = mapped_column(Float)
    last_score: Mapped[float] = mapped_column(Float)
    data_status: Mapped[str] = mapped_column(String(32))
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PaperCashModel(Base):
    __tablename__ = "paper_cash"
    __table_args__ = (
        UniqueConstraint("account_id", "sleeve", name="uq_paper_cash_account_sleeve"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    sleeve: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(8))
    balance: Mapped[float] = mapped_column(Float)
    target_per_slot: Mapped[float] = mapped_column(Float)


class PaperValuationModel(Base):
    __tablename__ = "paper_valuations"
    __table_args__ = (
        UniqueConstraint("account_id", "data_version", name="uq_paper_value_account_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("paper_accounts.id", ondelete="CASCADE"), index=True
    )
    data_version: Mapped[str] = mapped_column(String(96))
    as_of: Mapped[date] = mapped_column(Date, index=True)
    market_dates_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    total_value_krw: Mapped[float] = mapped_column(Float)
    cash_krw: Mapped[float] = mapped_column(Float)
    invested_krw: Mapped[float] = mapped_column(Float)
    benchmark_value_krw: Mapped[float | None] = mapped_column(Float, nullable=True)
    drawdown: Mapped[float] = mapped_column(Float)


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

    async def create(
        self,
        run_id: str,
        config_hash: str,
        request: dict[str, Any],
        *,
        run_kind: str = "DEMO_BACKTEST",
        data_version: str | None = None,
    ) -> None:
        async with self.session_factory() as session:
            session.add(
                BacktestRunModel(
                    id=run_id,
                    config_hash=config_hash,
                    status="QUEUED",
                    run_kind=run_kind,
                    data_version=data_version,
                    stage="QUEUED",
                    request_json=request,
                )
            )
            await session.commit()

    async def get(self, run_id: str) -> BacktestRunModel | None:
        async with self.session_factory() as session:
            return await session.get(BacktestRunModel, run_id)

    async def find_succeeded(
        self, config_hash: str, *, run_kind: str | None = None
    ) -> BacktestRunModel | None:
        async with self.session_factory() as session:
            statement = select(BacktestRunModel).where(
                BacktestRunModel.config_hash == config_hash,
                BacktestRunModel.status == "SUCCEEDED",
            )
            if run_kind is not None:
                statement = statement.where(BacktestRunModel.run_kind == run_kind)
            statement = statement.order_by(BacktestRunModel.created_at.desc()).limit(1)
            return (await session.scalars(statement)).first()

    async def set_running(self, run_id: str) -> None:
        await self._update(run_id, status="RUNNING", stage="RUNNING", error_message=None)

    async def set_succeeded(self, run_id: str, summary: dict[str, Any]) -> None:
        await self._update(
            run_id,
            status="SUCCEEDED",
            stage="SUCCEEDED",
            result_summary=summary,
        )

    async def set_failed(self, run_id: str, message: str) -> None:
        await self._update(
            run_id,
            status="FAILED",
            stage="FAILED",
            error_message=message[:2000],
        )

    async def set_progress(self, run_id: str, stage: str, completed: int, total: int) -> None:
        await self._update(
            run_id,
            stage=stage,
            completed_units=completed,
            total_units=total,
        )

    async def fail_interrupted(self, *, run_kind: str) -> list[tuple[str, str | None]]:
        async with self.session_factory() as session:
            statement = select(BacktestRunModel).where(
                BacktestRunModel.run_kind == run_kind,
                BacktestRunModel.status.in_(["QUEUED", "RUNNING"]),
            )
            models = list((await session.scalars(statement)).all())
            for model in models:
                model.status = "FAILED"
                model.stage = "INTERRUPTED"
                model.error_message = (
                    "API 재시작으로 실행이 중단되었습니다. 다시 요청할 수 있습니다."
                )
            await session.commit()
            return [(model.id, model.data_version) for model in models]

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
