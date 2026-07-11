import csv
import html
import io
import uuid
from datetime import timedelta
from typing import Any

import orjson
import polars as pl
from quant_core import PortfolioConfig, run_reference_backtest
from quant_core.config import PORTFOLIO_VERSION, TREND_SCORE_VERSION
from quant_core.enums import RunStatus
from quant_core.synthetic import DEMO_DATA_VERSION

from quant_api.artifacts import create_artifact_store
from quant_api.database import BacktestRunModel, RunRepository
from quant_api.research import research_service
from quant_api.schemas import BacktestCreate, BacktestResponse
from quant_api.settings import get_settings

settings = get_settings()
repository = RunRepository()
artifact_store = create_artifact_store(settings)


def canonical_hash(request: BacktestCreate) -> str:
    import hashlib

    versioned_request = {
        "data_version": DEMO_DATA_VERSION,
        "score_version": TREND_SCORE_VERSION,
        "portfolio_version": PORTFOLIO_VERSION,
        "request": request.model_dump(),
    }
    payload = orjson.dumps(versioned_request, option=orjson.OPT_SORT_KEYS)
    return hashlib.sha256(DEMO_DATA_VERSION.encode() + b":" + payload).hexdigest()


def response_from_model(model: BacktestRunModel) -> BacktestResponse:
    return BacktestResponse(
        run_id=model.id,
        status=RunStatus(model.status),
        created_at=model.created_at,
        updated_at=model.updated_at,
        config=model.request_json,
        result=model.result_summary,
        error_message=model.error_message,
    )


def _sample(values: list[dict[str, Any]], maximum: int = 320) -> list[dict[str, Any]]:
    if len(values) <= maximum:
        return values
    step = max(1, len(values) // (maximum - 1))
    sampled = values[::step]
    if sampled[-1] != values[-1]:
        sampled.append(values[-1])
    return sampled


def _csv_bytes(result: dict[str, Any]) -> bytes:
    output = io.StringIO()
    fieldnames = [
        "date",
        "asset_id",
        "symbol",
        "side",
        "quantity",
        "price",
        "notional",
        "cost",
        "currency",
        "reason",
        "decision_date",
        "signal_date",
        "score",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(result["trades"])
    return output.getvalue().encode("utf-8-sig")


def _html_bytes(result: dict[str, Any]) -> bytes:
    metrics = result["metrics"]
    rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{value}</td></tr>"
        for key, value in metrics.items()
    )
    document = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>Quant Trend Lab 백테스트</title>
<style>body{{font-family:system-ui;max-width:900px;margin:40px auto;color:#17201b}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:10px;border-bottom:1px solid #d9dfdb;text-align:left}}
.notice{{background:#f3f6f4;padding:16px;border-left:4px solid #1f7a52}}</style></head>
<body><h1>Quant Trend Lab 백테스트 보고서</h1>
<p class="notice">가상 시장 데이터 결과이며 투자 추천이나 상승 확률이 아닙니다.</p>
<p>데이터: {html.escape(result['data_version'])}<br>
전략: {html.escape(result['portfolio_version'])}</p>
<table>{rows}</table></body></html>"""
    return document.encode()


async def execute_backtest(run_id: str, limiter: Any = None, rate_key: str | None = None) -> None:
    try:
        await repository.set_running(run_id)
        model = await repository.get(run_id)
        if model is None:
            raise KeyError(run_id)
        request = BacktestCreate.model_validate(model.request_json)
        config = PortfolioConfig(sleeve_weights_bps=request.sleeve_weights_bps.as_domain())
        result = run_reference_backtest(
            research_service.bars,
            data_version=DEMO_DATA_VERSION,
            portfolio_config=config,
            scored_bars=research_service.scores,
        )
        result.run_id = run_id
        payload = result.as_dict()
        parquet_buffer = io.BytesIO()
        pl.DataFrame(payload["equity_curve"]).write_parquet(parquet_buffer)
        artifact_payloads = {
            "report.html": (_html_bytes(payload), "text/html; charset=utf-8"),
            "trades.csv": (_csv_bytes(payload), "text/csv; charset=utf-8"),
            "result.json": (orjson.dumps(payload), "application/json"),
            "equity.parquet": (parquet_buffer.getvalue(), "application/vnd.apache.parquet"),
        }
        for name, (content, content_type) in artifact_payloads.items():
            object_key = f"runs/{run_id}/{name}"
            size = artifact_store.put(object_key, content, content_type)
            await repository.add_artifact(run_id, name, object_key, content_type, size)
        summary = {
            "data_version": payload["data_version"],
            "score_version": payload["score_version"],
            "portfolio_version": payload["portfolio_version"],
            "started_on": payload["started_on"],
            "ended_on": payload["ended_on"],
            "metrics": payload["metrics"],
            "equity_curve": _sample(payload["equity_curve"]),
            "drawdown_curve": _sample(payload["drawdown_curve"]),
            "final_positions": payload["final_positions"],
            "warnings": payload["warnings"],
        }
        await repository.set_succeeded(run_id, summary)
    except Exception as error:
        await repository.set_failed(run_id, str(error))
        raise
    finally:
        if limiter is not None and rate_key is not None:
            await limiter.release(rate_key)


async def create_run(request: BacktestCreate) -> tuple[BacktestRunModel, bool]:
    digest = canonical_hash(request)
    cached = await repository.find_succeeded(digest)
    if cached is not None:
        return cached, True
    run_id = str(uuid.uuid4())
    await repository.create(run_id, digest, request.model_dump())
    created = await repository.get(run_id)
    if created is None:
        raise RuntimeError("백테스트 실행을 생성하지 못했습니다.")
    return created, False


async def artifact_responses(run_id: str) -> list[dict[str, Any]]:
    artifacts = await repository.artifacts(run_id)
    return [
        {
            "name": artifact.name,
            "content_type": artifact.content_type,
            "size_bytes": artifact.size_bytes,
            "download_url": artifact_store.download_url(
                artifact.object_key, expires_in=timedelta(minutes=10)
            ),
        }
        for artifact in artifacts
    ]
