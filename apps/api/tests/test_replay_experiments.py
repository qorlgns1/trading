import io
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import orjson
import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st
from quant_api import replay_experiments
from quant_api.database import (
    BacktestRunModel,
    Base,
    ReplayExperimentRepository,
    RunRepository,
)
from quant_api.replay_experiments import (
    _normalize_weights,
    _pareto_rows,
    _sweep_artifact_payloads,
    _sweep_result,
    _variants,
)
from quant_api.replay_strategy import strategy_domain, strategy_hash
from quant_api.research_replay import REPLAY_FEATURE_COLUMNS, _scheduled_signals
from quant_api.schemas import (
    ReplayDataConfig,
    ReplayExperimentCreate,
    ReplayExperimentPatch,
    ReplayExperimentRunCreate,
    ReplayStrategyConfig,
    ReplaySweepAxis,
    ReplaySweepCreate,
)
from quant_core.config import PortfolioConfig, TrendScoreConfig
from quant_core.enums import ExperimentObjective, ExperimentRunRole, PeerGroup, RunKind, Sleeve
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _strategy() -> ReplayStrategyConfig:
    return ReplayStrategyConfig(
        data=ReplayDataConfig(
            start_date=date(2017, 1, 2),
            split_date=date(2021, 1, 4),
            end_date=date(2025, 1, 3),
        )
    )


def _manifest() -> dict[str, Any]:
    return {
        "data_version": "data-v1",
        "history_start": "2010-01-01",
        "requested_end": "2026-01-01",
        "supports_point_in_time": True,
    }


