import pytest
from hypothesis import given
from hypothesis import strategies as st
from quant_core.config import PortfolioConfig
from quant_core.enums import Sleeve


def test_default_portfolio_weights_sum_to_100_percent() -> None:
    config = PortfolioConfig()
    assert sum(config.sleeve_weights_bps.values()) == 10_000


@given(st.integers(min_value=0, max_value=9_999))
def test_invalid_weight_total_is_rejected(first_weight: int) -> None:
    weights = {
        Sleeve.US_STOCK: first_weight,
        Sleeve.KR_STOCK: 0,
        Sleeve.US_ETF: 0,
        Sleeve.KR_ETF: 0,
    }
    with pytest.raises(ValueError, match="10,000bp"):
        PortfolioConfig(sleeve_weights_bps=weights)
