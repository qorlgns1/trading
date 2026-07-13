from datetime import date
from pathlib import Path

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st
from quant_api.database import Base, ReplayExperimentRepository, RunRepository
from quant_api.replay_experiments import _normalize_weights, _pareto_rows, _variants
from quant_api.replay_strategy import strategy_domain, strategy_hash
from quant_api.research_replay import REPLAY_FEATURE_COLUMNS, _scheduled_signals
from quant_api.schemas import (
    ReplayDataConfig,
    ReplayStrategyConfig,
    ReplaySweepAxis,
    ReplaySweepCreate,
)
from quant_core.config import PortfolioConfig, TrendScoreConfig
from quant_core.enums import PeerGroup, Sleeve
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _strategy() -> ReplayStrategyConfig:
    return ReplayStrategyConfig(
        data=ReplayDataConfig(
            start_date=date(2017, 1, 2),
            split_date=date(2021, 1, 4),
            end_date=date(2025, 1, 3),
        )
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
