from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from quant_api import research_replays
from quant_api.database import create_schema
from quant_api.research_replay import ReplayBuild
from quant_api.schemas import ReplayResponse
from quant_core.config import TREND_SCORE_VERSION, PortfolioConfig
from quant_core.enums import PeerGroup
from quant_core.market_portfolio import prepare_market_replay, simulate_prepared_replay
from quant_core.replay_analysis import REPLAY_ANALYSIS_VERSION, analyze_replay


def _replay_build(tmp_path: Path, run_id: str) -> ReplayBuild:
    review_date = date(2025, 7, 4)
    dates = [review_date, date(2025, 7, 7), date(2025, 7, 8)]
    signals: list[dict[str, object]] = []
    bars: list[dict[str, object]] = []
    reference: list[dict[str, object]] = []
    for group in PeerGroup:
        currency = "USD" if group.value.startswith("US_") else "KRW"
        asset_id = f"{group.value}:ASSET"
        signals.append(
            {
                "signal_date": date(2025, 7, 3) if currency == "USD" else review_date,
                "review_date": review_date,
                "asset_id": asset_id,
                "symbol": group.value,
                "name": group.value,
                "peer_group": group.value,
                "currency": currency,
                "trend_score": 75.0,
                "relative_momentum": 0.2,
                "data_eligible": True,
                "candidate_eligible": True,
                "benchmark_close": 110.0,
                "benchmark_sma200": 100.0,
            }
        )
        for index, current in enumerate(dates):
            bars.append(
                {
                    "date": current,
                    "asset_id": asset_id,
                    "symbol": group.value,
                    "name": group.value,
                    "peer_group": group.value,
                    "currency": currency,
                    "open": 100.0 + index,
                    "close": 101.0 + index,
                    "split_ratio": 1.0,
                    "dividend": 0.0,
                    "recovery_value": None,
                }
            )
            reference.append(
                {
                    "date": current,
                    "peer_group": group.value,
                    "benchmark_close": 100.0 + index,
                    "fx_krw_per_usd": 1_350.0,
                }
            )
    config = PortfolioConfig()
    prepared = prepare_market_replay(
        pl.DataFrame(bars),
        pl.DataFrame(signals),
        pl.DataFrame(reference),
        portfolio_config=config,
    )
    actual = simulate_prepared_replay(
        prepared,
        data_version="fixture-v1",
        score_version=TREND_SCORE_VERSION,
        portfolio_config=config,
        run_id=run_id,
        prices_are_split_adjusted=True,
    )
    no_cost = simulate_prepared_replay(
        prepared,
        data_version="fixture-v1",
        score_version=TREND_SCORE_VERSION,
        portfolio_config=replace(
            config,
            us_trade_cost=0.0,
            kr_trade_cost=0.0,
            initial_fx_cost=0.0,
        ),
        run_id=f"{run_id}-no-cost",
        prices_are_split_adjusted=True,
    )
    return ReplayBuild(
        result=actual.result,
        actual_run=actual,
        no_cost_run=no_cost,
        prepared=prepared,
        analysis=analyze_replay(prepared, actual, no_cost, portfolio_config=config),
        cache_key="fixture-cache",
        cache_hit=True,
        score_root=tmp_path,
    )


@pytest.mark.asyncio
async def test_replay_execution_persists_typed_analysis_and_all_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await create_schema()
    run_id = "analysis-artifact-run"
    await research_replays.repository.create(
        run_id,
        "analysis-config-hash",
        {"sleeve_weights_bps": {}},
        run_kind="REAL_REPLAY",
        data_version="fixture-v1",
    )
    build = _replay_build(tmp_path, run_id)
    monkeypatch.setattr(research_replays.replay_engine, "run", lambda *args, **kwargs: build)
    monkeypatch.setattr(
        research_replays.snapshot_store,
        "acquire_lease",
        lambda *args, **kwargs: tmp_path,
    )
    monkeypatch.setattr(
        research_replays.snapshot_store,
        "release_lease",
        lambda *args, **kwargs: None,
    )

    await research_replays.execute_replay(run_id)

    model = await research_replays.repository.get(run_id)
    assert model is not None
    response = research_replays.response_from_model(model)
    validated = ReplayResponse.model_validate(response)
    assert validated.result is not None
    assert validated.result.analysis is not None
    assert validated.result.analysis.version == REPLAY_ANALYSIS_VERSION
    artifacts = await research_replays.repository.artifacts(run_id)
    assert {artifact.name for artifact in artifacts} == {
        "analysis.json",
        "daily-ledger.parquet",
        "equity.parquet",
        "report.html",
        "result.json",
        "review-ledger.parquet",
        "round-trips.csv",
        "trades.csv",
    }


def test_legacy_replay_result_keeps_analysis_optional() -> None:
    result = {
        "data_version": "legacy-v1",
        "score_version": "score-v1",
        "portfolio_version": "portfolio-v1",
        "started_on": "2025-01-01",
        "ended_on": "2025-12-31",
        "metrics": {"total_return": 0.1},
        "equity_curve": [
            {"date": "2025-01-01", "portfolio": 50_000_000, "benchmark": 50_000_000}
        ],
        "drawdown_curve": [{"date": "2025-01-01", "drawdown": 0}],
        "final_positions": [],
    }

    response = ReplayResponse(
        run_id="legacy",
        status="SUCCEEDED",
        stage="SUCCEEDED",
        completed_units=1,
        total_units=1,
        progress_percent=100,
        data_version="legacy-v1",
        created_at="2025-01-01T00:00:00Z",
        updated_at="2025-01-01T00:00:00Z",
        config={},
        result=result,
    )

    assert response.result is not None
    assert response.result.analysis is None