@pytest.fixture
async def repositories(
    tmp_path: Path,
) -> AsyncIterator[tuple[RunRepository, ReplayExperimentRepository]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield RunRepository(session_factory), ReplayExperimentRepository(session_factory)
    await engine.dispose()


def _use_repositories(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> tuple[RunRepository, ReplayExperimentRepository]:
    runs, experiments = repositories
    monkeypatch.setattr(replay_experiments, "run_repository", runs)
    monkeypatch.setattr(replay_experiments, "experiment_repository", experiments)
    monkeypatch.setattr(replay_experiments, "_ensure_local", lambda: None)
    monkeypatch.setattr(replay_experiments, "_manifest", _manifest)
    return runs, experiments


def _fake_create_replay(runs: RunRepository) -> Any:
    async def create(request: Any) -> tuple[BacktestRunModel, bool]:
        assert request.strategy is not None
        digest = strategy_hash(request.strategy)
        cached = await runs.find_succeeded(digest, run_kind=RunKind.REAL_REPLAY_V2.value)
        if cached is not None:
            return cached, True
        run_id = f"replay-{digest[:12]}"
        if await runs.get(run_id) is None:
            await runs.create(
                run_id,
                digest,
                request.model_dump(mode="json"),
                run_kind=RunKind.REAL_REPLAY_V2.value,
                data_version="data-v1",
            )
        model = await runs.get(run_id)
        assert model is not None
        return model, False

    return create


async def _create_experiment_record(
    experiments: ReplayExperimentRepository,
    *,
    experiment_id: str = "experiment-1",
) -> None:
    await experiments.create(
        experiment_id=experiment_id,
        name="진입 기준 비교",
        hypothesis="높은 진입 점수는 낙폭을 줄인다.",
        objective=ExperimentObjective.BALANCED.value,
        success_criteria={
            "minimum_sharpe_improvement": 0.1,
            "maximum_cagr_degradation_pp": 0.5,
            "maximum_mdd_degradation_pp": 1.0,
        },
        data_version="data-v1",
        universe_mode="CURRENT_LISTED",
        period=_strategy().data.model_dump(mode="json"),
    )


@given(
    long_term=st.integers(min_value=0, max_value=10_000),
    relative=st.integers(min_value=0, max_value=10_000),
)
def test_weight_sweep_uses_exact_largest_remainder_total(long_term: int, relative: int) -> None:
    if long_term + relative > 10_000:
        with pytest.raises(ValueError):
            _normalize_weights(
                {
                    "long_term_trend": 3000,
                    "absolute_momentum": 2500,
                    "relative_strength": 2000,
                    "high_proximity": 1000,
                    "volatility_stability": 1000,
                    "trading_activity": 500,
                },
                {"long_term_trend": long_term, "relative_strength": relative},
            )
        return
    normalized = _normalize_weights(
        {
            "long_term_trend": 3000,
            "absolute_momentum": 2500,
            "relative_strength": 2000,
            "high_proximity": 1000,
            "volatility_stability": 1000,
            "trading_activity": 500,
        },
        {"long_term_trend": long_term, "relative_strength": relative},
    )
    assert sum(normalized.values()) == 10_000
    assert normalized["long_term_trend"] == long_term
    assert normalized["relative_strength"] == relative


def test_sweep_builds_valid_weight_and_threshold_combinations() -> None:
    request = ReplaySweepCreate(
        base_strategy=_strategy(),
        axes=[
            ReplaySweepAxis(
                path="signal.component_weights_bps.long_term_trend",
                values=[2500, 3500],
            ),
            ReplaySweepAxis(path="signal.entry_score", values=[65, 75]),
        ],
    )

    variants = _variants(request)

    assert len(variants) == 4
    assert {item.signal.entry_score for item in variants} == {65, 75}
    assert all(
        sum(item.signal.component_weights_bps.model_dump().values()) == 10_000 for item in variants
    )
    assert len({strategy_hash(item) for item in variants}) == 4


def test_sleeve_sweep_rebalances_unselected_allocations_to_100_percent() -> None:
    request = ReplaySweepCreate(
        base_strategy=_strategy(),
        axes=[
            ReplaySweepAxis(
                path="portfolio.sleeve_weights_bps.us_stock",
                values=[2000, 4000],
            )
        ],
    )

    variants = _variants(request)

    assert len(variants) == 2
    assert {item.portfolio.sleeve_weights_bps.us_stock for item in variants} == {
        2000,
        4000,
    }
    assert all(
        sum(item.portfolio.sleeve_weights_bps.model_dump().values()) == 10_000 for item in variants
    )


def test_default_v2_domain_keeps_legacy_trade_rules() -> None:
    _, portfolio = strategy_domain(_strategy())

    assert portfolio.entry_score == 65
    assert portfolio.exit_score == 60
    assert portfolio.initial_capital_krw == 50_000_000
    assert portfolio.execution_delay_sessions == 1
    assert portfolio.us_buy_cost == 0.0015
    assert portfolio.kr_sell_cost == 0.0025
    assert sum(portfolio.peer_group_slots.values()) == 12


def test_strategy_hash_changes_for_validation_and_execution_settings() -> None:
    baseline = _strategy()
    changed = baseline.model_copy(
        update={"execution": baseline.execution.model_copy(update={"slippage_bps": 10})}
    )

    assert strategy_hash(changed) != strategy_hash(baseline)


def test_pareto_keeps_only_non_dominated_training_results() -> None:
    rows = [
        {"index": 0, "training": {"cagr": 0.10, "max_drawdown": -0.20}},
        {"index": 1, "training": {"cagr": 0.11, "max_drawdown": -0.18}},
        {"index": 2, "training": {"cagr": 0.09, "max_drawdown": -0.12}},
    ]

    assert {row["index"] for row in _pareto_rows(rows)} == {1, 2}


def test_point_in_time_membership_is_applied_before_peer_readiness(tmp_path: Path) -> None:
    group = PeerGroup.US_STOCK
    first = date(2024, 1, 5)
    second = date(2024, 1, 12)
    rows = []
    for current in (first, second):
        for index in range(31):
            rows.append(
                {
                    "date": current,
                    "asset_id": f"US_STOCK:{index:03d}",
                    "symbol": f"S{index:03d}",
                    "name": f"Asset {index}",
                    "peer_group": group.value,
                    "currency": "USD",
                    "adjusted_close": 120.0,
                    "sma200": 100.0,
                    "r126": 0.2,
                    "relative_momentum": 0.1,
                    "vol60": 0.2,
                    "adv60": 20_000_000.0,
                    "data_eligible": True,
                    "peer_count": 31,
                    "benchmark_close": 120.0,
                    "benchmark_sma200": 100.0,
                    "long_term_trend_unit": 1.0,
                    "absolute_momentum_unit": 1.0,
                    "relative_strength_unit": 1.0,
                    "high_proximity_unit": 1.0,
                    "volatility_stability_unit": 1.0,
                    "trading_activity_unit": 1.0,
                }
            )
    output = tmp_path / "peer_group=US_STOCK" / "year=2024" / "scores.parquet"
    output.parent.mkdir(parents=True)
    pl.DataFrame(rows).select(REPLAY_FEATURE_COLUMNS).write_parquet(output)
    schedule = pl.DataFrame(
        {
            "peer_group": [group.value, group.value],
            "signal_date": [first, second],
            "review_date": [first, second],
        }
    )
    membership = pl.DataFrame(
        {
            "asset_id": [f"US_STOCK:{index:03d}" for index in range(31)],
            "valid_from": [date(2020, 1, 1)] * 31,
            "valid_to": [first, *([None] * 30)],
        }
    )
    slots = {item: 0 for item in PeerGroup}
    slots[group] = 3
    weights = {item: 0 for item in Sleeve}
    weights[Sleeve.US_STOCK] = 10_000

    signals, _ = _scheduled_signals(
        tmp_path,
        schedule,
        score_config=TrendScoreConfig(),
        portfolio_config=PortfolioConfig(
            sleeve_weights_bps=weights,
            peer_group_slots=slots,
        ),
        enabled_groups={group},
        point_in_time_membership=membership,
    )

    counts = signals.group_by("review_date").len().sort("review_date")
    assert counts.get_column("len").to_list() == [31, 30]


@pytest.mark.asyncio
async def test_experiment_links_runs_and_queued_cancel_is_immediate(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'experiments.db'}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    runs = RunRepository(session_factory)
    experiments = ReplayExperimentRepository(session_factory)
    await runs.create(
        "run-1",
        "hash-1",
        {"strategy": _strategy().model_dump(mode="json")},
        run_kind="REAL_REPLAY_V2",
        data_version="data-v1",
    )
    experiment = await experiments.create(
        experiment_id="experiment-1",
        name="진입 기준 비교",
        hypothesis="높은 진입 점수는 낙폭을 줄인다.",
        objective="BALANCED",
        success_criteria={},
        data_version="data-v1",
        universe_mode="CURRENT_LISTED",
        period=_strategy().data.model_dump(mode="json"),
    )
    await experiments.attach_run(experiment.id, "run-1", role="BASELINE", label="기준")

    linked = await experiments.runs(experiment.id)
    cancelled = await runs.request_cancel("run-1")

    assert [(link.role, run.id) for link, run in linked] == [("BASELINE", "run-1")]
    assert cancelled.status == "CANCELLED"
    assert cancelled.cancellation_requested is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_experiment_crud_and_archive_visibility(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = _use_repositories(monkeypatch, repositories)
    monkeypatch.setattr(replay_experiments, "create_replay", _fake_create_replay(runs))

    created, queued_run_ids = await replay_experiments.create_experiment(
        ReplayExperimentCreate(
            name="진입 기준 비교",
            hypothesis="높은 진입 점수는 낙폭을 줄인다.",
            objective=ExperimentObjective.BALANCED,
            baseline_strategy=_strategy(),
        )
    )

    assert created.run_count == 1
    assert created.runs[0]["role"] == ExperimentRunRole.BASELINE.value
    assert queued_run_ids == [created.runs[0]["run_id"]]
    assert (await replay_experiments.list_experiments()).total == 1
    assert (await replay_experiments.get_experiment(created.experiment_id)).name == created.name
    without_succeeded_baseline = await replay_experiments.experiment_comparison(
        created.experiment_id
    )
    assert without_succeeded_baseline.baseline_run_id is None

    patched = await replay_experiments.patch_experiment(
        created.experiment_id,
        ReplayExperimentPatch(name="보관된 실험", notes="결론 기록", archived=True),
    )
    assert patched.status == "ARCHIVED"
    assert patched.notes == "결론 기록"
    assert (await replay_experiments.list_experiments()).total == 0
    archived = await replay_experiments.list_experiments(include_archived=True)
    assert archived.total == 1
    assert archived.items[0].archived is True

    with pytest.raises(ValueError, match="보관된 실험"):
        await replay_experiments.add_experiment_run(
            created.experiment_id,
            ReplayExperimentRunCreate(label="도전", strategy=_strategy()),
        )
    with pytest.raises(KeyError):
        await replay_experiments.get_experiment("missing")
    with pytest.raises(KeyError):
        await replay_experiments.patch_experiment("missing", ReplayExperimentPatch(name="없음"))


@pytest.mark.asyncio
async def test_add_experiment_run_rejects_duplicates_period_mismatch_and_fourth_challenger(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, experiments = _use_repositories(monkeypatch, repositories)
    await _create_experiment_record(experiments)
    baseline = _strategy()
    digest = strategy_hash(baseline)
    await runs.create(
        "baseline",
        digest,
        {"strategy": baseline.model_dump(mode="json")},
        run_kind=RunKind.REAL_REPLAY_V2.value,
        data_version="data-v1",
    )
    await runs.set_succeeded("baseline", {"metrics": {}})
    await experiments.attach_run(
        "experiment-1",
        "baseline",
        role=ExperimentRunRole.BASELINE.value,
        label="기준",
    )
    monkeypatch.setattr(replay_experiments, "create_replay", _fake_create_replay(runs))

    with pytest.raises(ValueError, match="같은 설정"):
        await replay_experiments.add_experiment_run(
            "experiment-1",
            ReplayExperimentRunCreate(label="중복", strategy=baseline),
        )

    mismatched = baseline.model_copy(
        update={"data": baseline.data.model_copy(update={"end_date": date(2024, 12, 31)})}
    )
    with pytest.raises(ValueError, match="동일한 데이터 기간"):
        await replay_experiments.add_experiment_run(
            "experiment-1",
            ReplayExperimentRunCreate(label="기간 불일치", strategy=mismatched),
        )

    for entry_score in (70, 75, 80):
        strategy = baseline.model_copy(
            update={"signal": baseline.signal.model_copy(update={"entry_score": entry_score})}
        )
        accepted, cached = await replay_experiments.add_experiment_run(
            "experiment-1",
            ReplayExperimentRunCreate(label=f"도전 {entry_score}", strategy=strategy),
        )
        assert accepted.status.value == "QUEUED"
        assert cached is False

    fourth = baseline.model_copy(
        update={"signal": baseline.signal.model_copy(update={"entry_score": 85})}
    )
    with pytest.raises(ValueError, match="최대 3개"):
        await replay_experiments.add_experiment_run(
            "experiment-1",
            ReplayExperimentRunCreate(label="네 번째", strategy=fourth),
        )
    with pytest.raises(KeyError):
        await replay_experiments.add_experiment_run(
            "missing", ReplayExperimentRunCreate(label="없음", strategy=baseline)
        )


@pytest.mark.asyncio
async def test_create_sweep_cache_progress_and_rejections(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, experiments = _use_repositories(monkeypatch, repositories)
    await _create_experiment_record(experiments)
    request = ReplaySweepCreate(
        base_strategy=_strategy(),
        axes=[ReplaySweepAxis(path="signal.entry_score", values=[65, 70])],
    )

    accepted, cached = await replay_experiments.create_sweep("experiment-1", request)
    assert accepted.status.value == "QUEUED"
    assert cached is False
    await runs.set_progress(accepted.run_id, "SWEEP", 2, 3)
    progress = await replay_experiments.get_sweep(accepted.run_id)
    assert progress.progress_percent == 66.7
    assert progress.stage == "SWEEP"

    await runs.set_succeeded(accepted.run_id, {"trial_count": 2})
    cached_run, cached = await replay_experiments.create_sweep("experiment-1", request)
    assert cached is True
    assert cached_run.run_id == accepted.run_id
    assert cached_run.status.value == "SUCCEEDED"

    mismatched = _strategy().model_copy(
        update={"data": _strategy().data.model_copy(update={"end_date": date(2024, 12, 31)})}
    )
    with pytest.raises(ValueError, match="같은 기간"):
        await replay_experiments.create_sweep(
            "experiment-1",
            ReplaySweepCreate(
                base_strategy=mismatched,
                axes=[ReplaySweepAxis(path="signal.entry_score", values=[65, 70])],
            ),
        )
    await experiments.update("experiment-1", archived=True)
    with pytest.raises(ValueError, match="보관된 실험"):
        await replay_experiments.create_sweep("experiment-1", request)
    with pytest.raises(KeyError):
        await replay_experiments.create_sweep("missing", request)

    await runs.create("not-a-sweep", "hash", {}, run_kind="DEMO_BACKTEST")
    with pytest.raises(KeyError):
        await replay_experiments.get_sweep("not-a-sweep")
    with pytest.raises(KeyError):
        await replay_experiments.get_sweep("missing")


def _sweep_row(index: int, validation_rank_value: int) -> dict[str, Any]:
    return {
        "index": index,
        "strategy": {"signal": {"entry_score": 60 + index}},
        "full": {"cagr": 0.1},
        "training": {"cagr": index / 100, "max_drawdown": -(0.10 + index / 1000)},
        "validation": {
            "cagr": validation_rank_value / 100,
            "max_drawdown": -(0.20 - index / 2000),
        },
        "trade_count": 20 + index,
        "transaction_cost_krw": 1_000 + index,
        "winner_concentration": {
            "top_1_profit_ratio": 0.4,
            "top_3_profit_ratio": 0.6 if index == 0 else 0.4,
        },
    }


def test_sweep_post_processing_and_three_artifact_formats() -> None:
    rows = [_sweep_row(index, (index * 9) % 20) for index in range(20)]
    invalid_rows = [{"index": 20, "strategy": {}, "error": "후보 없음"}]
    axes = [{"path": "signal.entry_score", "values": [60, 79]}]

    result = _sweep_result(
        experiment_id="experiment-1",
        axes=axes,
        trial_count=21,
        rows=rows,
        invalid_rows=invalid_rows,
    )

    diagnostics = result["diagnostics"]
    assert result["valid_trial_count"] == 20
    assert diagnostics["invalid_trial_count"] == 1
    assert diagnostics["top_decile_size"] == 2
    assert diagnostics["pareto_boundary_axes"] == ["signal.entry_score"]
    assert diagnostics["maximum_top_3_profit_ratio"] == 0.6
    assert len(diagnostics["warnings"]) == 5

    payloads = _sweep_artifact_payloads(result)
    assert [item[0] for item in payloads] == ["sweep.json", "sweep.csv", "sweep.parquet"]
    assert orjson.loads(payloads[0][1]) == result
    assert payloads[1][1].startswith(b"\xef\xbb\xbfindex,training_cagr")
    parquet = pl.read_parquet(io.BytesIO(payloads[2][1]))
    assert parquet.shape == (20, 7)
    assert parquet.get_column("index").to_list() == list(range(20))


def test_sweep_variant_validation_and_weight_edge_cases() -> None:
    with pytest.raises(ValueError, match="지원하지 않는"):
        _variants(
            ReplaySweepCreate(
                base_strategy=_strategy(),
                axes=[ReplaySweepAxis(path="unknown.path", values=[1, 2])],
            )
        )
    with pytest.raises(ValueError, match="유효하지 않습니다"):
        _variants(
            ReplaySweepCreate(
                base_strategy=_strategy(),
                axes=[ReplaySweepAxis(path="signal.entry_score", values=[10, 20])],
            )
        )
    duplicate = _variants(
        ReplaySweepCreate(
            base_strategy=_strategy(),
            axes=[ReplaySweepAxis(path="signal.entry_score", values=[65, 65])],
        )
    )
    assert len(duplicate) == 1
    with pytest.raises(ValueError, match="0~10,000"):
        _normalize_weights({"a": 5_000, "b": 5_000}, {"a": -1})
    assert _normalize_weights({"a": 0, "b": 0}, {"a": 4_000}) == {
        "a": 4_000,
        "b": 6_000,
    }
    with pytest.raises(ValueError, match="10,000bp"):
        _normalize_weights({"a": 10_000}, {"a": 9_000})


def _replay_summary(
    *,
    cagr: float,
    max_drawdown: float,
    sharpe: float,
    validation_trades: int,
    full_trades: int = 100,
    cost: float = 1_000,
) -> dict[str, Any]:
    return {
        "metrics": {
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "sharpe": sharpe,
            "trade_count": full_trades,
            "final_value_krw": 60_000_000 + cagr * 1_000_000,
            "average_exposure": 0.8,
        },
        "validation": {
            "independent_validation": {
                "metrics": {
                    "cagr": cagr,
                    "max_drawdown": max_drawdown,
                    "sharpe": sharpe,
                    "trade_count": validation_trades,
                }
            }
        },
        "analysis": {"cost_summary": {"explicit_cost_krw": cost}},
        "strategy_config": {"signal": {"entry_score": 65}},
    }


@pytest.mark.asyncio
async def test_experiment_comparison_classifies_all_candidate_types_and_assesses_success(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, experiments = _use_repositories(monkeypatch, repositories)
    await _create_experiment_record(experiments)
    cases = [
        (
            "baseline",
            "BASELINE",
            _replay_summary(cagr=0.10, max_drawdown=-0.20, sharpe=1.0, validation_trades=100),
        ),
        (
            "small",
            "CHALLENGER",
            _replay_summary(cagr=0.20, max_drawdown=-0.10, sharpe=1.3, validation_trades=29),
        ),
        (
            "costly",
            "CHALLENGER",
            _replay_summary(
                cagr=0.20,
                max_drawdown=-0.10,
                sharpe=1.3,
                validation_trades=100,
                cost=1_300,
            ),
        ),
        (
            "return",
            "CHALLENGER",
            _replay_summary(cagr=0.12, max_drawdown=-0.19, sharpe=1.2, validation_trades=100),
        ),
        (
            "defense",
            "CHALLENGER",
            _replay_summary(cagr=0.09, max_drawdown=-0.15, sharpe=1.0, validation_trades=100),
        ),
        (
            "unstable",
            "CHALLENGER",
            _replay_summary(cagr=0.08, max_drawdown=-0.25, sharpe=0.8, validation_trades=100),
        ),
    ]
    for run_id, role, summary in cases:
        await runs.create(
            run_id,
            f"hash-{run_id}",
            {},
            run_kind=RunKind.REAL_REPLAY_V2.value,
            data_version="data-v1",
        )
        await runs.set_succeeded(run_id, summary)
        await experiments.attach_run("experiment-1", run_id, role=role, label=run_id)

    comparison = await replay_experiments.experiment_comparison("experiment-1")

    assert comparison.baseline_run_id == "baseline"
    assert {item["run_id"]: item["classification"] for item in comparison.runs} == {
        "baseline": "기준",
        "small": "표본 부족",
        "costly": "비용 과다",
        "return": "수익형",
        "defense": "방어형",
        "unstable": "불안정",
    }
    assessments = {item["run_id"]: item for item in comparison.success_assessments}
    assert assessments["return"]["passed"] is True
    assert assessments["unstable"]["passed"] is False
    assert all(item["explanation"] for item in comparison.runs)
    with pytest.raises(KeyError):
        await replay_experiments.experiment_comparison("missing")


class _FakeSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lease_events: list[tuple[str, str, str]] = []

    def acquire_lease(self, data_version: str, run_id: str) -> None:
        self.lease_events.append(("acquire", data_version, run_id))

    def release_lease(self, data_version: str, run_id: str) -> None:
        self.lease_events.append(("release", data_version, run_id))


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def put(self, object_key: str, content: bytes, content_type: str) -> int:
        self.objects[object_key] = (content, content_type)
        return len(content)


class _FakeReplayEngine:
    def __init__(self, score_root: Path) -> None:
        self.score_root = score_root

    def signal_context(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(score_root=self.score_root)

    def project_context(self, *_: Any, **__: Any) -> tuple[pl.DataFrame, pl.DataFrame]:
        signals = pl.DataFrame({"review_date": [date(2017, 1, 2)]})
        metadata = pl.DataFrame({"asset_id": ["US_STOCK:TEST"]})
        return signals, metadata

    def prepare_market(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(dates=[date(2017, 1, 2), date(2021, 1, 4), date(2025, 1, 3)])


class _FailingReplayEngine(_FakeReplayEngine):
    def signal_context(self, **_: Any) -> SimpleNamespace:
        raise RuntimeError("engine failed")


class _CancellingRepository:
    def __init__(self, delegate: RunRepository) -> None:
        self.delegate = delegate
        self.cancel_checks = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    async def cancellation_requested(self, _: str) -> bool:
        self.cancel_checks += 1
        return self.cancel_checks >= 2


async def _create_sweep_run(runs: RunRepository, run_id: str) -> ReplaySweepCreate:
    request = ReplaySweepCreate(
        base_strategy=_strategy(),
        axes=[ReplaySweepAxis(path="signal.entry_score", values=[65, 70])],
    )
    await runs.create(
        run_id,
        f"hash-{run_id}",
        {
            "version": replay_experiments.SWEEP_VERSION,
            "experiment_id": "experiment-1",
            **request.model_dump(mode="json"),
        },
        run_kind=RunKind.REPLAY_SWEEP.value,
        data_version="data-v1",
    )
    return request


def _configure_sweep_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    runs: Any,
    snapshot: _FakeSnapshotStore,
    artifacts: _FakeArtifactStore,
    engine: _FakeReplayEngine,
) -> None:
    monkeypatch.setattr(replay_experiments, "run_repository", runs)
    monkeypatch.setattr(replay_experiments, "snapshot_store", snapshot)
    monkeypatch.setattr(replay_experiments, "artifact_store", artifacts)
    monkeypatch.setattr(replay_experiments, "replay_engine", engine)
    monkeypatch.setattr(replay_experiments, "_prepared_with_signals", lambda base, _: base)

    def simulate(*_: Any, run_id: str, **__: Any) -> SimpleNamespace:
        index = int(run_id.rsplit("-", 1)[1])
        return SimpleNamespace(
            equity_values=[float(index), 1.0, 2.0],
            result=SimpleNamespace(metrics={"cagr": 0.10 + index / 100}, trades=[1] * 40),
            daily_ledger=[SimpleNamespace(transaction_cost_krw=100 + index)],
            round_trips=[
                SimpleNamespace(net_pnl_krw=300),
                SimpleNamespace(net_pnl_krw=200),
                SimpleNamespace(net_pnl_krw=-50),
            ],
        )

    def metrics(
        equity_values: list[float],
        _: list[date],
        *,
        start_index: int,
        **__: Any,
    ) -> dict[str, float]:
        index = int(equity_values[0])
        if start_index == 0:
            return {"cagr": 0.10 + index / 100, "max_drawdown": -0.20}
        return {"cagr": 0.09 + index / 100, "max_drawdown": -0.18}

    monkeypatch.setattr(replay_experiments, "simulate_prepared_replay", simulate)
    monkeypatch.setattr(replay_experiments, "period_metrics", metrics)


@pytest.mark.asyncio
async def test_execute_sweep_success_persists_artifacts_and_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = repositories
    await _create_sweep_run(runs, "sweep-success")
    snapshot = _FakeSnapshotStore(tmp_path / "research")
    artifacts = _FakeArtifactStore()
    _configure_sweep_execution(
        monkeypatch,
        runs=runs,
        snapshot=snapshot,
        artifacts=artifacts,
        engine=_FakeReplayEngine(tmp_path / "scores"),
    )

    await replay_experiments.execute_sweep("sweep-success")

    model = await runs.get("sweep-success")
    assert model is not None
    assert model.status == "SUCCEEDED"
    assert model.result_summary is not None
    assert model.result_summary["trial_count"] == 2
    assert model.completed_units == 2
    assert {item.name for item in await runs.artifacts("sweep-success")} == {
        "sweep.json",
        "sweep.csv",
        "sweep.parquet",
    }
    assert len(artifacts.objects) == 3
    json_content = artifacts.objects["research-runs/sweep-success/sweep.json"][0]
    assert orjson.loads(json_content)["valid_trial_count"] == 2
    assert snapshot.lease_events == [
        ("acquire", "data-v1", "sweep-success"),
        ("release", "data-v1", "sweep-success"),
    ]
    assert not (snapshot.root / "sweep-work" / "sweep-success").exists()


@pytest.mark.asyncio
async def test_execute_sweep_cancellation_before_start_does_not_acquire_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = repositories
    await _create_sweep_run(runs, "sweep-cancelled-before")
    await runs.request_cancel("sweep-cancelled-before")
    snapshot = _FakeSnapshotStore(tmp_path / "research")
    monkeypatch.setattr(replay_experiments, "run_repository", runs)
    monkeypatch.setattr(replay_experiments, "snapshot_store", snapshot)

    await replay_experiments.execute_sweep("sweep-cancelled-before")

    model = await runs.get("sweep-cancelled-before")
    assert model is not None
    assert model.status == "CANCELLED"
    assert snapshot.lease_events == []


@pytest.mark.asyncio
async def test_execute_sweep_runtime_cancellation_cleans_work_and_releases_lease(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = repositories
    await _create_sweep_run(runs, "sweep-cancelled-running")
    cancelling = _CancellingRepository(runs)
    snapshot = _FakeSnapshotStore(tmp_path / "research")
    artifacts = _FakeArtifactStore()
    _configure_sweep_execution(
        monkeypatch,
        runs=cancelling,
        snapshot=snapshot,
        artifacts=artifacts,
        engine=_FakeReplayEngine(tmp_path / "scores"),
    )

    await replay_experiments.execute_sweep("sweep-cancelled-running")

    model = await runs.get("sweep-cancelled-running")
    assert model is not None
    assert model.status == "CANCELLED"
    assert artifacts.objects == {}
    assert snapshot.lease_events[-1] == (
        "release",
        "data-v1",
        "sweep-cancelled-running",
    )
    assert not (snapshot.root / "sweep-work" / "sweep-cancelled-running").exists()


@pytest.mark.asyncio
async def test_execute_sweep_engine_failure_marks_failed_and_cleans_resources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = repositories
    await _create_sweep_run(runs, "sweep-failed")
    snapshot = _FakeSnapshotStore(tmp_path / "research")
    artifacts = _FakeArtifactStore()
    _configure_sweep_execution(
        monkeypatch,
        runs=runs,
        snapshot=snapshot,
        artifacts=artifacts,
        engine=_FailingReplayEngine(tmp_path / "scores"),
    )

    with pytest.raises(RuntimeError, match="engine failed"):
        await replay_experiments.execute_sweep("sweep-failed")

    model = await runs.get("sweep-failed")
    assert model is not None
    assert model.status == "FAILED"
    assert model.error_message == "engine failed"
    assert snapshot.lease_events[-1] == ("release", "data-v1", "sweep-failed")
    assert not (snapshot.root / "sweep-work" / "sweep-failed").exists()


@pytest.mark.asyncio
async def test_execute_sweep_rejects_missing_or_versionless_run(
    monkeypatch: pytest.MonkeyPatch,
    repositories: tuple[RunRepository, ReplayExperimentRepository],
) -> None:
    runs, _ = repositories
    monkeypatch.setattr(replay_experiments, "run_repository", runs)
    with pytest.raises(KeyError):
        await replay_experiments.execute_sweep("missing")
    await runs.create(
        "versionless",
        "hash",
        {},
        run_kind=RunKind.REPLAY_SWEEP.value,
    )
    with pytest.raises(KeyError):
        await replay_experiments.execute_sweep("versionless")
