import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from quant_api.database import Base, PaperTradeModel
from quant_api.forward import ForwardService
from quant_api.forward_repository import ForwardLedgerRepository
from quant_api.research_store import ResearchSnapshotStore
from quant_api.schemas import ForwardAccountCreate
from quant_api.settings import Settings
from quant_core.enums import PeerGroup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _write_snapshot(
    root: Path,
    version: str,
    *,
    us_date: date,
    kr_date: date,
    invalid_asset_id: str | None = None,
) -> None:
    snapshot = root / "snapshots" / version
    rows: list[dict[str, object]] = []
    bars_by_group: dict[PeerGroup, list[dict[str, object]]] = {group: [] for group in PeerGroup}
    for group in PeerGroup:
        market_date = us_date if group.value.startswith("US_") else kr_date
        currency = "USD" if group.value.startswith("US_") else "KRW"
        for index in range(31):
            asset_id = f"{group.value}:ASSET-{index:02d}"
            is_invalid = asset_id == invalid_asset_id
            rows.append(
                {
                    "asset_id": asset_id,
                    "date": market_date,
                    "symbol": f"A{index:02d}",
                    "name": f"Asset {index:02d}",
                    "peer_group": group.value,
                    "currency": currency,
                    "open": 100.0 + index,
                    "close": 101.0 + index,
                    "trend_score": 10.0 if is_invalid else 80.0 - index / 10,
                    "relative_momentum": 0.2 - index / 1000,
                    "data_eligible": not is_invalid,
                    "candidate_eligible": not is_invalid,
                    "official_candidate": not is_invalid,
                    "benchmark_close": 110.0,
                    "benchmark_sma200": 100.0,
                    "data_status": "INVALID_DATA" if is_invalid else "READY",
                    "fx_krw_per_usd": 1_350.0,
                    "score_version": "trend-score-v1.0.0",
                    "score_config_hash": "fixture-score-config",
                }
            )
            bars_by_group[group].append(
                {
                    "date": market_date,
                    "asset_id": asset_id,
                    "peer_group": group.value,
                    "open": 100.0 + index,
                    "close": 101.0 + index,
                    "split_ratio": 1.0,
                    "dividend": 0.0,
                    "recovery_value": None,
                }
            )
    (snapshot / "scores").mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(snapshot / "scores" / "latest.parquet")
    for group, group_rows in bars_by_group.items():
        path = snapshot / "bars" / f"peer_group={group.value}" / f"year={us_date.year}"
        path.mkdir(parents=True)
        pl.DataFrame(group_rows).write_parquet(path / "bars.parquet")
    coverage = {
        group.value: {"as_of": (us_date if group.value.startswith("US_") else kr_date).isoformat()}
        for group in PeerGroup
    }
    manifest = {
        "data_version": version,
        "coverage": coverage,
        "snapshot_path": f"snapshots/{version}",
    }
    (snapshot / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.mark.asyncio
async def test_forward_review_fill_and_retry_are_idempotent(tmp_path: Path) -> None:
    research_root = tmp_path / "research"
    _write_snapshot(
        research_root,
        "data-v1",
        us_date=date(2025, 7, 3),
        kr_date=date(2025, 7, 4),
    )
    _write_snapshot(
        research_root,
        "data-v2",
        us_date=date(2025, 7, 11),
        kr_date=date(2025, 7, 11),
    )
    _write_snapshot(
        research_root,
        "data-v3",
        us_date=date(2025, 7, 14),
        kr_date=date(2025, 7, 14),
    )
    _write_snapshot(
        research_root,
        "data-v4",
        us_date=date(2025, 7, 18),
        kr_date=date(2025, 7, 18),
        invalid_asset_id="US_STOCK:ASSET-00",
    )
    (research_root / "current.json").write_text(
        json.dumps(
            {
                "data_version": "data-v1",
                "manifest_path": "snapshots/data-v1/manifest.json",
            }
        ),
        encoding="utf-8",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = ForwardLedgerRepository(session_factory)
    settings = Settings(
        app_mode="local_research",
        artifact_backend="local",
        research_root=research_root,
        research_auto_sync=False,
    )
    service = ForwardService(
        settings,
        repository=repository,
        store=ResearchSnapshotStore(research_root),
    )

    created = await service.create_account(ForwardAccountCreate())
    await service.process_snapshot("data-v2")
    reviewed = await repository.get_account(created.account_id)
    _, _, pending, _ = await repository.account_state(created.account_id)

    assert reviewed is not None
    assert reviewed.status == "ACTIVE"
    assert reviewed.last_review_date == date(2025, 7, 11)
    assert len(pending) == 12

    await service.process_snapshot("data-v3")
    await service.process_snapshot("data-v3")
    _, positions, pending, valuations = await repository.account_state(created.account_id)
    async with session_factory() as session:
        trades = int((await session.scalar(select(func.count(PaperTradeModel.id)))) or 0)

    assert len(positions) == 12
    assert pending == []
    assert trades == 12
    assert len(valuations) == 3
    assert all(position.quantity == int(position.quantity) for position in positions)

    await service.process_snapshot("data-v4")
    review_account = await repository.get_account(created.account_id)
    _, positions, _, _ = await repository.account_state(created.account_id)
    async with session_factory() as session:
        trades_after_error = int(
            (await session.scalar(select(func.count(PaperTradeModel.id)))) or 0
        )

    assert review_account is not None
    assert review_account.status == "REVIEW_REQUIRED"
    assert review_account.review_required_json == ["US_STOCK:ASSET-00"]
    assert "US_STOCK:ASSET-00" in {position.asset_id for position in positions}
    assert trades_after_error == trades
    score_source = pl.read_parquet(
        research_root / "forward" / "accounts" / created.account_id / "scores" / "data-v4.parquet"
    )
    assert (
        score_source.filter(pl.col("asset_id") == "US_STOCK:ASSET-00")
        .get_column("data_status")
        .item()
        == "INVALID_DATA"
    )
    await engine.dispose()
