from datetime import date

import pytest
from quant_api.replay_strategy import (
    default_period,
    default_success_criteria,
    strategy_domain,
    strategy_hash,
    success_assessment,
)
from quant_api.schemas import (
    ReplayDataConfig,
    ReplayPeerSlots,
    ReplayPeerThreshold,
    ReplayPortfolioConfig,
    ReplaySignalConfig,
    ReplayStrategyConfig,
)
from quant_core.enums import ExperimentObjective, PeerGroup, Sleeve


def _data(*, peer_groups: list[PeerGroup] | None = None) -> ReplayDataConfig:
    return ReplayDataConfig(
        peer_groups=peer_groups or list(PeerGroup),
        start_date=date(2017, 1, 2),
        split_date=date(2021, 1, 4),
        end_date=date(2025, 1, 3),
    )


def test_default_period_reserves_warmup_and_excludes_requested_end() -> None:
    period = default_period(
        {
            "history_start": "2015-01-01",
            "requested_end": "2025-01-01",
        }
    )

    assert period.start_date == date(2016, 1, 16)
    assert period.split_date == date(2021, 6, 1)
    assert period.end_date == date(2024, 12, 31)


def test_strategy_hash_is_stable_and_sensitive_to_strategy_data() -> None:
    baseline = ReplayStrategyConfig(data=_data())
    changed = baseline.model_copy(
        update={"data": baseline.data.model_copy(update={"end_date": date(2025, 1, 4)})}
    )

    assert strategy_hash(baseline) == strategy_hash(ReplayStrategyConfig(data=_data()))
    assert strategy_hash(changed) != strategy_hash(baseline)


@pytest.mark.parametrize(
    ("objective", "expected"),
    [
        (
            ExperimentObjective.RETURN,
            {
                "minimum_cagr_improvement_pp": 0.5,
                "maximum_mdd_degradation_pp": 2.0,
            },
        ),
        (
            ExperimentObjective.DRAWDOWN,
            {
                "minimum_mdd_improvement_pp": 2.0,
                "maximum_cagr_degradation_pp": 1.0,
            },
        ),
        (
            ExperimentObjective.COST,
            {
                "minimum_cost_reduction_ratio": 0.10,
                "maximum_cagr_degradation_pp": 1.0,
            },
        ),
        (
            ExperimentObjective.BALANCED,
            {
                "minimum_sharpe_improvement": 0.10,
                "maximum_cagr_degradation_pp": 0.5,
                "maximum_mdd_degradation_pp": 1.0,
            },
        ),
    ],
)
def test_default_success_criteria_match_objective(
    objective: ExperimentObjective,
    expected: dict[str, float],
) -> None:
    criteria = default_success_criteria(objective)

    assert criteria.model_dump(exclude_defaults=True) == expected


@pytest.mark.parametrize(
    ("objective", "candidate", "candidate_cost", "expected_labels"),
    [
        (
            ExperimentObjective.RETURN,
            {"cagr": 0.106, "max_drawdown": -0.215, "sharpe": 1.0},
            100.0,
            ["검증 CAGR 개선", "낙폭 악화 제한"],
        ),
        (
            ExperimentObjective.DRAWDOWN,
            {"cagr": 0.095, "max_drawdown": -0.175, "sharpe": 1.0},
            100.0,
            ["최대 낙폭 개선", "CAGR 감소 제한"],
        ),
        (
            ExperimentObjective.COST,
            {"cagr": 0.095, "max_drawdown": -0.20, "sharpe": 1.0},
            85.0,
            ["비용 감소", "CAGR 감소 제한"],
        ),
        (
            ExperimentObjective.BALANCED,
            {"cagr": 0.096, "max_drawdown": -0.208, "sharpe": 1.15},
            100.0,
            ["Sharpe 개선", "CAGR 감소 제한", "낙폭 악화 제한"],
        ),
    ],
)
def test_success_assessment_passes_objective_specific_thresholds(
    objective: ExperimentObjective,
    candidate: dict[str, float],
    candidate_cost: float,
    expected_labels: list[str],
) -> None:
    assessment = success_assessment(
        objective=objective,
        criteria=default_success_criteria(objective),
        baseline={"cagr": 0.10, "max_drawdown": -0.20, "sharpe": 1.0},
        candidate=candidate,
        baseline_cost=100.0,
        candidate_cost=candidate_cost,
    )

    assert assessment["passed"] is True
    assert [check["label"] for check in assessment["checks"]] == expected_labels
    assert all(check["passed"] for check in assessment["checks"])


