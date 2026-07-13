import asyncio
import csv
import hashlib
import html
import io
import shutil
import uuid
from dataclasses import asdict
from datetime import timedelta
from typing import Any

import orjson
import polars as pl
from quant_core import MARKET_EVENT_VERSION, REPLAY_ANALYSIS_VERSION, PortfolioConfig
from quant_core.config import (
    PORTFOLIO_VERSION,
    REPLAY_PORTFOLIO_VERSION,
    REPLAY_SCORE_VERSION,
    TREND_SCORE_VERSION,
)
from quant_core.enums import RunKind, RunStatus

from quant_api.backtests import artifact_store
from quant_api.database import BacktestRunModel, RunRepository
from quant_api.replay_strategy import strategy_domain
from quant_api.research_quality import QUALITY_POLICY_VERSION
from quant_api.research_replay import REPLAY_ENGINE_VERSION, ReplayBuild, ResearchReplayEngine
from quant_api.research_store import ResearchSnapshotStore
from quant_api.schemas import ReplayCreate, ReplayResponse, SleeveWeights
from quant_api.settings import get_settings

settings = get_settings()
repository = RunRepository()
snapshot_store = ResearchSnapshotStore(settings.research_root)
replay_engine = ResearchReplayEngine(snapshot_store)


class ReplayCancelled(RuntimeError):
    pass


def _canonical_hash(request: ReplayCreate, manifest: dict[str, Any]) -> str:
    payload = {
        "data_version": manifest["data_version"],
        "bars_sha256": manifest["bars_sha256"],
        "score_version": (
            REPLAY_SCORE_VERSION if request.strategy is not None else TREND_SCORE_VERSION
        ),
        "portfolio_version": (
            REPLAY_PORTFOLIO_VERSION if request.strategy is not None else PORTFOLIO_VERSION
        ),
        "quality_policy": QUALITY_POLICY_VERSION,
        "market_event": MARKET_EVENT_VERSION,
        "replay_engine": REPLAY_ENGINE_VERSION,
        "replay_analysis": REPLAY_ANALYSIS_VERSION,
        "request": request.model_dump(mode="json", exclude_none=True),
    }
    return hashlib.sha256(orjson.dumps(payload, option=orjson.OPT_SORT_KEYS)).hexdigest()


