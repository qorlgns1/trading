import asyncio
import csv
import gc
import hashlib
import io
import itertools
import math
import shutil
import uuid
from dataclasses import replace
from datetime import date
from typing import Any

import orjson
import polars as pl
from pydantic import ValidationError
from quant_core.config import PEER_GROUP_SLEEVE, PortfolioConfig
from quant_core.enums import (
    ExperimentObjective,
    ExperimentRunRole,
    RunKind,
    RunStatus,
    UniverseMode,
)
from quant_core.market_portfolio import simulate_prepared_replay, slice_prepared_replay
from quant_core.replay_validation import period_metrics
from scipy.stats import spearmanr

from quant_api.backtests import artifact_store
from quant_api.database import (
    BacktestRunModel,
    ReplayExperimentModel,
    ReplayExperimentRepository,
    RunRepository,
)
from quant_api.replay_strategy import (
    default_period,
    default_success_criteria,
    strategy_domain,
    success_assessment,
)
from quant_api.research_replay import REPLAY_ENGINE_VERSION, ResearchReplayEngine
from quant_api.research_replays import create_replay
from quant_api.research_store import ResearchSnapshotStore
from quant_api.schemas import (
    ReplayAccepted,
    ReplayComparisonResponse,
    ReplayCreate,
    ReplayExperimentCreate,
    ReplayExperimentListResponse,
    ReplayExperimentPatch,
    ReplayExperimentResponse,
    ReplayExperimentRunCreate,
    ReplayExperimentSummary,
    ReplayOptionsResponse,
    ReplayStrategyConfig,
    ReplaySuccessCriteria,
    ReplaySweepCreate,
    ReplaySweepResponse,
)
from quant_api.settings import get_settings

settings = get_settings()
run_repository = RunRepository()
experiment_repository = ReplayExperimentRepository()
snapshot_store = ResearchSnapshotStore(settings.research_root)
replay_engine = ResearchReplayEngine(snapshot_store)

SWEEP_VERSION = "replay-sweep-v1.0.0"
ALLOWED_SWEEP_PATHS = {
    "signal.entry_score",
    "signal.exit_score",
    "signal.minimum_adv_multiplier",
    "signal.market_gate_mode",
    "signal.require_above_sma200",
    "signal.require_positive_six_month",
    "signal.require_absolute_liquidity",
    "signal.require_order_size_liquidity",
    "portfolio.position_sizing",
    "portfolio.replacement_policy",
    "portfolio.replacement_score_gap",
    "risk.fixed_stop_loss",
    "risk.trailing_stop_loss",
    "execution.review_frequency",
    "execution.execution_delay_sessions",
    "execution.us_buy_cost",
    "execution.us_sell_cost",
    "execution.kr_buy_cost",
    "execution.kr_sell_cost",
    "execution.initial_fx_cost",
    "execution.slippage_bps",
}
SCORE_WEIGHT_PREFIX = "signal.component_weights_bps."
SLEEVE_WEIGHT_PREFIX = "portfolio.sleeve_weights_bps."
SLOT_PREFIX = "portfolio.peer_group_slots."
PEER_OVERRIDE_PREFIX = "signal.peer_overrides."


class SweepCancelled(RuntimeError):
    pass


def _ensure_local() -> None:
    if settings.app_mode != "local_research":
        raise PermissionError("전략 실험실은 local_research 모드에서만 사용할 수 있습니다.")


def _manifest() -> dict[str, Any]:
    manifest = snapshot_store.current_manifest()
    if manifest is None:
        raise RuntimeError("활성화된 실데이터 스냅샷이 없습니다.")
    return manifest


def replay_options() -> ReplayOptionsResponse:
    _ensure_local()
    manifest = _manifest()
    period = default_period(manifest)
    default_strategy = ReplayStrategyConfig(data=period)
    return ReplayOptionsResponse(
        data_version=str(manifest["data_version"]),
        raw_history_start=date.fromisoformat(str(manifest["history_start"])),
        raw_history_end=date.fromisoformat(str(manifest["requested_end"])),
        supports_point_in_time=bool(manifest.get("supports_point_in_time", False)),
        universe_modes=(
            [UniverseMode.CURRENT_LISTED, UniverseMode.POINT_IN_TIME]
            if manifest.get("supports_point_in_time", False)
            else [UniverseMode.CURRENT_LISTED]
        ),
        default_strategy=default_strategy,
        limits={
            "challengers": 3,
            "sweep_axes": 2,
            "sweep_combinations": 100,
            "entry_score": [50, 100, 5],
            "exit_score": [50, 95, 5],
            "execution_delay_sessions": [1, 5],
            "forward_baseline_accounts": 1,
            "forward_experiment_accounts": 3,
        },
    )