@pytest.mark.parametrize("objective", list(ExperimentObjective))
def test_success_assessment_fails_when_required_improvement_is_absent(
    objective: ExperimentObjective,
) -> None:
    baseline = {"cagr": 0.10, "max_drawdown": -0.20, "sharpe": 1.0}

    assessment = success_assessment(
        objective=objective,
        criteria=default_success_criteria(objective),
        baseline=baseline,
        candidate=baseline,
        baseline_cost=100.0,
        candidate_cost=100.0,
    )

    assert assessment["passed"] is False
    assert assessment["cagr_change_pp"] == 0.0
    assert assessment["mdd_improvement_pp"] == 0.0
    assert assessment["sharpe_change"] == 0.0


def test_cost_assessment_handles_zero_baseline_cost_without_division() -> None:
    assessment = success_assessment(
        objective=ExperimentObjective.COST,
        criteria=default_success_criteria(ExperimentObjective.COST),
        baseline={},
        candidate={},
        baseline_cost=0.0,
        candidate_cost=0.0,
    )

    assert assessment["passed"] is False
    assert assessment["cost_reduction_ratio"] == 0.0
    assert assessment["checks"][0] == {"label": "비용 감소", "passed": False}


def test_strategy_domain_maps_overrides_and_disables_unselected_peer_groups() -> None:
    enabled = [
        PeerGroup.US_STOCK,
        PeerGroup.US_EQUITY_ETF,
        PeerGroup.KR_KOSPI,
        PeerGroup.KR_DOMESTIC_EQUITY_ETF,
    ]
    strategy = ReplayStrategyConfig(
        data=_data(peer_groups=enabled),
        signal=ReplaySignalConfig(
            peer_overrides={PeerGroup.KR_KOSPI: ReplayPeerThreshold(entry_score=75, exit_score=65)},
            minimum_adv_multiplier=1.5,
        ),
        portfolio=ReplayPortfolioConfig(
            peer_group_slots=ReplayPeerSlots(
                kr_kospi=4,
                kr_kosdaq=5,
                kr_domestic_equity_etf=3,
                kr_overseas_equity_etf=4,
            )
        ),
    )

    score, portfolio = strategy_domain(strategy)

    assert score.minimum_adv_multiplier == 1.5
    assert score.component_weights_bps == strategy.signal.component_weights_bps.model_dump()
    assert portfolio.sleeve_weights_bps == {
        Sleeve.US_STOCK: 2500,
        Sleeve.KR_STOCK: 2500,
        Sleeve.US_ETF: 2500,
        Sleeve.KR_ETF: 2500,
    }
    assert portfolio.peer_entry_scores == {PeerGroup.KR_KOSPI: 75.0}
    assert portfolio.peer_exit_scores == {PeerGroup.KR_KOSPI: 65.0}
    assert portfolio.peer_group_slots[PeerGroup.KR_KOSPI] == 4
    assert portfolio.peer_group_slots[PeerGroup.KR_DOMESTIC_EQUITY_ETF] == 3
    assert portfolio.peer_group_slots[PeerGroup.KR_KOSDAQ] == 0
    assert portfolio.peer_group_slots[PeerGroup.KR_OVERSEAS_EQUITY_ETF] == 0
