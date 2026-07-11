"""Framework-independent quant research engine."""

from quant_core.backtest import run_reference_backtest
from quant_core.config import PortfolioConfig, TrendScoreConfig
from quant_core.market_portfolio import (
    MARKET_EVENT_VERSION,
    MarketReplayRun,
    PortfolioPosition,
    PreparedMarketReplay,
    plan_weekly_orders,
    prepare_market_replay,
    run_market_replay,
    simulate_prepared_replay,
)
from quant_core.replay_analysis import (
    REPLAY_ANALYSIS_VERSION,
    ReplayAnalysisBuild,
    ReplayInvariantError,
    analyze_replay,
)
from quant_core.scoring import explain_result, score_trends
from quant_core.synthetic import DEMO_DATA_VERSION, generate_demo_market

__all__ = [
    "DEMO_DATA_VERSION",
    "MARKET_EVENT_VERSION",
    "REPLAY_ANALYSIS_VERSION",
    "MarketReplayRun",
    "PortfolioConfig",
    "PortfolioPosition",
    "PreparedMarketReplay",
    "ReplayAnalysisBuild",
    "ReplayInvariantError",
    "TrendScoreConfig",
    "analyze_replay",
    "explain_result",
    "generate_demo_market",
    "plan_weekly_orders",
    "prepare_market_replay",
    "run_market_replay",
    "run_reference_backtest",
    "score_trends",
    "simulate_prepared_replay",
]