def _validate_strategy_capabilities(
    strategy: ReplayStrategyConfig, manifest: dict[str, Any]
) -> None:
    if strategy.data.universe_mode is UniverseMode.POINT_IN_TIME and not bool(
        manifest.get("supports_point_in_time", False)
    ):
        raise ValueError("현재 데이터에는 시점 기준 종목군 이력이 없습니다.")
    raw_start = date.fromisoformat(str(manifest["history_start"]))
    raw_end = date.fromisoformat(str(manifest["requested_end"]))
    if strategy.data.start_date < raw_start or strategy.data.end_date > raw_end:
        raise ValueError("선택한 기간이 현재 데이터 범위를 벗어납니다.")
    if (strategy.data.split_date - strategy.data.start_date).days < 330:
        raise ValueError("학습 구간은 최소 약 1년이어야 합니다.")
    if (strategy.data.end_date - strategy.data.split_date).days < 330:
        raise ValueError("검증 구간은 최소 약 1년이어야 합니다.")
    strategy_domain(strategy)


async def _experiment_response(model: ReplayExperimentModel) -> ReplayExperimentResponse:
    links = await experiment_repository.runs(model.id)
    runs = [
        {
            "run_id": run.id,
            "role": link.role,
            "label": link.label,
            "status": run.status,
            "stage": run.stage,
            "config": run.request_json,
            "result": run.result_summary,
            "error_message": run.error_message,
            "created_at": run.created_at,
            "updated_at": run.updated_at,
        }
        for link, run in links
    ]
    return ReplayExperimentResponse(
        experiment_id=model.id,
        name=model.name,
        hypothesis=model.hypothesis,
        objective=ExperimentObjective(model.objective),
        status=model.status,
        data_version=model.data_version,
        universe_mode=UniverseMode(model.universe_mode),
        run_count=len(runs),
        archived=model.archived,
        created_at=model.created_at,
        updated_at=model.updated_at,
        notes=model.notes,
        success_criteria=ReplaySuccessCriteria.model_validate(model.success_criteria_json),
        period=model.period_json,
        runs=runs,
    )


async def create_experiment(
    request: ReplayExperimentCreate,
) -> tuple[ReplayExperimentResponse, list[str]]:
    _ensure_local()
    manifest = _manifest()
    _validate_strategy_capabilities(request.baseline_strategy, manifest)
    criteria = request.success_criteria or default_success_criteria(request.objective)
    experiment = await experiment_repository.create(
        experiment_id=str(uuid.uuid4()),
        name=request.name,
        hypothesis=request.hypothesis,
        objective=request.objective.value,
        success_criteria=criteria.model_dump(),
        data_version=str(manifest["data_version"]),
        universe_mode=request.baseline_strategy.data.universe_mode.value,
        period=request.baseline_strategy.data.model_dump(mode="json"),
    )
    run, cached = await create_replay(ReplayCreate(strategy=request.baseline_strategy))
    await experiment_repository.attach_run(
        experiment.id,
        run.id,
        role=ExperimentRunRole.BASELINE.value,
        label=request.baseline_label,
    )
    return await _experiment_response(experiment), ([] if cached else [run.id])


async def list_experiments(*, include_archived: bool = False) -> ReplayExperimentListResponse:
    _ensure_local()
    models = await experiment_repository.list_experiments(include_archived=include_archived)
    items: list[ReplayExperimentSummary] = []
    for model in models:
        run_count = len(await experiment_repository.runs(model.id))
        items.append(
            ReplayExperimentSummary(
                experiment_id=model.id,
                name=model.name,
                hypothesis=model.hypothesis,
                objective=ExperimentObjective(model.objective),
                status=model.status,
                data_version=model.data_version,
                universe_mode=UniverseMode(model.universe_mode),
                run_count=run_count,
                archived=model.archived,
                created_at=model.created_at,
                updated_at=model.updated_at,
            )
        )
    return ReplayExperimentListResponse(total=len(items), items=items)


async def get_experiment(experiment_id: str) -> ReplayExperimentResponse:
    _ensure_local()
    model = await experiment_repository.get(experiment_id)
    if model is None:
        raise KeyError(experiment_id)
    return await _experiment_response(model)


async def patch_experiment(
    experiment_id: str, request: ReplayExperimentPatch
) -> ReplayExperimentResponse:
    _ensure_local()
    model = await experiment_repository.update(
        experiment_id,
        name=request.name,
        notes=request.notes,
        archived=request.archived,
    )
    return await _experiment_response(model)


