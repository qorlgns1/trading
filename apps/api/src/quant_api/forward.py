import math
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from quant_core.calendar import next_trading_date
from quant_core.config import PEER_GROUP_SLEEVE, PortfolioConfig
from quant_core.enums import (
    CandidateEventType,
    DataStatus,
    PaperAccountStatus,
    PaperOrderStatus,
    PeerGroup,
    Sleeve,
)
from quant_core.market_portfolio import PortfolioPosition, plan_weekly_orders
from sqlalchemy import select

from quant_api.database import (
    CandidateSnapshotModel,
    PaperAccountModel,
    PaperCashModel,
    PaperOrderModel,
    PaperPositionModel,
    PaperReviewModel,
    PaperTradeModel,
    PaperValuationModel,
)
from quant_api.forward_repository import ForwardLedgerRepository, total_pages
from quant_api.research_store import ResearchSnapshotStore, file_sha256
from quant_api.schemas import (
    CandidateHistoryItem,
    CandidateHistoryResponse,
    ForwardAccountCreate,
    ForwardAccountResponse,
    ForwardActivityResponse,
)
from quant_api.settings import Settings, get_settings


def _market_dates(manifest: dict[str, Any]) -> dict[str, str]:
    coverage = manifest.get("coverage", {})
    groups = {
        "US": [PeerGroup.US_STOCK, PeerGroup.US_EQUITY_ETF],
        "KR": [
            PeerGroup.KR_KOSPI,
            PeerGroup.KR_KOSDAQ,
            PeerGroup.KR_DOMESTIC_EQUITY_ETF,
            PeerGroup.KR_OVERSEAS_EQUITY_ETF,
        ],
    }
    result: dict[str, str] = {}
    for market, peer_groups in groups.items():
        dates = [
            str(coverage[group.value]["as_of"])
            for group in peer_groups
            if coverage.get(group.value, {}).get("as_of")
        ]
        if len(dates) == len(peer_groups):
            result[market] = min(dates)
    return result


def _completed_review_date(manifest: dict[str, Any]) -> date | None:
    market_dates = _market_dates(manifest)
    if set(market_dates) != {"US", "KR"}:
        return None
    us_date = date.fromisoformat(market_dates["US"])
    kr_date = date.fromisoformat(market_dates["KR"])
    if us_date.isocalendar()[:2] != kr_date.isocalendar()[:2]:
        return None
    week = us_date.isocalendar()[:2]
    if next_trading_date(us_date, "US").isocalendar()[:2] == week:
        return None
    if next_trading_date(kr_date, "KR").isocalendar()[:2] == week:
        return None
    return max(us_date, kr_date)


