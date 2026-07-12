# Local Real-Data Research

## Configure

Set these values in the ignored `.env` file:

```dotenv
APP_MODE=local_research
ARTIFACT_BACKEND=local
RESEARCH_ROOT=data/research
RESEARCH_HISTORY_YEARS=10
RESEARCH_AUTO_SYNC=true
KRX_ID=your-krx-login-id
KRX_PW=your-krx-login-password
TOSSINVEST_ENABLED=false
TOSSINVEST_CLIENT_ID=
TOSSINVEST_CLIENT_SECRET=
```

The public Docker Compose stack fixes `APP_MODE=public_demo`; real-data mode is intended only for direct local execution.

## KRX Authentication

`pykrx==1.2.8` creates an authenticated KRX session and downloads the stock and ETF basic-information reports. The application uses those reports only to build the Korean universe; price history continues to come from the local-only yfinance adapter.

Keep `.env` ignored and readable only by your user. Neither credential is stored in the database, universe manifest, logs, or public API responses.

## Optional Toss Standby Provider

Set `TOSSINVEST_ENABLED=true` and provide both Toss credentials to prepare the market-data connection. Open **관리** and select **연결 확인** to issue an in-memory OAuth token and query `005930` and `AAPL`. The screen stores only the latest status, check time, latency, and sanitized error code. It never displays credentials or tokens.

Toss remains outside the research pipeline: enabling or checking it does not replace yfinance data or change scores, replays, and forward accounts. The management APIs return `403` in `public_demo`.

## Optional KRX CSV Fallback

If pykrx authentication is unavailable, sign in to [KRX Data Marketplace](https://data.krx.co.kr/) and download:

1. `통계 > 기본 통계 > 주식 > 종목정보 > 전종목 기본정보`
2. `통계 > 기본 통계 > 증권상품 > ETF > 전종목 기본정보`

Reference the files without moving them into Git-tracked directories:

```dotenv
RESEARCH_KRX_STOCK_CSV=/absolute/path/to/krx-stocks.csv
RESEARCH_KRX_ETF_CSV=/absolute/path/to/krx-etfs.csv
```

Both fallback paths must be supplied together. A partial or empty KRX result never activates a new snapshot.

## Run

```bash
make api
make web
```

`make api` applies the current Alembic migration before starting FastAPI. Open `http://127.0.0.1:3000`. Startup requests a sync when the current snapshot is missing or stale. The same operation can be retried from the dashboard. A repeated request reuses the active run instead of starting a second concurrent collection.

`price-pipeline-v2.0.0` enables provider price repair and adds row-level repair provenance. The first sync after upgrading from the previous pipeline intentionally downloads the full ten-year history once; subsequent runs return to incremental collection.

The sync stages are `UNIVERSE -> DOWNLOAD -> MATERIALIZE -> VALIDATE_RAW -> REPAIR -> SCORE -> VALIDATE_SCORE -> ACTIVATE`. Open **데이터 품질** in local mode to inspect coverage, checks, quarantined assets, repaired assets, and CSV issue exports. A failed quality gate never replaces the last normal `current.json` pointer.

## Storage

```text
data/research/
  universes/<version>/
  checkpoints/<universe>/<peer-group>/
  snapshots/<data-version>/
    bars/peer_group=<group>/year=<year>/bars.parquet
    scores/latest.parquet
    scores/history.parquet
    quality/summary.json
    quality/issues.parquet
    universe.csv
    manifest.json
  quality-runs/<sync-id>/
    summary.json
    issues.parquet
  replay-cache/<cache-key>/peer_group=<group>/year=<year>/scores.parquet
  leases/<data-version>/<run-id>
  forward/signals/<data-version>/candidates.parquet
  forward/accounts/<account-id>/scores/<data-version>.parquet
  current.json
```

Only a fully materialized, scored, and quality-approved snapshot updates `current.json`. The latest three snapshots and latest ten run-level quality reports are retained. Raw data, score files, quality reports, and local database files are ignored by Git.

Open **과거 시뮬레이션** to run the current-listed universe through `portfolio-v1.0.0`. A completed run provides summary, cause analysis, trade quality, and replay-integrity tabs plus HTML/JSON/CSV and Parquet ledgers. Open **후보 이력** for daily candidate changes. **포워드 포트폴리오** creates one active account, freezes its sleeve weights, stores the current snapshot as `BASELINE`, and waits for the next completed weekly review before creating orders. These local-only APIs return `403` in `public_demo`.