async def add_experiment_run(
    experiment_id: str,
    request: ReplayExperimentRunCreate,
) -> tuple[ReplayAccepted, bool]:
    _ensure_local()
    experiment = await experiment_repository.get(experiment_id)
    if experiment is None:
        raise KeyError(experiment_id)
    if experiment.archived:
        raise ValueError("보관된 실험에는 실행을 추가할 수 없습니다.")
    _validate_strategy_capabilities(request.strategy, _manifest())
    if request.strategy.data.model_dump(mode="json") != experiment.period_json:
        raise ValueError("한 실험의 전략은 동일한 데이터 기간과 종목군을 사용해야 합니다.")
    links = await experiment_repository.runs(experiment_id)
    challenger_count = sum(
        link.role in {ExperimentRunRole.CHALLENGER.value, ExperimentRunRole.PARETO.value}
        for link, _ in links
    )
    if (
        request.role in {ExperimentRunRole.CHALLENGER, ExperimentRunRole.PARETO}
        and challenger_count >= 3
    ):
        raise ValueError("한 실험에는 도전 전략을 최대 3개까지 추가할 수 있습니다.")
    run, cached = await create_replay(ReplayCreate(strategy=request.strategy))
    if any(existing.id == run.id for _, existing in links):
        raise ValueError("같은 설정의 전략이 이미 이 실험에 포함되어 있습니다.")
    await experiment_repository.attach_run(
        experiment_id,
        run.id,
        role=request.role.value,
        label=request.label,
    )
    return (
        ReplayAccepted(
            run_id=run.id,
            status=RunStatus.SUCCEEDED if cached else RunStatus.QUEUED,
            cached=cached,
        ),
        cached,
    )


