# Quant Trend Lab

Quant Trend Lab is an explainable trend-screening product for US and Korean stocks and equity ETFs. The public application uses a deterministic fictional market, while a private local mode discovers current securities and computes candidates from real end-of-day prices without redistributing market data.

> Trend Score measures how well an asset satisfies predefined price-trend conditions. It is not a buy recommendation, return forecast, or probability of a price increase.

## Product Surface

- Six peer-group Trend Score screens with deterministic reasons, warnings, and exclusion codes
- Asset detail with price, moving averages, score history, and six score components
- Resumable ten-year local collection with exchange-universe snapshots and daily catch-up
- Versioned data-quality gates, full-history repair, per-asset quarantine, and score traces
- Server-side search, filtering, pagination, data-health states, and 65/60 candidate hysteresis
- Public synthetic backtests plus local current-universe historical replay with reconciled cause analysis and an explicit survivorship-bias warning
- Daily candidate-change history and a forward-only paper ledger that starts after account creation
- Integer shares, separate KRW/USD cash, market-specific next-session fills, and explicit transaction costs
- HTML, CSV, JSON, and Parquet result artifacts
- Public guest limits: five new runs per IP per hour and one concurrent run

## Architecture

| Layer | Stack |
| --- | --- |
| Web | Next.js 16, React 19, TypeScript, Tailwind CSS 4, Radix UI, ECharts |
| API and jobs | FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, Celery |
| Quant engine | Python 3.12, Polars, DuckDB, PyArrow, NumPy |
| Runtime data | PostgreSQL 17, Valkey 8, Parquet, OCI Object Storage |
| Operations | Docker Compose, Caddy, GitHub Actions, OCIR, OCI Resource Manager |

The framework-independent engine is in `packages/quant-core`. Product and infrastructure decisions are documented in [architecture](docs/architecture.md), [Trend Score v1](docs/trend-score-v1.md), [data quality v1](docs/data-quality-v1.md), [portfolio strategy](docs/portfolio-strategy-v1.md), [data policy](docs/data-policy.md), and [OCI operations](docs/operations.md).

## Local Development

Prerequisites: Python 3.12, `uv` 0.8+, Node.js 24, pnpm 10, and Docker for the container path.

```bash
uv sync --all-packages --all-groups
pnpm install
uv run python scripts/export_openapi.py
pnpm --filter web generate:api
```

Run the API and web application in separate terminals:

```bash
make api
make web
```

Open [http://localhost:3000](http://localhost:3000). The API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs).

For the container stack, create `.env` from `.env.example`, set local-only passwords, then run:

```bash
docker compose up --build
```

The local override publishes the web app on port 3000 and API on port 8000. PostgreSQL and Valkey are not published.

## Quality Gates

```bash
make lint
make typecheck
make test
make test-integration
make build
pnpm --filter web test:e2e
```

`make test` uses SQLite for fast isolated unit and API tests. `make test-integration` starts a disposable PostgreSQL 17 container on an automatically assigned local port, applies Alembic migrations, checks model/schema drift, runs repository integration tests, and removes the container and temporary database afterward.

CI runs both test layers independently, regenerates OpenAPI and TypeScript contracts, and fails when committed contracts drift. Main-branch images are built for both `linux/amd64` and `linux/arm64` and pushed to private OCIR repositories.

## Real-Data Research

Real data is deliberately unavailable in `public_demo`. For local research:

```bash
cp .env.example .env
# Set APP_MODE=local_research and keep ARTIFACT_BACKEND=local.
make api
make web
```

The local application snapshots the current US directory from Nasdaq Trader. Korean stock and ETF basic information is collected through the pinned `pykrx` adapter using `KRX_ID` and `KRX_PW`. These values belong only in the ignored local `.env`; they are masked by the settings model and never written to a manifest. Authenticated KRX CSV exports can still be supplied through `RESEARCH_KRX_STOCK_CSV` and `RESEARCH_KRX_ETF_CSV` as a fallback.

Toss Securities Open API can be configured as an optional standby provider. It is checked only when the user selects **관리 > 연결 확인** and never participates in collection, scoring, replay, or forward processing. Tokens remain in memory and the database stores only the latest sanitized connection result.

The API catches up on startup, polls for completed Korean and US trading sessions, and resumes completed download batches after interruption. Before activation it repairs provider-detected price errors, validates raw bars and score invariants, and quarantines assets whose errors remain. Downloaded data and real-data results remain under `data/research`, are excluded from Git, and are never exposed by the public OCI deployment. See [local real-data operations](docs/local-research.md).

Current-listed-security history is not a point-in-time universe. Local mode therefore exposes its ten-year result only as a **current-universe historical simulation with survivorship bias**, never as an official performance claim. The forward portfolio is stored separately and begins with the first completed weekly review after the user creates an account. See [historical replay and forward ledger](docs/replay-forward-v1.md) and [replay analysis](docs/replay-analysis-v1.md).

## License

MIT. Market-data provider terms remain separate and are not granted by this repository.
