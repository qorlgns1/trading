# Replay Strategy Lab v2

## Scope

- Strategy contract: `replay-strategy-v2.0.0`
- Feature projection: `trend-score-v2.0.0`
- Portfolio rules: `portfolio-v2.0.0`
- Replay engine: `real-replay-v2.0.0`
- Market events: `market-event-v2.0.0`
- Analysis: `replay-analysis-v2.0.0`
- Feature cache: `replay-feature-cache-v2.0.0`
- Mode: `local_research` only

The strategy lab compares one user-selected baseline with up to three challengers. It does not accept user code, new indicators, or alternative lookback periods. Every run hash includes the immutable strategy document, data version, engine versions, quality policy, dates, costs, and execution assumptions.

## Strategy Contract

`ReplayStrategyConfig` contains five controlled sections:

| Section | Adjustable settings |
| --- | --- |
| Data | Peer groups, start/split/end dates, current-listed or capability-gated point-in-time universe |
| Signal | 50-100 entry and exit thresholds, peer overrides, six weights totaling 10,000bp, required gates, market SMA200 gate |
| Portfolio | Initial capital, four sleeve weights totaling 10,000bp, six peer slots, equal or inverse-volatility entry sizing, replacement policy |
| Risk and execution | Optional fixed/trailing close stops, daily/weekly/monthly review, 1-5 session delay, side-specific costs, FX cost, slippage |
| Validation | Walk-forward training, test, and step lengths |

The engine first creates fixed Trend Features and writes them by peer group and year. Strategy-specific weights and gates are projected from those features, so a sweep does not recalculate moving averages and ranks for every combination. A 253-session preparation window is read before the requested performance start.

The default v2 strategy is the legacy 65 entry / 60 exit, equal 25% sleeve allocation, 12-slot, weekly, next-open strategy. It remains a default only; the user chooses the experiment baseline.

## Validation

An experiment freezes its hypothesis, objective, numerical success criteria, data version, universe mode, and period before the first run. Training and validation each require at least 252 evaluation dates.

Each completed strategy reports:

- full-period replay
- continuous validation with the training portfolio carried across the split
- independent validation restarted from initial cash at the split
- configurable rolling walk-forward windows
- exact 2x transaction and FX cost replay
- one-session-later execution replay
- top-one and top-three winning-trade subtraction concentration stress
- future-signal, fill ordering, slot, cash, and value-reconciliation invariants

The comparison API reports final KRW value, validation CAGR/MDD/Sharpe, full-period costs, trades and exposure, baseline differences, objective success, and deterministic explanation text.

## Sensitivity Sweeps

A sweep accepts one or two controlled axes and at most 100 unique combinations. Score and sleeve weight axes use largest-remainder redistribution so each generated strategy totals exactly 10,000bp. Results include all combinations, the training CAGR / absolute MDD Pareto set, validation heatmap values, training-validation rank correlation, top-decile overlap, trade-count range, winning-trade concentration, boundary selection, and overfitting warnings. The product deliberately does not label one combination as the optimum.

## Forward Promotion

A successful v2 run can be promoted without changing its strategy document. Active slots are limited to one `BASELINE` account and three `EXPERIMENT` accounts. Accounts keep independent lifetime performance and also receive a common-start comparison period. Archiving is required before replacing a slot. Existing v1 accounts are interpreted with the legacy default configuration and keep all prior ledger rows.

## Storage and API

Experiments and run links use `replay_experiments` and `replay_experiment_runs`; replay and sweep jobs continue to use `backtest_runs`. Full market data stays in local Parquet. Completed replays add strategy, validation, robustness, analysis, ledger, trade, and equity artifacts. Celery processes replay and sweep work serially with worker concurrency one.

The v2 endpoints are rooted at `/api/v1/research/replay-options`, `/api/v1/research/experiments`, and `/api/v1/research/sweeps`. Existing `/api/v1/research/replays` requests remain compatible: an old weight-only request runs the v1 contract, while a strategy document runs v2. All experiment, sweep, cancellation, promotion, and multi-account endpoints return `403` in `public_demo`.

## Local Regression Baseline

The local snapshot `yf-20260710T200150Z-81ca7894e96b` reproduces the following final values from KRW 50,000,000:

| Strategy | Final value |
| --- | ---: |
| 65/60, 25/25/25/25 | KRW 116,303,133 |
| 80/60, 30/20/30/20 | KRW 119,546,620 |
| 90/80, 30/20/30/20 | KRW 110,744,219 |

On the same 18,755,980-row snapshot, a cached 100-combination sweep completed in 555.43 seconds with a maximum resident set size of 4.68 GiB. This is a local regression and capacity baseline, not an investment performance claim.

## Current Limitation

The active free-data snapshot contains currently listed securities, so historical experiments have survivorship bias. `POINT_IN_TIME` exists in the contract and test fixtures but is rejected until a snapshot manifest declares and supplies validated membership intervals. Strategy experiments are research diagnostics, not investment recommendations or official performance claims.