def _set_nested(payload: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cursor: dict[str, Any] = payload
    for part in parts[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            cursor[part] = next_value
        cursor = next_value
    cursor[parts[-1]] = value


def _get_nested(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _normalize_weights(current: dict[str, int], selected: dict[str, int]) -> dict[str, int]:
    if any(value < 0 or value > 10_000 for value in selected.values()):
        raise ValueError("가중치는 0~10,000bp여야 합니다.")
    selected_total = sum(selected.values())
    if selected_total > 10_000:
        raise ValueError("선택한 가중치 합계가 10,000bp를 초과합니다.")
    others = [key for key in current if key not in selected]
    remaining = 10_000 - selected_total
    if not others:
        if remaining != 0:
            raise ValueError("전체 가중치 합계는 10,000bp여야 합니다.")
        return dict(selected)
    base_total = sum(current[key] for key in others)
    raw = {
        key: (remaining / len(others) if base_total == 0 else remaining * current[key] / base_total)
        for key in others
    }
    allocated = {key: math.floor(value) for key, value in raw.items()}
    remainder = remaining - sum(allocated.values())
    for key in sorted(others, key=lambda item: (-(raw[item] - allocated[item]), item))[:remainder]:
        allocated[key] += 1
    return {**allocated, **selected}


def _variants(request: ReplaySweepCreate) -> list[ReplayStrategyConfig]:
    for axis in request.axes:
        if not (
            axis.path in ALLOWED_SWEEP_PATHS
            or axis.path.startswith(SCORE_WEIGHT_PREFIX)
            or axis.path.startswith(SLEEVE_WEIGHT_PREFIX)
            or axis.path.startswith(SLOT_PREFIX)
            or axis.path.startswith(PEER_OVERRIDE_PREFIX)
        ):
            raise ValueError(f"지원하지 않는 민감도 분석 축입니다: {axis.path}")
    variants: list[ReplayStrategyConfig] = []
    for values in itertools.product(*(axis.values for axis in request.axes)):
        payload = request.base_strategy.model_dump(mode="json")
        selected_weights: dict[str, int] = {}
        selected_sleeve_weights: dict[str, int] = {}
        for axis, value in zip(request.axes, values, strict=True):
            if axis.path.startswith(SCORE_WEIGHT_PREFIX):
                selected_weights[axis.path.removeprefix(SCORE_WEIGHT_PREFIX)] = int(value)
            elif axis.path.startswith(SLEEVE_WEIGHT_PREFIX):
                selected_sleeve_weights[axis.path.removeprefix(SLEEVE_WEIGHT_PREFIX)] = int(value)
            else:
                _set_nested(payload, axis.path, value)
        if selected_weights:
            current = payload["signal"]["component_weights_bps"]
            payload["signal"]["component_weights_bps"] = _normalize_weights(
                {str(key): int(value) for key, value in current.items()}, selected_weights
            )
        if selected_sleeve_weights:
            current = payload["portfolio"]["sleeve_weights_bps"]
            payload["portfolio"]["sleeve_weights_bps"] = _normalize_weights(
                {str(key): int(value) for key, value in current.items()},
                selected_sleeve_weights,
            )
        try:
            variants.append(ReplayStrategyConfig.model_validate(payload))
        except ValidationError as error:
            raise ValueError(f"민감도 조합이 유효하지 않습니다: {error}") from error
    unique: dict[bytes, ReplayStrategyConfig] = {}
    for variant in variants:
        encoded = orjson.dumps(variant.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS)
        unique[encoded] = variant
    return list(unique.values())


async def create_sweep(
    experiment_id: str, request: ReplaySweepCreate
) -> tuple[ReplayAccepted, bool]:
    _ensure_local()
    experiment = await experiment_repository.get(experiment_id)
    if experiment is None:
        raise KeyError(experiment_id)
    if experiment.archived:
        raise ValueError("보관된 실험에는 민감도 분석을 추가할 수 없습니다.")
    variants = _variants(request)
    if not variants:
        raise ValueError("실행할 민감도 조합이 없습니다.")
    for strategy in variants:
        _validate_strategy_capabilities(strategy, _manifest())
        if strategy.data.model_dump(mode="json") != experiment.period_json:
            raise ValueError("민감도 분석은 실험과 같은 기간을 사용해야 합니다.")
    payload = {
        "version": SWEEP_VERSION,
        "experiment_id": experiment_id,
        **request.model_dump(mode="json"),
    }
    digest = hashlib.sha256(
        orjson.dumps(
            {
                "engine": REPLAY_ENGINE_VERSION,
                "data_version": experiment.data_version,
                "request": payload,
            },
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()
    cached = await run_repository.find_succeeded(digest, run_kind=RunKind.REPLAY_SWEEP.value)
    if cached is None:
        run_id = str(uuid.uuid4())
        await run_repository.create(
            run_id,
            digest,
            payload,
            run_kind=RunKind.REPLAY_SWEEP.value,
            data_version=experiment.data_version,
        )
        run = await run_repository.get(run_id)
        if run is None:
            raise RuntimeError("민감도 분석 작업을 생성하지 못했습니다.")
    else:
        run = cached
    await experiment_repository.attach_run(
        experiment_id,
        run.id,
        role=ExperimentRunRole.SWEEP.value,
        label=request.label,
    )
    return (
        ReplayAccepted(
            run_id=run.id,
            status=RunStatus.SUCCEEDED if cached is not None else RunStatus.QUEUED,
            cached=cached is not None,
        ),
        cached is not None,
    )


def _pareto_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        cagr = float(row["training"]["cagr"])
        risk = abs(float(row["training"]["max_drawdown"]))
        dominated = any(
            float(other["training"]["cagr"]) >= cagr
            and abs(float(other["training"]["max_drawdown"])) <= risk
            and (
                float(other["training"]["cagr"]) > cagr + 0.0001
                or abs(float(other["training"]["max_drawdown"])) < risk - 0.0001
            )
            for other in rows
            if other is not row
        )
        if not dominated:
            result.append(row)
    return result


def _winner_concentration(run: Any) -> dict[str, float]:
    profits = sorted(
        (float(item.net_pnl_krw) for item in run.round_trips if item.net_pnl_krw > 0),
        reverse=True,
    )
    total = sum(profits)
    return {
        "top_1_profit_ratio": round(sum(profits[:1]) / total, 4) if total else 0.0,
        "top_3_profit_ratio": round(sum(profits[:3]) / total, 4) if total else 0.0,
    }


def _enabled_groups(config: PortfolioConfig) -> set[Any]:
    return {
        group
        for group in config.peer_group_slots
        if config.peer_group_slots[group] > 0
        and config.sleeve_weights_bps[PEER_GROUP_SLEEVE[group]] > 0
    }


def _prepared_with_signals(base: Any, signals: pl.DataFrame) -> Any:
    first_review = signals.get_column("review_date").min()
    if not isinstance(first_review, date):
        raise RuntimeError("민감도 조합의 평가 기간을 결정할 수 없습니다.")
    sliced = slice_prepared_replay(base, start=first_review, end=base.dates[-1])
    by_review = {
        key[0] if isinstance(key, tuple) else key: part.drop("review_date").to_dicts()
        for key, part in signals.partition_by("review_date", as_dict=True).items()
    }
    return replace(
        sliced,
        signals_by_review=by_review,
        review_dates=signals.get_column("review_date").unique().sort().to_list(),
    )


def _sweep_result(
    *,
    experiment_id: str,
    axes: list[dict[str, Any]],
    trial_count: int,
    rows: list[dict[str, Any]],
    invalid_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    pareto = _pareto_rows(rows)
    train_sorted = sorted(rows, key=lambda item: item["training"]["cagr"], reverse=True)
    validation_sorted = sorted(rows, key=lambda item: item["validation"]["cagr"], reverse=True)
    validation_rank = {row["index"]: rank for rank, row in enumerate(validation_sorted, 1)}
    top_count = max(1, math.ceil(len(rows) * 0.10))
    train_top = {row["index"] for row in train_sorted[:top_count]}
    validation_top = {row["index"] for row in validation_sorted[:top_count]}
    correlation = spearmanr(
        [row["training"]["cagr"] for row in rows],
        [row["validation"]["cagr"] for row in rows],
    ).statistic
    boundary_axes: list[str] = []
    for axis in axes:
        values = axis["values"]
        numeric_values = [value for value in values if isinstance(value, int | float)]
        if len(numeric_values) != len(values):
            continue
        lower, upper = min(numeric_values), max(numeric_values)
        if any(_get_nested(row["strategy"], str(axis["path"])) in {lower, upper} for row in pareto):
            boundary_axes.append(str(axis["path"]))
    trade_counts = [int(row["trade_count"]) for row in rows]
    concentrations = [float(row["winner_concentration"]["top_3_profit_ratio"]) for row in rows]
    overlap = len(train_top & validation_top)
    warnings: list[str] = []
    if invalid_rows:
        warnings.append(
            f"{len(invalid_rows)}개 조합은 진입 가능한 후보가 없어 비교에서 제외됐습니다."
        )
    if len(rows) >= 20 and abs(float(correlation)) < 0.3:
        warnings.append("학습·검증 순위 상관이 낮아 조건 선택이 불안정할 수 있습니다.")
    if overlap / top_count < 0.3:
        warnings.append("학습 상위 10% 조합이 검증 상위권에서 거의 재현되지 않았습니다.")
    if boundary_axes:
        warnings.append("Pareto 후보가 시험 범위 경계에 있어 범위를 넓힌 재검증이 필요합니다.")
    if max(concentrations, default=0.0) >= 0.5:
        warnings.append("일부 조합은 상위 3개 거래에 이익이 크게 집중되었습니다.")
    diagnostics = {
        "trial_count": trial_count,
        "valid_trial_count": len(rows),
        "invalid_trial_count": len(invalid_rows),
        "train_validation_spearman": (
            round(float(correlation), 4) if math.isfinite(float(correlation)) else 0.0
        ),
        "top_decile_overlap": overlap,
        "top_decile_size": top_count,
        "top_decile_overlap_rate": round(overlap / top_count, 4),
        "best_train_validation_rank": validation_rank[train_sorted[0]["index"]],
        "trade_count": {
            "minimum": min(trade_counts),
            "median": float(sorted(trade_counts)[len(trade_counts) // 2]),
            "maximum": max(trade_counts),
        },
        "maximum_top_3_profit_ratio": round(max(concentrations, default=0.0), 4),
        "pareto_boundary_axes": boundary_axes,
        "warnings": warnings,
    }
    return {
        "version": SWEEP_VERSION,
        "experiment_id": experiment_id,
        "axes": axes,
        "trial_count": trial_count,
        "valid_trial_count": len(rows),
        "pareto": pareto,
        "diagnostics": diagnostics,
        "rows": rows,
        "invalid_rows": invalid_rows,
    }


def _sweep_tabular_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "index": row["index"],
            "training_cagr": row["training"]["cagr"],
            "training_mdd": row["training"]["max_drawdown"],
            "validation_cagr": row["validation"]["cagr"],
            "validation_mdd": row["validation"]["max_drawdown"],
            "trade_count": row["trade_count"],
            "transaction_cost_krw": row["transaction_cost_krw"],
        }
        for row in rows
    ]


def _sweep_artifact_payloads(
    result: dict[str, Any],
) -> tuple[tuple[str, bytes, str], ...]:
    fieldnames = [
        "index",
        "training_cagr",
        "training_mdd",
        "validation_cagr",
        "validation_mdd",
        "trade_count",
        "transaction_cost_krw",
    ]
    tabular_rows = _sweep_tabular_rows(result["rows"])
    csv_output = io.StringIO()
    writer = csv.DictWriter(csv_output, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(tabular_rows)
    parquet = io.BytesIO()
    pl.DataFrame(tabular_rows).write_parquet(parquet, compression="zstd")
    return (
        ("sweep.json", orjson.dumps(result), "application/json"),
        ("sweep.csv", csv_output.getvalue().encode("utf-8-sig"), "text/csv"),
        ("sweep.parquet", parquet.getvalue(), "application/vnd.apache.parquet"),
    )


async def execute_sweep(run_id: str) -> None:
    model = await run_repository.get(run_id)
    if model is None or model.data_version is None:
        raise KeyError(run_id)
    request = ReplaySweepCreate.model_validate(model.request_json)
    variants = _variants(request)
    if await run_repository.cancellation_requested(run_id):
        await run_repository.set_cancelled(run_id)
        return
    snapshot_store.acquire_lease(model.data_version, run_id)
    work_root = snapshot_store.root / "sweep-work" / run_id
    shutil.rmtree(work_root, ignore_errors=True)
    work_root.mkdir(parents=True, exist_ok=True)
    try:
        await run_repository.set_running(run_id)
        loop = asyncio.get_running_loop()

        def check_cancelled(_: str, __: int, ___: int) -> None:
            cancelled = asyncio.run_coroutine_threadsafe(
                run_repository.cancellation_requested(run_id), loop
            ).result(timeout=30)
            if cancelled:
                raise SweepCancelled("사용자가 민감도 분석을 취소했습니다.")

        domain_configs = [strategy_domain(strategy) for strategy in variants]
        by_frequency: dict[Any, list[int]] = {}
        for index, (_, portfolio_config) in enumerate(domain_configs):
            by_frequency.setdefault(portfolio_config.review_frequency, []).append(index)
        signal_paths: dict[int, Any] = {}
        metadata_frames: list[pl.DataFrame] = []
        invalid_combinations: list[dict[str, Any]] = []
        first_review_date: date | None = None
        score_root: Any = None
        built_signals = 0
        for frequency, indexes in by_frequency.items():
            union_groups = set().union(
                *(_enabled_groups(domain_configs[index][1]) for index in indexes)
            )
            representative_score = domain_configs[indexes[0]][0]
            strategy = variants[indexes[0]]
            context = await asyncio.to_thread(
                replay_engine.signal_context,
                data_version=model.data_version,
                score_config=representative_score,
                frequency=frequency,
                enabled_groups=union_groups,
                start_date=strategy.data.start_date,
                end_date=strategy.data.end_date,
                universe_mode=strategy.data.universe_mode,
                progress=check_cancelled,
            )
            score_root = context.score_root
            for index in indexes:
                if await run_repository.cancellation_requested(run_id):
                    raise SweepCancelled("사용자가 민감도 분석을 취소했습니다.")
                score_config, portfolio_config = domain_configs[index]
                try:
                    signals, metadata = await asyncio.to_thread(
                        replay_engine.project_context,
                        context,
                        score_config=score_config,
                        portfolio_config=portfolio_config,
                        enabled_groups=_enabled_groups(portfolio_config),
                    )
                except RuntimeError as error:
                    if "진입 가능한 후보" not in str(error):
                        raise
                    invalid_combinations.append(
                        {
                            "index": index,
                            "strategy": variants[index].model_dump(mode="json"),
                            "error": str(error),
                        }
                    )
                    built_signals += 1
                    await run_repository.set_progress(
                        run_id,
                        "SWEEP_SIGNALS",
                        built_signals,
                        len(variants),
                    )
                    continue
                signal_path = work_root / f"signals-{index:03d}.parquet"
                await asyncio.to_thread(
                    signals.write_parquet,
                    signal_path,
                    compression="zstd",
                    statistics=True,
                )
                signal_paths[index] = signal_path
                metadata_frames.append(metadata)
                current_first = signals.get_column("review_date").min()
                if not isinstance(current_first, date):
                    raise RuntimeError("민감도 조합의 시작일을 결정할 수 없습니다.")
                first_review_date = (
                    current_first
                    if first_review_date is None
                    else min(first_review_date, current_first)
                )
                built_signals += 1
                await run_repository.set_progress(
                    run_id,
                    "SWEEP_SIGNALS",
                    built_signals,
                    len(variants),
                )
            del context
            gc.collect()
        if score_root is None or first_review_date is None or not metadata_frames:
            raise RuntimeError("민감도 분석 입력을 준비하지 못했습니다.")
        metadata = pl.concat(metadata_frames, how="diagonal_relaxed").unique("asset_id")
        seed_signals = metadata.with_columns(
            pl.lit(first_review_date).cast(pl.Date).alias("signal_date"),
            pl.lit(first_review_date).cast(pl.Date).alias("review_date"),
            pl.lit(100.0).alias("trend_score"),
            pl.lit(0.0).alias("relative_momentum"),
            pl.lit(0.2).alias("vol60"),
            pl.lit(True).alias("data_eligible"),
            pl.lit(True).alias("candidate_eligible"),
            pl.lit(1.0).alias("benchmark_close"),
            pl.lit(0.0).alias("benchmark_sma200"),
        )
        base_prepared = await asyncio.to_thread(
            replay_engine.prepare_market,
            data_version=model.data_version,
            score_root=score_root,
            signals=seed_signals,
            metadata=metadata,
            portfolio_config=domain_configs[0][1],
            end_date=variants[0].data.end_date,
        )
        del metadata_frames, metadata, seed_signals
        gc.collect()

        rows: list[dict[str, Any]] = []
        for index, strategy in enumerate(variants):
            if await run_repository.cancellation_requested(run_id):
                raise SweepCancelled("사용자가 민감도 분석을 취소했습니다.")
            if index not in signal_paths:
                await run_repository.set_progress(run_id, "SWEEP", index + 1, len(variants))
                continue
            score_config, portfolio_config = domain_configs[index]
            signals = await asyncio.to_thread(pl.read_parquet, signal_paths[index])
            prepared = _prepared_with_signals(base_prepared, signals)
            del signals
            actual_run = await asyncio.to_thread(
                simulate_prepared_replay,
                prepared,
                data_version=model.data_version,
                score_version=score_config.version,
                portfolio_config=portfolio_config,
                run_id=f"{run_id}-{index}",
                prices_are_split_adjusted=True,
                progress=lambda completed, total: check_cancelled(
                    "SWEEP_SIMULATE", completed, total
                ),
            )
            split_index = next(
                position
                for position, current in enumerate(prepared.dates)
                if current >= strategy.data.split_date
            )
            training = period_metrics(
                actual_run.equity_values,
                prepared.dates,
                start_index=0,
                end_index=split_index,
                initial_capital=portfolio_config.initial_capital_krw,
            )
            validation = period_metrics(
                actual_run.equity_values,
                prepared.dates,
                start_index=split_index,
                end_index=len(prepared.dates),
                initial_capital=portfolio_config.initial_capital_krw,
            )
            rows.append(
                {
                    "index": index,
                    "strategy": strategy.model_dump(mode="json"),
                    "full": dict(actual_run.result.metrics),
                    "training": training,
                    "validation": validation,
                    "trade_count": len(actual_run.result.trades),
                    "transaction_cost_krw": round(
                        sum(item.transaction_cost_krw for item in actual_run.daily_ledger),
                        0,
                    ),
                    "winner_concentration": _winner_concentration(actual_run),
                }
            )
            signal_paths[index].unlink(missing_ok=True)
            del prepared, actual_run
            gc.collect()
            await run_repository.set_progress(run_id, "SWEEP", index + 1, len(variants))
        result = _sweep_result(
            experiment_id=str(model.request_json["experiment_id"]),
            axes=model.request_json["axes"],
            trial_count=len(variants),
            rows=rows,
            invalid_rows=invalid_combinations,
        )
        for name, content, content_type in _sweep_artifact_payloads(result):
            object_key = f"research-runs/{run_id}/{name}"
            size = artifact_store.put(object_key, content, content_type)
            await run_repository.add_artifact(run_id, name, object_key, content_type, size)
        await run_repository.set_succeeded(run_id, result)
    except SweepCancelled:
        await run_repository.set_cancelled(run_id)
    except Exception as error:
        await run_repository.set_failed(run_id, str(error))
        raise
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
        snapshot_store.release_lease(model.data_version, run_id)


async def get_sweep(run_id: str) -> ReplaySweepResponse:
    _ensure_local()
    model = await run_repository.get(run_id)
    if model is None or model.run_kind != RunKind.REPLAY_SWEEP.value:
        raise KeyError(run_id)
    total = model.total_units
    progress = 0.0 if total <= 0 else min(100.0, model.completed_units / total * 100)
    return ReplaySweepResponse(
        run_id=model.id,
        experiment_id=str(model.request_json["experiment_id"]),
        status=RunStatus(model.status),
        stage=model.stage,
        completed_units=model.completed_units,
        total_units=model.total_units,
        progress_percent=round(progress, 1),
        result=model.result_summary,
        error_message=model.error_message,
    )


def _validation_metrics(run: BacktestRunModel) -> dict[str, float]:
    result = run.result_summary or {}
    validation = result.get("validation") or {}
    independent = validation.get("independent_validation") or {}
    metrics = independent.get("metrics") or result.get("metrics") or {}
    return {
        str(key): float(value) for key, value in metrics.items() if isinstance(value, int | float)
    }


def _full_metrics(run: BacktestRunModel) -> dict[str, float]:
    metrics = (run.result_summary or {}).get("metrics") or {}
    return {
        str(key): float(value) for key, value in metrics.items() if isinstance(value, int | float)
    }


def _comparison_explanation(
    *,
    classification: str,
    cagr_change_pp: float,
    mdd_improvement_pp: float,
    cost_change_krw: float,
    trade_change: int,
) -> str:
    if classification == "기준":
        return "도전 전략의 변화량을 계산하는 사용자 선택 기준입니다."
    if classification == "표본 부족":
        return "검증 거래가 30회 미만이라 우연과 전략 효과를 구분하기 어렵습니다."
    if classification == "비용 과다":
        return (
            f"기준보다 거래가 {trade_change:+d}회, 비용이 {cost_change_krw:+,.0f}원 변해 "
            "비용 부담이 커졌습니다."
        )
    if classification == "수익형":
        return (
            f"검증 CAGR은 {cagr_change_pp:+.2f}%p, 최대 낙폭 개선 폭은 "
            f"{mdd_improvement_pp:+.2f}%p입니다."
        )
    if classification == "방어형":
        return (
            f"검증 최대 낙폭이 {mdd_improvement_pp:+.2f}%p 개선되고 CAGR은 "
            f"{cagr_change_pp:+.2f}%p 변했습니다."
        )
    return (
        f"검증 CAGR {cagr_change_pp:+.2f}%p, 최대 낙폭 {mdd_improvement_pp:+.2f}%p로 "
        "수익과 위험 개선이 함께 재현되지 않았습니다."
    )


async def experiment_comparison(experiment_id: str) -> ReplayComparisonResponse:
    _ensure_local()
    experiment = await experiment_repository.get(experiment_id)
    if experiment is None:
        raise KeyError(experiment_id)
    linked = await experiment_repository.runs(experiment_id)
    succeeded = [(link, run) for link, run in linked if run.status == RunStatus.SUCCEEDED.value]
    baseline_pair = next(
        ((link, run) for link, run in succeeded if link.role == ExperimentRunRole.BASELINE.value),
        None,
    )
    if baseline_pair is None:
        return ReplayComparisonResponse(experiment_id=experiment_id)
    _, baseline = baseline_pair
    baseline_metrics = _validation_metrics(baseline)
    baseline_full = _full_metrics(baseline)
    baseline_analysis = (baseline.result_summary or {}).get("analysis") or {}
    baseline_cost = float((baseline_analysis.get("cost_summary") or {}).get("explicit_cost_krw", 0))
    criteria = ReplaySuccessCriteria.model_validate(experiment.success_criteria_json)
    objective = ExperimentObjective(experiment.objective)
    runs: list[dict[str, Any]] = []
    assessments: list[dict[str, Any]] = []
    for link, run in succeeded:
        metrics = _validation_metrics(run)
        full_metrics = _full_metrics(run)
        analysis = (run.result_summary or {}).get("analysis") or {}
        cost = float((analysis.get("cost_summary") or {}).get("explicit_cost_krw", 0))
        trade_count = int(metrics.get("trade_count", 0))
        if run.id == baseline.id:
            classification = "기준"
        elif trade_count < 30:
            classification = "표본 부족"
        elif cost > baseline_cost * 1.20 and baseline_cost > 0:
            classification = "비용 과다"
        elif metrics.get("cagr", 0) > baseline_metrics.get("cagr", 0) and abs(
            metrics.get("max_drawdown", 0)
        ) <= abs(baseline_metrics.get("max_drawdown", 0)):
            classification = "수익형"
        elif abs(metrics.get("max_drawdown", 0)) < abs(baseline_metrics.get("max_drawdown", 0)):
            classification = "방어형"
        else:
            classification = "불안정"
        cagr_change_pp = (metrics.get("cagr", 0) - baseline_metrics.get("cagr", 0)) * 100
        mdd_improvement_pp = (
            abs(baseline_metrics.get("max_drawdown", 0)) - abs(metrics.get("max_drawdown", 0))
        ) * 100
        cost_change = cost - baseline_cost
        trade_change = round(
            full_metrics.get("trade_count", 0) - baseline_full.get("trade_count", 0)
        )
        differences = {
            "final_value_krw": round(
                full_metrics.get("final_value_krw", 0) - baseline_full.get("final_value_krw", 0),
                0,
            ),
            "cagr_pp": round(cagr_change_pp, 3),
            "mdd_improvement_pp": round(mdd_improvement_pp, 3),
            "sharpe": round(metrics.get("sharpe", 0) - baseline_metrics.get("sharpe", 0), 3),
            "cost_krw": round(cost_change, 0),
            "trade_count": trade_change,
            "average_exposure_pp": round(
                (full_metrics.get("average_exposure", 0) - baseline_full.get("average_exposure", 0))
                * 100,
                2,
            ),
        }
        runs.append(
            {
                "run_id": run.id,
                "label": link.label,
                "role": link.role,
                "metrics": metrics,
                "full_metrics": full_metrics,
                "classification": classification,
                "cost_krw": cost,
                "strategy": (run.result_summary or {}).get("strategy_config"),
                "differences": differences,
                "explanation": _comparison_explanation(
                    classification=classification,
                    cagr_change_pp=cagr_change_pp,
                    mdd_improvement_pp=mdd_improvement_pp,
                    cost_change_krw=cost_change,
                    trade_change=trade_change,
                ),
            }
        )
        if run.id != baseline.id:
            assessments.append(
                {
                    "run_id": run.id,
                    **success_assessment(
                        objective=objective,
                        criteria=criteria,
                        baseline=baseline_metrics,
                        candidate=metrics,
                        baseline_cost=baseline_cost,
                        candidate_cost=cost,
                    ),
                }
            )
    return ReplayComparisonResponse(
        experiment_id=experiment_id,
        baseline_run_id=baseline.id,
        runs=runs,
        success_assessments=assessments,
    )
