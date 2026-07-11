# Historical Replay and Forward Ledger v1

## Scope

- Replay engine: `real-replay-v1.1.0`
- Market event engine: `market-event-v1.1.0`
- Replay analysis: `replay-analysis-v1.0.0`
- Shared rules: `trend-score-v1.0.0`, `portfolio-v1.0.0`, `data-quality-v1.0.0`
- Mode: `local_research` only; public requests receive `403`

Historical replay and forward tracking are separate data products. A replay reconstructs the strategy over the immutable current-listed universe. A forward account records only observations and decisions made after the user creates it. Their results are never joined.

## Historical Replay

The scorer reads peer-group/year Parquet partitions with a 253-session carry window. Performance begins at the first completed common weekly review where all six peer groups have at least 30 scoreable assets and a 200-session benchmark. A snapshot lease prevents retention cleanup while a run is active.

Replay rules:

- current `INVALID_DATA` and unsupported assets are excluded for the full period
- provider-repaired assets remain included and are reported in warnings
- Korean and US weekly signal dates follow their own exchange calendars
- orders execute at each market's first valid open after the common review
- whole shares, dividends, delisting recovery, FX, and transaction costs affect cash
- missing sell opens carry forward; missing buys are canceled at the next review
- Yahoo OHLC uses split-normalized units, so replay does not apply split quantity twice

The actual-cost and zero-cost simulations reuse one prepared price matrix. Performance starts from the full KRW 50,000,000 before the initial FX cost. The result warning always states that the universe uses currently listed securities and contains survivorship bias.

The analysis decomposes the total-return gap into full-investment benchmark return, prior-close exposure and entry-gate effect, selection and execution effect before costs, and transaction/FX cost effect. It also reports annual and monthly periods, sleeve contribution, completed trade episodes, market-entry regimes, and hard replay invariants. HTML, JSON, CSV, and daily/review/equity Parquet artifacts remain local.

## Candidate History

Every activated snapshot writes official candidate source rows to `forward/signals/<data-version>/candidates.parquet`. SQL stores compact `BASELINE`, `ENTERED`, `RETAINED`, and `EXITED` events for filtering and pagination. When an account exists, candidate and held-asset score rows are also written to `forward/accounts/<account-id>/scores/<data-version>.parquet`. Reprocessing the same data version returns the existing snapshot and does not duplicate events.

## Forward Account

Only one account may own the SQL `CURRENT` slot. Its initial capital is KRW 50,000,000 and four sleeve weights are immutable after creation. The creation snapshot is stored as `BASELINE`; status remains `WAITING_FOR_REVIEW` until a later completed weekly review.

Forward processing is transactional and idempotent per data version:

- the first review converts US sleeve cash once and creates market-specific next-open orders
- daily validated snapshots apply fills, dividends, splits, recoveries, positions, cash, and valuation
- data failures retain the last normal price and set `REVIEW_REQUIRED` instead of generating a sell
- archiving freezes the account, releases the `CURRENT` slot, and cancels unfilled buys
- annualized metrics stay hidden until at least 252 valuations exist

The SQL ledger uses `paper_accounts`, `paper_reviews`, `paper_orders`, `paper_trades`, `paper_positions`, `paper_cash`, and `paper_valuations`. Deterministic review dates, order idempotency keys, and account/data-version valuation constraints prevent duplicates after retries or API restarts.

## API

| Endpoint | Purpose |
| --- | --- |
| `POST /api/v1/research/replays` | Queue or reuse a replay |
| `GET /api/v1/research/replays/{id}` | Read stage, progress, and result |
| `GET /api/v1/research/replays/{id}/artifacts` | Read local artifact links |
| `GET /api/v1/research/candidate-history` | Filter candidate changes |
| `POST /api/v1/forward/accounts` | Create the only active account |
| `GET /api/v1/forward/accounts/current` | Read account state and ledger summary |
| `GET /api/v1/forward/accounts/{id}/activity` | Read reviews, orders, and trades |
| `POST /api/v1/forward/accounts/{id}/archive` | Freeze and archive the account |
| `POST /api/v1/forward/accounts/{id}/retry` | Retry the current data version |