def _sample(values: list[dict[str, Any]], maximum: int = 400) -> list[dict[str, Any]]:
    if len(values) <= maximum:
        return values
    step = max(1, len(values) // (maximum - 1))
    sampled = values[::step]
    if sampled[-1] != values[-1]:
        sampled.append(values[-1])
    return sampled


def _trade_csv(payload: dict[str, Any]) -> bytes:
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
    writer.writerows(payload["trades"])
    return output.getvalue().encode("utf-8-sig")


def _daily_rows(build: ReplayBuild) -> list[dict[str, Any]]:
    return [
        {
            **asdict(row),
            "sleeve": row.sleeve.value,
        }
        for row in build.actual_run.daily_ledger
    ]


def _review_rows(build: ReplayBuild) -> list[dict[str, Any]]:
    return [
        {
            **asdict(row),
            "peer_group": row.peer_group.value,
        }
        for row in build.actual_run.review_ledger
    ]


def _round_trip_rows(build: ReplayBuild) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in build.actual_run.round_trips:
        payload = asdict(item)
        payload["peer_group"] = item.peer_group.value
        payload["sleeve"] = item.sleeve.value
        payload["entry_date"] = item.entry_date.isoformat()
        payload["exit_date"] = item.exit_date.isoformat() if item.exit_date else None
        rows.append(payload)
    return rows


def _parquet_bytes(rows: list[dict[str, Any]]) -> bytes:
    output = io.BytesIO()
    pl.DataFrame(rows).write_parquet(output, compression="zstd", statistics=True)
    return output.getvalue()


def _round_trip_csv(rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO()
    fieldnames = [
        "asset_id",
        "symbol",
        "name",
        "peer_group",
        "sleeve",
        "currency",
        "status",
        "entry_date",
        "exit_date",
        "entry_score",
        "exit_score",
        "quantity",
        "entry_price",
        "exit_price",
        "entry_notional_krw",
        "exit_value_krw",
        "dividends_krw",
        "costs_krw",
        "net_pnl_krw",
        "net_return",
        "holding_days",
        "exit_reason",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8-sig")


def _percent(value: float) -> str:
    return f"{value * 100:,.2f}%"


def _krw(value: float) -> str:
    return f"{value:,.0f}원"


def _report_html(payload: dict[str, Any]) -> bytes:
    analysis = payload["analysis"]
    headline = analysis["headline"]
    gap = analysis["gap_analysis"]
    metrics = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{value}</td></tr>"
        for key, value in payload["metrics"].items()
    )
    gap_rows = "".join(
        f"<tr><th>{label}</th><td>{_percent(float(gap[key]))}</td></tr>"
        for key, label in (
            ("full_benchmark_return", "완전투자 벤치마크"),
            ("exposure_effect", "시장 노출·진입 제한 효과"),
            ("selection_execution_effect", "종목 선택·체결 효과"),
            ("cost_effect", "매매·환전 비용 효과"),
            ("actual_strategy_return", "실제 전략"),
        )
    )
    annual_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['period']))}</td>"
        f"<td>{_percent(float(row['strategy_return']))}</td>"
        f"<td>{_percent(float(row['benchmark_return']))}</td>"
        f"<td>{_percent(float(row['excess_return']))}</td>"
        f"<td>{_percent(float(row['average_exposure']))}</td>"
        "</tr>"
        for row in analysis["annual_periods"]
    )
    sleeve_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['sleeve']))}</td>"
        f"<td>{_krw(float(row['ending_value_krw']))}</td>"
        f"<td>{_krw(float(row['pnl_krw']))}</td>"
        f"<td>{_percent(float(row['contribution']))}</td>"
        "</tr>"
        for row in analysis["sleeve_attribution"]
    )
    check_rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(row['label']))}</td>"
        f"<td>{html.escape(str(row['status']))}</td>"
        f"<td>{html.escape(str(row['detail']))}</td>"
        "</tr>"
        for row in analysis["integrity_checks"]
    )
    warnings = "".join(f"<li>{html.escape(str(warning))}</li>" for warning in payload["warnings"])
    document = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>실데이터 과거 시뮬레이션</title>
