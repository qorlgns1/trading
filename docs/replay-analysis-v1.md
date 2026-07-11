# Replay Analysis v1

## Contract

- Analysis version: `replay-analysis-v1.0.0`
- Replay engine: `real-replay-v1.1.0`
- Market event engine: `market-event-v1.1.0`
- Scope: local real-data historical replay only

The analysis is deterministic and does not use an LLM. Legacy replay summaries remain readable with `analysis=null`; the new versions participate in the replay hash so an old successful run is not reused as an analyzed result.

## Performance Bridge

Actual strategy performance starts from KRW 50,000,000 before initial FX cost. The gap bridge uses total-return percentage points:

1. full-investment sleeve-weighted benchmark
2. prior-close sleeve exposure and 200-day entry-gate effect
3. zero-cost strategy selection and execution effect
4. actual transaction and initial FX cost effect

The four terms must reconcile to actual strategy return within one basis point. Sleeve ending value minus initial allocation must reconcile to total portfolio profit within KRW 1.

## Ledgers And Artifacts

- `daily-ledger.parquet`: sleeve cash, positions, exposure, costs, dividends, and FX
- `review-ledger.parquet`: signal/review dates, entry regime, candidates, holdings, and planned orders
- `round-trips.csv`: completed and open trade episodes with KRW P&L after FX, dividends, and costs
- `equity.parquet`: actual, full benchmark, exposure-matched benchmark, and zero-cost curves
- `analysis.json`: period, sleeve, trade, regime, cost, and integrity summaries

The HTML report and `ReplayResponse.result.analysis` carry the compact explanation. Full raw prices remain in the immutable local research snapshot and are not copied into result artifacts.

## Hard Invariants

A replay is marked `FAILED` before artifacts are promoted when any of these checks fail: signal/decision/trade time order, next-session execution order, 12-position limit, peer-group slots, non-negative cash, daily equity reconciliation, sleeve P&L reconciliation, gap reconciliation, or finite positive performance curves.
