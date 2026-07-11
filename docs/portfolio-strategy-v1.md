# Portfolio Strategy v1

## Status

- Version: `portfolio-v1.0.0`
- Mode: long-only paper simulation
- Base currency: KRW
- Starting capital: KRW 50,000,000
- Review frequency: weekly, after the final Korean and US session closes are both confirmed

## Allocation and Capacity

| Sleeve | Default weight | Slots |
| --- | ---: | ---: |
| US stocks | 25% | 3 |
| Korean stocks | 25% | KOSPI 2, KOSDAQ 1 |
| US equity ETFs | 25% | 3 |
| Korean equity ETFs | 25% | domestic 2, overseas 1 |

Public users may change only the four sleeve weights. Inputs use integer basis points, must be non-negative, and must sum to 10,000. Position quantities are always whole shares.

## Entry and Exit

1. A new position requires the Trend Score gate and a score of at least 65 at a weekly review.
2. The peer-group benchmark must be above its 200-day moving average for a new entry.
3. Candidates are ordered by score, relative momentum, and asset ID for deterministic ties.
4. A higher-ranked candidate does not replace a valid current holding. Only an empty slot can be filled.
5. A holding exits when the gate fails or its weekly score falls below 60.
6. Every order executes at the next available session open for its own market. Missing sell opens remain pending; missing buys are canceled at the next weekly review.
7. A held asset with failed, stale, or quarantined data is marked `REVIEW_REQUIRED` and is not sold automatically.
8. A validated delisting recovery closes the position at its recorded recovery value.

## Cash, FX, and Costs

- US sleeve allocations convert to USD once at the first session and keep residual cash in USD.
- Daily portfolio value converts USD cash and positions to KRW at the synthetic daily FX rate.
- Initial FX cost: 0.25%.
- US buy and sell composite cost: 0.15% each side.
- Korean buy and sell composite cost: 0.25% each side.
- Composite costs stand in for commissions, taxes, spread, and slippage; no additional cost is added.

## Reported Metrics

CAGR, annual volatility, Sharpe, Sortino, maximum drawdown, Calmar, turnover, trade count, average exposure, total return, and a sleeve-weighted synthetic benchmark are reported. Real-data replay returns and drawdowns start from the full KRW 50,000,000 before the initial FX cost. Sharpe and Sortino use a 0% risk-free or minimum acceptable return in v1.