<style>body{{font-family:system-ui;max-width:960px;margin:40px auto;color:#17201b}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:10px;border-bottom:1px solid #d9dfdb;text-align:left}}
.notice{{background:#fff6df;padding:16px;border-left:4px solid #b7791f}}
.summary{{background:#edf2ef;padding:18px;border-left:4px solid #176b48}}
h2{{margin-top:32px}}</style></head>
<body><h1>Quant Trend Lab 과거 시뮬레이션</h1>
<div class="notice"><strong>현재 상장 종목 기준·생존편향 포함</strong>
<p>개인 연구용 실제 시장 데이터 결과이며 투자 추천이나 공식 성과가 아닙니다.</p></div>
<p>데이터 버전: {html.escape(payload["data_version"])}<br>
전략 버전: {html.escape(payload["portfolio_version"])}<br>
분석 버전: {html.escape(analysis["version"])}</p>
<div class="summary"><strong>{html.escape(headline["title"])}</strong>
<p>{html.escape(headline["summary"])}</p></div>
<h2>핵심 지표</h2><table>{metrics}</table>
<h2>성과 차이 분해</h2><table>{gap_rows}</table>
<h2>연도별 성과</h2><table><thead><tr><th>연도</th><th>전략</th><th>벤치마크</th>
<th>초과수익</th><th>평균 노출</th></tr></thead><tbody>{annual_rows}</tbody></table>
<h2>자산군 기여</h2><table><thead><tr><th>자산군</th><th>최종 가치</th><th>손익</th>
<th>전체 기여</th></tr></thead><tbody>{sleeve_rows}</tbody></table>
<h2>무결성 검사</h2><table><thead><tr><th>검사</th><th>상태</th><th>내용</th></tr></thead>
<tbody>{check_rows}</tbody></table><ul>{warnings}</ul></body></html>"""
    return document.encode()


def response_from_model(model: BacktestRunModel) -> ReplayResponse:
    total = model.total_units
    progress = 0.0 if total <= 0 else min(100.0, model.completed_units / total * 100)
    if model.status == RunStatus.SUCCEEDED.value:
        progress = 100.0
    strategy = model.request_json.get("strategy") if isinstance(model.request_json, dict) else None
    universe_mode = (
        ((strategy or {}).get("data") or {}).get("universe_mode")
        if isinstance(strategy, dict)
        else None
    )
    bias_warning = (
        "각 평가일에 유효한 시점 기준 종목군을 사용했습니다."
        if universe_mode == "POINT_IN_TIME"
        else "현재 상장 종목 기준으로 생존편향이 포함됩니다."
    )
    return ReplayResponse(
        run_id=model.id,
        status=RunStatus(model.status),
        stage=model.stage,
        completed_units=model.completed_units,
        total_units=total,
        progress_percent=round(progress, 1),
        data_version=model.data_version or "unknown",
        created_at=model.created_at,
        updated_at=model.updated_at,
        config=model.request_json,
        result=model.result_summary,
        error_message=model.error_message,
        bias_warning=bias_warning,
    )


async def create_replay(
    request: ReplayCreate,
) -> tuple[BacktestRunModel, bool]:
    if settings.app_mode != "local_research":
        raise PermissionError("실데이터 과거 재생은 local_research 모드에서만 사용할 수 있습니다.")
    manifest = snapshot_store.current_manifest()
    if manifest is None:
        raise RuntimeError("활성화된 실데이터 스냅샷이 없습니다.")
    digest = _canonical_hash(request, manifest)
    run_kind = (
        RunKind.REAL_REPLAY_V2.value if request.strategy is not None else RunKind.REAL_REPLAY.value
    )
    cached = await repository.find_succeeded(digest, run_kind=run_kind)
    if cached is not None:
        return cached, True
    run_id = str(uuid.uuid4())
    await repository.create(
        run_id,
        digest,
        request.model_dump(mode="json", exclude_none=True),
        run_kind=run_kind,
        data_version=str(manifest["data_version"]),
    )
    model = await repository.get(run_id)
    if model is None:
        raise RuntimeError("과거 시뮬레이션 실행을 생성하지 못했습니다.")
    return model, False


async def execute_replay(run_id: str) -> None:
    model = await repository.get(run_id)
    if model is None or model.data_version is None:
        raise KeyError(run_id)
    loop = asyncio.get_running_loop()

    def progress(stage: str, completed: int, total: int) -> None:
        cancelled = asyncio.run_coroutine_threadsafe(
            repository.cancellation_requested(run_id), loop
        ).result(timeout=30)
        if cancelled:
            raise ReplayCancelled("사용자가 과거 시뮬레이션을 취소했습니다.")
        future = asyncio.run_coroutine_threadsafe(
            repository.set_progress(run_id, stage, completed, total), loop
        )
        future.result(timeout=30)

    try:
        if await repository.cancellation_requested(run_id):
            await repository.set_cancelled(run_id)
            return
        snapshot_store.acquire_lease(model.data_version, run_id)
    except Exception as error:
        await repository.set_failed(run_id, str(error))
        raise
    try:
        await repository.set_running(run_id)
        request = ReplayCreate.model_validate(model.request_json)
        if request.strategy is None:
            config = PortfolioConfig(
                sleeve_weights_bps=(request.sleeve_weights_bps or SleeveWeights()).as_domain()
            )
            engine_options: dict[str, Any] = {}
        else:
            score_config, config = strategy_domain(request.strategy)
            engine_options = {
                "score_config": score_config,
                "start_date": request.strategy.data.start_date,
                "split_date": request.strategy.data.split_date,
                "end_date": request.strategy.data.end_date,
                "universe_mode": request.strategy.data.universe_mode,
                "walk_forward_train_years": (request.strategy.validation.walk_forward_train_years),
                "walk_forward_test_years": request.strategy.validation.walk_forward_test_years,
                "walk_forward_step_years": request.strategy.validation.walk_forward_step_years,
            }
        build = await asyncio.to_thread(
            replay_engine.run,
            run_id,
            data_version=model.data_version,
            portfolio_config=config,
            progress=progress,
            **engine_options,
        )
        build.result.config_hash = model.config_hash[:16]
        payload = build.result.as_dict()
        payload["analysis"] = build.analysis.analysis
        payload["strategy_config"] = (
            request.strategy.model_dump(mode="json") if request.strategy is not None else None
        )
        payload["validation"] = build.validation
        payload["walk_forward"] = build.walk_forward
        payload["stress_tests"] = build.stress_tests
        parquet = io.BytesIO()
        pl.DataFrame(payload["equity_curve"]).write_parquet(parquet)
        round_trip_rows = _round_trip_rows(build)
        artifacts = {
            "report.html": (_report_html(payload), "text/html; charset=utf-8"),
            "trades.csv": (_trade_csv(payload), "text/csv; charset=utf-8"),
            "round-trips.csv": (
                _round_trip_csv(round_trip_rows),
                "text/csv; charset=utf-8",
            ),
            "result.json": (orjson.dumps(payload), "application/json"),
            "analysis.json": (
                orjson.dumps(build.analysis.analysis),
                "application/json",
            ),
            "strategy-config.json": (
                orjson.dumps(payload["strategy_config"]),
                "application/json",
            ),
            "validation.json": (
                orjson.dumps(payload["validation"]),
                "application/json",
            ),
            "robustness.json": (
                orjson.dumps(
                    {
                        "walk_forward": payload["walk_forward"],
                        "stress_tests": payload["stress_tests"],
                    }
                ),
                "application/json",
            ),
            "equity.parquet": (
                parquet.getvalue(),
                "application/vnd.apache.parquet",
            ),
            "daily-ledger.parquet": (
                _parquet_bytes(_daily_rows(build)),
                "application/vnd.apache.parquet",
            ),
            "review-ledger.parquet": (
                _parquet_bytes(_review_rows(build)),
                "application/vnd.apache.parquet",
            ),
        }
        if request.strategy is None:
            for legacy_optional in (
                "strategy-config.json",
                "validation.json",
                "robustness.json",
            ):
                artifacts.pop(legacy_optional)
        for name, (content, content_type) in artifacts.items():
            object_key = f"research-runs/{run_id}/{name}"
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
            "review_required_assets": payload["review_required_assets"],
            "warnings": payload["warnings"],
            "cache_hit": build.cache_hit,
            "cache_key": build.cache_key,
            "analysis": build.analysis.analysis,
            "strategy_config": payload["strategy_config"],
            "validation": payload["validation"],
            "walk_forward": payload["walk_forward"],
            "stress_tests": payload["stress_tests"],
        }
        await repository.set_succeeded(run_id, summary)
    except ReplayCancelled:
        await repository.set_cancelled(run_id)
    except Exception as error:
        await repository.set_failed(run_id, str(error))
        raise
    finally:
        snapshot_store.release_lease(model.data_version, run_id)


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


async def recover_interrupted_replays() -> None:
    for run_kind in (
        RunKind.REAL_REPLAY.value,
        RunKind.REAL_REPLAY_V2.value,
        RunKind.REPLAY_SWEEP.value,
    ):
        interrupted = await repository.fail_interrupted(run_kind=run_kind)
        for run_id, data_version in interrupted:
            if data_version is not None:
                snapshot_store.release_lease(data_version, run_id)
            if run_kind == RunKind.REPLAY_SWEEP.value:
                shutil.rmtree(
                    snapshot_store.root / "sweep-work" / run_id,
                    ignore_errors=True,
                )