class ForwardService:
    def __init__(
        self,
        settings: Settings,
        *,
        repository: ForwardLedgerRepository | None = None,
        store: ResearchSnapshotStore | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or ForwardLedgerRepository()
        self.store = store or ResearchSnapshotStore(settings.research_root)

    def _ensure_local(self) -> None:
        if self.settings.app_mode != "local_research":
            raise PermissionError(
                "포워드 연구 기능은 local_research 모드에서만 사용할 수 있습니다."
            )

    def _snapshot(self, data_version: str | None = None) -> tuple[Path, dict[str, Any]]:
        if data_version is None:
            manifest = self.store.current_manifest()
            if manifest is None:
                raise RuntimeError("활성화된 실데이터 스냅샷이 없습니다.")
            snapshot = self.store.snapshot_path(str(manifest["data_version"]))
            return snapshot, manifest
        snapshot = self.store.snapshot_path(data_version)
        import json

        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        return snapshot, manifest

    async def capture_candidates(self, data_version: str | None = None) -> CandidateSnapshotModel:
        self._ensure_local()
        snapshot, manifest = self._snapshot(data_version)
        version = str(manifest["data_version"])
        existing = await self.repository.candidate_snapshot(version)
        if existing is not None:
            return existing
        latest = pl.read_parquet(snapshot / "scores" / "latest.parquet")
        candidates = (
            latest.filter(pl.col("official_candidate").fill_null(False))
            .select(
                "date",
                "asset_id",
                "symbol",
                "name",
                "peer_group",
                "currency",
                "trend_score",
                "relative_momentum",
                "data_status",
                "candidate_eligible",
                "benchmark_close",
                "benchmark_sma200",
            )
            .sort(["peer_group", "trend_score", "asset_id"], descending=[False, True, False])
        )
        market_dates = _market_dates(manifest)
        if not market_dates:
            raise RuntimeError("후보 스냅샷의 시장 기준일을 결정할 수 없습니다.")
        as_of = max(date.fromisoformat(value) for value in market_dates.values())
        output_dir = self.store.root / "forward" / "signals" / version
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "candidates.parquet"
        temp_path = output_path.with_suffix(".tmp.parquet")
        candidates.write_parquet(temp_path, compression="zstd", statistics=True)
        os.replace(temp_path, output_path)

        previous = await self.repository.latest_candidate_snapshot()
        previous_rows: dict[str, dict[str, Any]] = {}
        if previous is not None and Path(previous.artifact_path).is_file():
            previous_rows = {
                str(row["asset_id"]): row
                for row in pl.read_parquet(previous.artifact_path).to_dicts()
            }
        current_rows = {str(row["asset_id"]): row for row in candidates.to_dicts()}
        events: list[dict[str, Any]] = []
        for asset_id, row in current_rows.items():
            prior = previous_rows.get(asset_id)
            event_type = (
                CandidateEventType.BASELINE
                if previous is None
                else CandidateEventType.RETAINED
                if prior is not None
                else CandidateEventType.ENTERED
            )
            events.append(
                {
                    "as_of": as_of,
                    "event_type": event_type.value,
                    "asset_id": asset_id,
                    "symbol": str(row["symbol"]),
                    "name": str(row["name"]),
                    "peer_group": str(row["peer_group"]),
                    "score": float(row["trend_score"]),
                    "previous_score": (float(prior["trend_score"]) if prior is not None else None),
                    "details_json": {"market_dates": market_dates},
                }
            )
        for asset_id in sorted(set(previous_rows) - set(current_rows)):
            row = previous_rows[asset_id]
            events.append(
                {
                    "as_of": as_of,
                    "event_type": CandidateEventType.EXITED.value,
                    "asset_id": asset_id,
                    "symbol": str(row["symbol"]),
                    "name": str(row["name"]),
                    "peer_group": str(row["peer_group"]),
                    "score": None,
                    "previous_score": float(row["trend_score"]),
                    "details_json": {"market_dates": market_dates},
                }
            )
        event_counts: dict[str, int] = {}
        for event in events:
            kind = str(event["event_type"])
            event_counts[kind] = event_counts.get(kind, 0) + 1
        return await self.repository.save_candidate_snapshot(
            data_version=version,
            as_of=as_of,
            artifact_path=str(output_path.resolve()),
            artifact_sha256=file_sha256(output_path),
            counts={"candidates": len(current_rows), "events": event_counts},
            events=events,
        )

    async def candidate_history(
        self,
        *,
        page: int,
        page_size: int,
        peer_group: PeerGroup | None,
        event_type: CandidateEventType | None,
        date_from: date | None,
        date_to: date | None,
    ) -> CandidateHistoryResponse:
        self._ensure_local()
        if await self.repository.latest_candidate_snapshot() is None:
            await self.capture_candidates()
        total, rows = await self.repository.candidate_history(
            page=page,
            page_size=page_size,
            peer_group=peer_group.value if peer_group else None,
            event_type=event_type.value if event_type else None,
            date_from=date_from,
            date_to=date_to,
        )
        return CandidateHistoryResponse(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages(total, page_size),
            items=[CandidateHistoryItem.model_validate(row) for row in rows],
        )

    async def create_account(self, request: ForwardAccountCreate) -> ForwardAccountResponse:
        self._ensure_local()
        candidate_snapshot = await self.capture_candidates()
        _, manifest = self._snapshot(candidate_snapshot.data_version)
        weights = {
            sleeve.value: weight
            for sleeve, weight in request.sleeve_weights_bps.as_domain().items()
        }
        account = await self.repository.create_account(
            weights=weights,
            baseline_data_version=candidate_snapshot.data_version,
            baseline_as_of=candidate_snapshot.as_of,
            market_dates=_market_dates(manifest),
        )
        return await self.account_response(account)

    async def current_account(self) -> ForwardAccountResponse | None:
        self._ensure_local()
        account = await self.repository.current_account()
        return await self.account_response(account) if account is not None else None

    async def archive_account(self, account_id: str) -> ForwardAccountResponse:
        self._ensure_local()
        account = await self.repository.archive_account(account_id)
        return await self.account_response(account)

    async def activity(
        self, account_id: str, *, page: int, page_size: int
    ) -> ForwardActivityResponse:
        self._ensure_local()
        if await self.repository.get_account(account_id) is None:
            raise KeyError(account_id)
        total, items = await self.repository.activity(account_id, page=page, page_size=page_size)
        return ForwardActivityResponse(
            account_id=account_id,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages(total, page_size),
            items=items,
        )

    async def account_response(self, account: PaperAccountModel) -> ForwardAccountResponse:
        _, positions, pending, valuations = await self.repository.account_state(account.id)
        latest = valuations[-1] if valuations else None
        values = np.asarray([value.total_value_krw for value in valuations], dtype=float)
        current_value = float(values[-1]) if len(values) else account.initial_capital_krw
        cumulative = current_value / account.initial_capital_krw - 1
        max_drawdown = min((value.drawdown for value in valuations), default=0.0)
        annualized: dict[str, float] | None = None
        if len(values) >= 252:
            returns = np.diff(values) / values[:-1]
            years = max((valuations[-1].as_of - valuations[0].as_of).days / 365.25, 1.0)
            volatility = float(np.std(returns, ddof=1) * math.sqrt(252))
            std = float(np.std(returns, ddof=1))
            annualized = {
                "cagr": round(float((values[-1] / values[0]) ** (1 / years) - 1), 6),
                "annual_volatility": round(volatility, 6),
                "sharpe": round(float(np.mean(returns) / std * math.sqrt(252)), 4)
                if std > 0
                else 0.0,
            }
        warnings = ["실제 주문이 아닌 로컬 연구용 포워드 장부입니다."]
        if account.status == PaperAccountStatus.WAITING_FOR_REVIEW.value:
            warnings.append("계좌 생성 이후 첫 공식 주간 평가를 기다리고 있습니다.")
        if account.review_required_json:
            warnings.append("데이터 이상 종목은 자동 매도하지 않고 검토 필요로 보류합니다.")
        return ForwardAccountResponse(
            account_id=account.id,
            status=PaperAccountStatus(account.status),
            initial_capital_krw=account.initial_capital_krw,
            sleeve_weights_bps={
                str(key): int(value) for key, value in account.weights_json.items()
            },
            baseline_data_version=account.baseline_data_version,
            last_data_version=account.last_data_version,
            last_review_date=account.last_review_date,
            created_at=account.created_at,
            started_at=account.started_at,
            archived_at=account.archived_at,
            current_value_krw=round(current_value, 0),
            cash_krw=round(latest.cash_krw if latest else account.initial_capital_krw, 0),
            invested_krw=round(latest.invested_krw if latest else 0.0, 0),
            cumulative_return=round(cumulative, 6),
            max_drawdown=round(max_drawdown, 6),
            observation_count=len(valuations),
            annualized_metrics=annualized,
            market_dates=latest.market_dates_json if latest else {},
            positions=[
                {
                    "asset_id": model.asset_id,
                    "symbol": model.symbol,
                    "name": model.name,
                    "peer_group": model.peer_group,
                    "quantity": model.quantity,
                    "last_price": model.last_price,
                    "last_score": model.last_score,
                    "currency": model.currency,
                    "review_required": model.review_required,
                }
                for model in positions
            ],
            pending_orders=[
                {
                    "order_id": model.id,
                    "asset_id": model.asset_id,
                    "symbol": model.symbol,
                    "side": model.side,
                    "status": model.status,
                    "scheduled_date": model.scheduled_date.isoformat(),
                    "reason": model.reason,
                }
                for model in pending
            ],
            review_required_assets=list(account.review_required_json or []),
            warnings=warnings,
        )

    async def retry(self, account_id: str) -> ForwardAccountResponse:
        self._ensure_local()
        account = await self.repository.get_account(account_id)
        if account is None:
            raise KeyError(account_id)
        manifest = self.store.current_manifest()
        if manifest is None:
            raise RuntimeError("활성화된 실데이터 스냅샷이 없습니다.")
        await self.process_snapshot(str(manifest["data_version"]), force=True)
        refreshed = await self.repository.get_account(account_id)
        if refreshed is None:
            raise KeyError(account_id)
        return await self.account_response(refreshed)

    async def process_snapshot(self, data_version: str, *, force: bool = False) -> None:
        self._ensure_local()
        await self.capture_candidates(data_version)
        account = await self.repository.current_account()
        if account is None:
            return
        try:
            await self._write_account_score_source(account.id, data_version)
            await self._process_account(account.id, data_version, force=force)
        except Exception as error:
            await self.repository.set_account_error(account.id, str(error))
            raise

    async def _write_account_score_source(self, account_id: str, data_version: str) -> None:
        snapshot, _ = self._snapshot(data_version)
        _, positions, _, _ = await self.repository.account_state(account_id)
        held_ids = [position.asset_id for position in positions]
        latest = pl.read_parquet(snapshot / "scores" / "latest.parquet")
        source = latest.filter(
            pl.col("official_candidate").fill_null(False) | pl.col("asset_id").is_in(held_ids)
        ).select(
            "date",
            "asset_id",
            "symbol",
            "name",
            "peer_group",
            "currency",
            "trend_score",
            "relative_momentum",
            "data_eligible",
            "candidate_eligible",
            "official_candidate",
            "data_status",
            "benchmark_close",
            "benchmark_sma200",
            "score_version",
            "score_config_hash",
        )
        output_dir = self.store.root / "forward" / "accounts" / account_id / "scores"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{data_version}.parquet"
        temp_path = output_path.with_suffix(".tmp.parquet")
        source.write_parquet(temp_path, compression="zstd", statistics=True)
        os.replace(temp_path, output_path)

    async def _process_account(self, account_id: str, data_version: str, *, force: bool) -> None:
        snapshot, manifest = self._snapshot(data_version)
        latest = pl.read_parquet(snapshot / "scores" / "latest.parquet")
        latest_rows = {str(row["asset_id"]): row for row in latest.to_dicts()}
        fx_values = latest.get_column("fx_krw_per_usd").drop_nulls()
        if fx_values.is_empty():
            raise RuntimeError("포워드 평가에 필요한 원/달러 환율이 없습니다.")
        current_fx = float(fx_values.item(-1))
        market_dates = _market_dates(manifest)
        if set(market_dates) != {"US", "KR"}:
            raise RuntimeError("한국·미국 시장 기준일이 모두 필요합니다.")
        valuation_date = max(date.fromisoformat(value) for value in market_dates.values())
        review_date = _completed_review_date(manifest)

        async with self.repository.session_factory() as session, session.begin():
            account = await session.scalar(
                select(PaperAccountModel)
                .where(PaperAccountModel.id == account_id)
                .with_for_update()
            )
            if account is None or account.active_slot != "CURRENT":
                return
            if account.last_data_version == data_version and not force:
                return
            if account.baseline_data_version == data_version:
                account.status = PaperAccountStatus.WAITING_FOR_REVIEW.value
                account.error_message = None
                return

            cash_models = list(
                (
                    await session.scalars(
                        select(PaperCashModel).where(PaperCashModel.account_id == account_id)
                    )
                ).all()
            )
            cash = {Sleeve(model.sleeve): model for model in cash_models}
            positions = {
                model.asset_id: model
                for model in (
                    await session.scalars(
                        select(PaperPositionModel).where(
                            PaperPositionModel.account_id == account_id
                        )
                    )
                ).all()
            }
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
            previous_value = await session.scalar(
                select(PaperValuationModel)
                .where(PaperValuationModel.account_id == account_id)
                .order_by(PaperValuationModel.as_of.desc())
                .limit(1)
            )
            previous_as_of = previous_value.as_of if previous_value else valuation_date
            review_required: set[str] = set()

            action_rows = pl.DataFrame()
            if positions and previous_as_of < valuation_date:
                action_rows = (
                    self.store.scan_bars(
                        snapshot_path=snapshot,
                        start=previous_as_of + timedelta(days=1),
                    )
                    .filter(pl.col("date") <= valuation_date)
                    .filter(pl.col("asset_id").is_in(list(positions)))
                    .select(
                        "date",
                        "asset_id",
                        "split_ratio",
                        "dividend",
                        "recovery_value",
                    )
                    .collect()
                    .sort(["date", "asset_id"])
                )
            for row in action_rows.iter_rows(named=True):
                asset_id = str(row["asset_id"])
                position = positions.get(asset_id)
                if position is None:
                    continue
                current = latest_rows.get(asset_id)
                if current is None or current.get("data_status") != DataStatus.READY.value:
                    position.review_required = True
                    review_required.add(asset_id)
                    continue
                split = row.get("split_ratio")
                if split is not None and float(split) > 0 and float(split) != 1:
                    adjusted_quantity = math.floor(position.quantity * float(split))
                    if adjusted_quantity < 1:
                        position.review_required = True
                        review_required.add(asset_id)
                        continue
                    position.quantity = adjusted_quantity
                dividend = row.get("dividend")
                if dividend is not None and float(dividend) > 0:
                    cash[Sleeve(position.sleeve)].balance += position.quantity * float(dividend)
                recovery = row.get("recovery_value")
                if recovery is None or float(recovery) <= 0:
                    continue
                recovered_on = row["date"]
                recovery_price = float(recovery)
                quantity = position.quantity
                notional = quantity * recovery_price
                cash[Sleeve(position.sleeve)].balance += notional
                system_review = await session.scalar(
                    select(PaperReviewModel).where(
                        PaperReviewModel.account_id == account_id,
                        PaperReviewModel.review_date == recovered_on,
                    )
                )
                if system_review is None:
                    system_review = PaperReviewModel(
                        id=str(uuid.uuid4()),
                        account_id=account_id,
                        review_date=recovered_on,
                        data_version=data_version,
                        status="SYSTEM",
                        details_json={"recoveries": [asset_id]},
                    )
                    session.add(system_review)
                else:
                    details = dict(system_review.details_json or {})
                    recoveries = list(details.get("recoveries", []))
                    if asset_id not in recoveries:
                        recoveries.append(asset_id)
                    details["recoveries"] = recoveries
                    system_review.details_json = details
                recovery_order = PaperOrderModel(
                    id=str(uuid.uuid4()),
                    idempotency_key=(
                        f"{account_id}:{recovered_on.isoformat()}:RECOVERY:{asset_id}"
                    ),
                    account_id=account_id,
                    review_id=system_review.id,
                    asset_id=asset_id,
                    symbol=position.symbol,
                    name=position.name,
                    peer_group=position.peer_group,
                    sleeve=position.sleeve,
                    currency=position.currency,
                    side="SELL",
                    status=PaperOrderStatus.FILLED.value,
                    scheduled_date=recovered_on,
                    filled_date=recovered_on,
                    quantity=quantity,
                    price=recovery_price,
                    notional=notional,
                    cost=0.0,
                    reason="DELISTED_RECOVERY",
                )
                session.add(recovery_order)
                session.add(
                    PaperTradeModel(
                        order_id=recovery_order.id,
                        account_id=account_id,
                        traded_on=recovered_on,
                        asset_id=asset_id,
                        symbol=position.symbol,
                        side="SELL",
                        quantity=quantity,
                        price=recovery_price,
                        notional=notional,
                        cost=0.0,
                        currency=position.currency,
                        reason="DELISTED_RECOVERY",
                    )
                )
                await session.delete(position)
                del positions[asset_id]

            order_bars = pl.DataFrame()
            if pending:
                first_order_date = min(order.scheduled_date for order in pending)
                order_bars = (
                    self.store.scan_bars(
                        snapshot_path=snapshot,
                        start=first_order_date,
                    )
                    .filter(pl.col("date") <= valuation_date)
                    .filter(pl.col("asset_id").is_in([order.asset_id for order in pending]))
                    .filter(pl.col("open").is_not_null() & (pl.col("open") > 0))
                    .select("date", "asset_id", "open")
                    .collect()
                    .sort(["date", "asset_id"])
                )
            order_rows = order_bars.to_dicts()
            for order in sorted(pending, key=lambda item: item.side != "SELL"):
                current = latest_rows.get(order.asset_id)
                if current is None or current.get("data_status") != DataStatus.READY.value:
                    order.status = PaperOrderStatus.DEFERRED.value
                    if order.asset_id in positions:
                        positions[order.asset_id].review_required = True
                        review_required.add(order.asset_id)
                    continue
                fill = next(
                    (
                        row
                        for row in order_rows
                        if row["asset_id"] == order.asset_id and row["date"] >= order.scheduled_date
                    ),
                    None,
                )
                if fill is None:
                    order.status = PaperOrderStatus.DEFERRED.value
                    continue
                price = float(fill["open"])
                sleeve = Sleeve(order.sleeve)
                cost_rate = 0.0015 if order.currency == "USD" else 0.0025
                if order.side == "SELL":
                    position = positions.get(order.asset_id)
                    if position is None:
                        order.status = PaperOrderStatus.CANCELLED.value
                        continue
                    quantity = position.quantity
                    notional = quantity * price
                    cost = notional * cost_rate
                    cash[sleeve].balance += notional - cost
                    await session.delete(position)
                    del positions[order.asset_id]
                else:
                    if order.asset_id in positions:
                        order.status = PaperOrderStatus.CANCELLED.value
                        continue
                    spendable = min(cash[sleeve].target_per_slot, cash[sleeve].balance)
                    quantity = math.floor(spendable / (price * (1 + cost_rate)))
                    if quantity < 1:
                        order.status = PaperOrderStatus.CANCELLED.value
                        continue
                    notional = quantity * price
                    cost = notional * cost_rate
                    cash[sleeve].balance -= notional + cost
                    position = PaperPositionModel(
                        account_id=account_id,
                        asset_id=order.asset_id,
                        symbol=order.symbol,
                        name=order.name,
                        peer_group=order.peer_group,
                        sleeve=order.sleeve,
                        currency=order.currency,
                        quantity=quantity,
                        average_cost=price,
                        last_price=price,
                        last_score=float(current.get("trend_score") or 0),
                        data_status=DataStatus.READY.value,
                        review_required=False,
                    )
                    session.add(position)
                    positions[order.asset_id] = position
                order.status = PaperOrderStatus.FILLED.value
                order.filled_date = fill["date"]
                order.quantity = quantity
                order.price = price
                order.notional = notional
                order.cost = cost
                session.add(
                    PaperTradeModel(
                        order_id=order.id,
                        account_id=account_id,
                        traded_on=fill["date"],
                        asset_id=order.asset_id,
                        symbol=order.symbol,
                        side=order.side,
                        quantity=quantity,
                        price=price,
                        notional=notional,
                        cost=cost,
                        currency=order.currency,
                        reason=order.reason,
                    )
                )

            for asset_id, position in positions.items():
                latest_row = latest_rows.get(asset_id)
                if latest_row is None or latest_row.get("data_status") != DataStatus.READY.value:
                    position.review_required = True
                    review_required.add(asset_id)
                    continue
                close = latest_row.get("close")
                if close is not None and float(close) > 0:
                    position.last_price = float(close)
                position.last_score = float(latest_row.get("trend_score") or position.last_score)
                position.data_status = DataStatus.READY.value
                position.review_required = False
                review_required.discard(asset_id)

            baseline = await session.scalar(
                select(CandidateSnapshotModel).where(
                    CandidateSnapshotModel.data_version == account.baseline_data_version
                )
            )
            baseline_as_of = baseline.as_of if baseline is not None else account.created_at.date()
            should_review = (
                review_date is not None
                and review_date > baseline_as_of
                and (account.last_review_date is None or review_date > account.last_review_date)
            )
            if should_review and review_date is not None:
                for order in pending:
                    if order.side == "BUY" and order.status in {
                        PaperOrderStatus.PENDING.value,
                        PaperOrderStatus.DEFERRED.value,
                    }:
                        order.status = PaperOrderStatus.CANCELLED.value
                readiness = (
                    latest.group_by("peer_group")
                    .agg(
                        pl.col("data_eligible").fill_null(False).sum().alias("eligible"),
                        pl.col("benchmark_sma200").is_not_null().any().alias("benchmark"),
                    )
                    .filter((pl.col("eligible") >= 30) & pl.col("benchmark"))
                )
                if readiness.height != len(PeerGroup):
                    raise RuntimeError("주간 평가에 필요한 여섯 비교군의 준비 조건이 부족합니다.")
                review = await session.scalar(
                    select(PaperReviewModel).where(
                        PaperReviewModel.account_id == account_id,
                        PaperReviewModel.review_date == review_date,
                    )
                )
                if review is None:
                    review = PaperReviewModel(
                        id=str(uuid.uuid4()),
                        account_id=account_id,
                        review_date=review_date,
                        data_version=data_version,
                        status="COMPLETED",
                        details_json={},
                    )
                    session.add(review)
                else:
                    review.data_version = data_version
                    review.status = "COMPLETED"
                if account.started_at is None:
                    for sleeve in (Sleeve.US_STOCK, Sleeve.US_ETF):
                        native = cash[sleeve].balance * (1 - 0.0025) / current_fx
                        cash[sleeve].currency = "USD"
                        cash[sleeve].balance = native
                        cash[sleeve].target_per_slot = native / 3
                    account.started_at = datetime.now(UTC)
                domain_positions = {
                    asset_id: PortfolioPosition(
                        asset_id=asset_id,
                        symbol=model.symbol,
                        name=model.name,
                        peer_group=PeerGroup(model.peer_group),
                        sleeve=Sleeve(model.sleeve),
                        currency=model.currency,
                        quantity=model.quantity,
                        last_score=model.last_score,
                    )
                    for asset_id, model in positions.items()
                }
                config = PortfolioConfig(
                    sleeve_weights_bps={
                        Sleeve(key): int(value) for key, value in account.weights_json.items()
                    }
                )
                plan = plan_weekly_orders(
                    latest.to_dicts(),
                    domain_positions,
                    config=config,
                    pending_sell_ids={
                        order.asset_id
                        for order in pending
                        if order.side == "SELL"
                        and order.status
                        in {
                            PaperOrderStatus.PENDING.value,
                            PaperOrderStatus.DEFERRED.value,
                        }
                    },
                )
                review_required.update(plan.review_required_assets)
                for planned in plan.orders:
                    row = latest_rows[planned.asset_id]
                    group = PeerGroup(str(row["peer_group"]))
                    sleeve = PEER_GROUP_SLEEVE[group]
                    session.add(
                        PaperOrderModel(
                            id=str(uuid.uuid4()),
                            idempotency_key=(
                                f"{account_id}:{review_date.isoformat()}:"
                                f"{planned.side}:{planned.asset_id}"
                            ),
                            account_id=account_id,
                            review_id=review.id,
                            asset_id=planned.asset_id,
                            symbol=str(row["symbol"]),
                            name=str(row["name"]),
                            peer_group=group.value,
                            sleeve=sleeve.value,
                            currency=str(row["currency"]),
                            side=planned.side,
                            status=PaperOrderStatus.PENDING.value,
                            scheduled_date=next_trading_date(review_date, planned.market),
                            reason=planned.reason,
                        )
                    )
                review.details_json = {
                    **dict(review.details_json or {}),
                    "orders": len(plan.orders),
                    "review_required_assets": sorted(review_required),
                    "market_dates": market_dates,
                }
                account.last_review_date = review_date

            cash_krw = sum(
                model.balance * (current_fx if model.currency == "USD" else 1)
                for model in cash.values()
            )
            invested_krw = sum(
                model.quantity * model.last_price * (current_fx if model.currency == "USD" else 1)
                for model in positions.values()
            )
            total_value = cash_krw + invested_krw
            historical_max = await session.scalar(
                select(PaperValuationModel.total_value_krw)
                .where(PaperValuationModel.account_id == account_id)
                .order_by(PaperValuationModel.total_value_krw.desc())
                .limit(1)
            )
            running_max = max(float(historical_max or total_value), total_value)
            existing_value = await session.scalar(
                select(PaperValuationModel).where(
                    PaperValuationModel.account_id == account_id,
                    PaperValuationModel.data_version == data_version,
                )
            )
            if existing_value is None:
                session.add(
                    PaperValuationModel(
                        account_id=account_id,
                        data_version=data_version,
                        as_of=valuation_date,
                        market_dates_json=market_dates,
                        total_value_krw=total_value,
                        cash_krw=cash_krw,
                        invested_krw=invested_krw,
                        benchmark_value_krw=None,
                        drawdown=total_value / running_max - 1,
                    )
                )
            account.last_data_version = data_version
            account.review_required_json = sorted(review_required)
            account.error_message = None
            if review_required:
                account.status = PaperAccountStatus.REVIEW_REQUIRED.value
            elif account.started_at is not None:
                account.status = PaperAccountStatus.ACTIVE.value
            else:
                account.status = PaperAccountStatus.WAITING_FOR_REVIEW.value


settings = get_settings()
forward_service = ForwardService(settings)
