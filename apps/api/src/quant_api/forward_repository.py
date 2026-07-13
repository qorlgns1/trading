import math
import uuid
from datetime import UTC, date, datetime
from typing import Any

from quant_core.config import PORTFOLIO_VERSION, TREND_SCORE_VERSION
from quant_core.enums import ForwardAccountType, PaperAccountStatus, PaperOrderStatus, Sleeve
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from quant_api.database import (
    CandidateEventModel,
    CandidateSnapshotModel,
    PaperAccountModel,
    PaperCashModel,
    PaperOrderModel,
    PaperPositionModel,
    PaperReviewModel,
    PaperTradeModel,
    PaperValuationModel,
    SessionFactory,
)


class ForwardLedgerRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] = SessionFactory) -> None:
        self.session_factory = session_factory

    async def latest_candidate_snapshot(self) -> CandidateSnapshotModel | None:
        async with self.session_factory() as session:
            statement = (
                select(CandidateSnapshotModel)
                .order_by(
                    CandidateSnapshotModel.as_of.desc(),
                    CandidateSnapshotModel.created_at.desc(),
                )
                .limit(1)
            )
            return (await session.scalars(statement)).first()

    async def candidate_snapshot(self, data_version: str) -> CandidateSnapshotModel | None:
        async with self.session_factory() as session:
            statement = select(CandidateSnapshotModel).where(
                CandidateSnapshotModel.data_version == data_version
            )
            return (await session.scalars(statement)).first()

    async def save_candidate_snapshot(
        self,
        *,
        data_version: str,
        as_of: date,
        artifact_path: str,
        artifact_sha256: str,
        counts: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> CandidateSnapshotModel:
        existing = await self.candidate_snapshot(data_version)
        if existing is not None:
            return existing
        model = CandidateSnapshotModel(
            id=str(uuid.uuid4()),
            data_version=data_version,
            as_of=as_of,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            counts_json=counts,
        )
        try:
            async with self.session_factory() as session, session.begin():
                session.add(model)
                for event in events:
                    session.add(CandidateEventModel(snapshot_id=model.id, **event))
        except IntegrityError:
            existing = await self.candidate_snapshot(data_version)
            if existing is None:
                raise
            return existing
        return model

    async def candidate_history(
        self,
        *,
        page: int,
        page_size: int,
        peer_group: str | None = None,
        event_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        filters = []
        if peer_group is not None:
            filters.append(CandidateEventModel.peer_group == peer_group)
        if event_type is not None:
            filters.append(CandidateEventModel.event_type == event_type)
        if date_from is not None:
            filters.append(CandidateEventModel.as_of >= date_from)
        if date_to is not None:
            filters.append(CandidateEventModel.as_of <= date_to)
        async with self.session_factory() as session:
            total_statement = select(func.count(CandidateEventModel.id)).where(*filters)
            total = int((await session.scalar(total_statement)) or 0)
            statement = (
                select(CandidateEventModel, CandidateSnapshotModel.data_version)
                .join(
                    CandidateSnapshotModel,
                    CandidateSnapshotModel.id == CandidateEventModel.snapshot_id,
                )
                .where(*filters)
                .order_by(
                    CandidateEventModel.as_of.desc(),
                    CandidateEventModel.score.desc(),
                    CandidateEventModel.asset_id,
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            rows = (await session.execute(statement)).all()
        return total, [
            {
                "as_of": event.as_of,
                "data_version": data_version,
                "event_type": event.event_type,
                "asset_id": event.asset_id,
                "symbol": event.symbol,
                "name": event.name,
                "peer_group": event.peer_group,
                "score": event.score,
                "previous_score": event.previous_score,
            }
            for event, data_version in rows
        ]

    async def create_account(
        self,
        *,
        weights: dict[str, int],
        baseline_data_version: str,
        baseline_as_of: date,
        market_dates: dict[str, str],
        account_type: ForwardAccountType = ForwardAccountType.BASELINE,
        name: str = "기준 포트폴리오",
        initial_capital_krw: float = 50_000_000.0,
        strategy_config: dict[str, Any] | None = None,
        strategy_config_hash: str | None = None,
        source_experiment_id: str | None = None,
        source_run_id: str | None = None,
        score_version: str = TREND_SCORE_VERSION,
        portfolio_version: str = PORTFOLIO_VERSION,
        sleeve_slot_counts: dict[str, int] | None = None,
    ) -> PaperAccountModel:
        sleeve_slot_counts = sleeve_slot_counts or {sleeve.value: 3 for sleeve in Sleeve}
        active_slot = account_type.value
        account = PaperAccountModel(
            id=str(uuid.uuid4()),
            active_slot=active_slot,
            account_type=account_type.value,
            name=name,
            status=PaperAccountStatus.WAITING_FOR_REVIEW.value,
            initial_capital_krw=initial_capital_krw,
            weights_json=weights,
            strategy_config_json=strategy_config,
            strategy_config_hash=strategy_config_hash,
            source_experiment_id=source_experiment_id,
            source_run_id=source_run_id,
            score_version=score_version,
            portfolio_version=portfolio_version,
            baseline_data_version=baseline_data_version,
            last_data_version=baseline_data_version,
            review_required_json=[],
        )
        try:
            async with self.session_factory() as session, session.begin():
                active_slots = set(
                    (
                        await session.scalars(
                            select(PaperAccountModel.active_slot).where(
                                PaperAccountModel.active_slot.is_not(None)
                            )
                        )
                    ).all()
                )
                if account_type is ForwardAccountType.BASELINE:
                    if "BASELINE" in active_slots or "CURRENT" in active_slots:
                        raise ValueError("운영 중인 기준 포워드 계좌는 한 개만 만들 수 있습니다.")
                    account.active_slot = "BASELINE"
                else:
                    available = next(
                        (
                            f"EXPERIMENT_{index}"
                            for index in range(1, 4)
                            if f"EXPERIMENT_{index}" not in active_slots
                        ),
                        None,
                    )
                    if available is None:
                        raise ValueError("운영 중인 실험 포워드 계좌는 세 개까지 만들 수 있습니다.")
                    account.active_slot = available
                session.add(account)
                for sleeve in Sleeve:
                    allocation = initial_capital_krw * weights[sleeve.value] / 10_000
                    session.add(
                        PaperCashModel(
                            account_id=account.id,
                            sleeve=sleeve.value,
                            currency="KRW",
                            balance=allocation,
                            target_per_slot=allocation
                            / max(sleeve_slot_counts.get(sleeve.value, 0), 1),
                        )
                    )
                session.add(
                    PaperValuationModel(
                        account_id=account.id,
                        data_version=baseline_data_version,
                        as_of=baseline_as_of,
                        market_dates_json=market_dates,
                        total_value_krw=initial_capital_krw,
                        cash_krw=initial_capital_krw,
                        invested_krw=0.0,
                        benchmark_value_krw=None,
                        drawdown=0.0,
                    )
                )
        except IntegrityError as error:
            raise ValueError("같은 유형의 활성 포워드 계좌 슬롯을 사용할 수 없습니다.") from error
        return account

    async def current_account(self) -> PaperAccountModel | None:
        async with self.session_factory() as session:
            statement = (
                select(PaperAccountModel)
                .where(PaperAccountModel.active_slot.in_(["BASELINE", "CURRENT"]))
                .limit(1)
            )
            return (await session.scalars(statement)).first()

    async def active_accounts(self) -> list[PaperAccountModel]:
        async with self.session_factory() as session:
            statement = (
                select(PaperAccountModel)
                .where(PaperAccountModel.active_slot.is_not(None))
                .order_by(PaperAccountModel.account_type, PaperAccountModel.created_at)
            )
            return list((await session.scalars(statement)).all())

    async def get_account(self, account_id: str) -> PaperAccountModel | None:
        async with self.session_factory() as session:
            return await session.get(PaperAccountModel, account_id)

    async def archive_account(self, account_id: str) -> PaperAccountModel:
        async with self.session_factory() as session, session.begin():
            account = await session.get(PaperAccountModel, account_id)
            if account is None:
                raise KeyError(account_id)
            if account.active_slot is None:
                return account
            account.active_slot = None
            account.status = PaperAccountStatus.ARCHIVED.value
            account.archived_at = datetime.now(UTC)
            await session.execute(
                update(PaperOrderModel)
                .where(
                    PaperOrderModel.account_id == account_id,
                    PaperOrderModel.side == "BUY",
                    PaperOrderModel.status.in_(
                        [
                            PaperOrderStatus.PENDING.value,
                            PaperOrderStatus.DEFERRED.value,
                        ]
                    ),
                )
                .values(status=PaperOrderStatus.CANCELLED.value)
            )
        return account

    async def account_state(
        self, account_id: str
    ) -> tuple[
        list[PaperCashModel],
        list[PaperPositionModel],
        list[PaperOrderModel],
        list[PaperValuationModel],
    ]:
        async with self.session_factory() as session:
            cash = list(
                (
                    await session.scalars(
                        select(PaperCashModel).where(PaperCashModel.account_id == account_id)
                    )
                ).all()
            )
            positions = list(
                (
                    await session.scalars(
                        select(PaperPositionModel).where(
                            PaperPositionModel.account_id == account_id
                        )
                    )
                ).all()
            )
            pending = list(
                (
                    await session.scalars(
                        select(PaperOrderModel).where(
                            PaperOrderModel.account_id == account_id,
                            PaperOrderModel.status.in_(
                                [
                                    PaperOrderStatus.PENDING.value,
                                    PaperOrderStatus.DEFERRED.value,
                                ]
                            ),
                        )
                    )
                ).all()
            )
            valuations = list(
                (
                    await session.scalars(
                        select(PaperValuationModel)
                        .where(PaperValuationModel.account_id == account_id)
                        .order_by(PaperValuationModel.as_of)
                    )
                ).all()
            )
        return cash, positions, pending, valuations

    async def activity(
        self, account_id: str, *, page: int, page_size: int
    ) -> tuple[int, list[dict[str, Any]]]:
        async with self.session_factory() as session:
            reviews = list(
                (
                    await session.scalars(
                        select(PaperReviewModel).where(PaperReviewModel.account_id == account_id)
                    )
                ).all()
            )
            orders = list(
                (
                    await session.scalars(
                        select(PaperOrderModel).where(PaperOrderModel.account_id == account_id)
                    )
                ).all()
            )
            trades = list(
                (
                    await session.scalars(
                        select(PaperTradeModel).where(PaperTradeModel.account_id == account_id)
                    )
                ).all()
            )
        items: list[dict[str, Any]] = [
            {
                "type": "REVIEW",
                "date": model.review_date.isoformat(),
                "status": model.status,
                "data_version": model.data_version,
                "details": model.details_json,
            }
            for model in reviews
        ]
        items.extend(
            {
                "type": "ORDER",
                "date": model.scheduled_date.isoformat(),
                "status": model.status,
                "side": model.side,
                "asset_id": model.asset_id,
                "symbol": model.symbol,
                "quantity": model.quantity,
                "price": model.price,
                "reason": model.reason,
            }
            for model in orders
        )
        items.extend(
            {
                "type": "TRADE",
                "date": model.traded_on.isoformat(),
                "status": "FILLED",
                "side": model.side,
                "asset_id": model.asset_id,
                "symbol": model.symbol,
                "quantity": model.quantity,
                "price": model.price,
                "cost": model.cost,
                "reason": model.reason,
            }
            for model in trades
        )
        items.sort(key=lambda item: (str(item["date"]), str(item["type"])), reverse=True)
        total = len(items)
        start = (page - 1) * page_size
        return total, items[start : start + page_size]

    async def set_account_error(self, account_id: str, message: str) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(PaperAccountModel)
                .where(PaperAccountModel.id == account_id)
                .values(
                    status=PaperAccountStatus.ERROR.value,
                    error_message=message[:2000],
                )
            )
            await session.commit()


def total_pages(total: int, page_size: int) -> int:
    return max(1, math.ceil(total / page_size))
