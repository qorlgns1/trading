"""Framework-independent quant research engine."""

from quant_core.backtest import run_reference_backtest
from quant_core.config import PortfolioConfig, TrendScoreConfig
from quant_core.scoring import explain_result, score_trends
from quant_core.synthetic import DEMO_DATA_VERSION, generate_demo_market

__all__ = [
    "DEMO_DATA_VERSION",
    "PortfolioConfig",
    "TrendScoreConfig",
    "explain_result",
    "generate_demo_market",
    "run_reference_backtest",
    "score_trends",
]
