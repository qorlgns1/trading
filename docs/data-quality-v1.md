# Data Quality v1

`data-quality-v1.0.0` controls whether a local real-data snapshot can replace the currently active snapshot. It does not claim that a free market-data source is authoritative; it verifies that inputs and derived scores are internally usable and traceable.

## Activation Flow

```text
collect -> materialize -> validate raw bars -> repair affected assets once
        -> validate raw bars again -> calculate scores -> validate scores and coverage
        -> write report -> atomically activate
```

Provider repairs are recorded in `provider_repaired`. An invalid asset is fetched again for its full configured history. If the same error remains, the asset receives `INVALID_DATA`, is omitted from Trend Score calculation, and remains searchable with the exclusion reason.

## Decisions

| Decision | Conditions |
| --- | --- |
| Block snapshot | Missing required columns, duplicate asset/date, future or out-of-range dates, universe mismatch, invalid benchmark/FX, score invariant failure, fewer than 30 ready assets in a peer group, quarantine above `max(5, 1%)`, or ready-rate drop of at least 20 percentage points |
| Quarantine asset | Non-positive or non-finite price, negative/non-finite volume, invalid dividend/split, or an adjusted-price error that remains after full-history retry |
| Warn and continue | Download failure, fewer than 253 sessions, stale latest session, extreme return/distribution, provider repair, or ready-rate drop of at least 5 percentage points |

An isolated quarantine is intentionally not a global failure when every peer group still has sufficient coverage. The old snapshot remains active whenever a global check fails.

## Artifacts and API

- Active report: `snapshots/<data-version>/quality/summary.json`
- Active issues: `snapshots/<data-version>/quality/issues.parquet`
- Run report: `quality-runs/<sync-id>/` (latest ten retained)
- `GET /api/v1/research/quality`
- `GET /api/v1/research/quality/issues`
- `GET /api/v1/research/quality/issues.csv`
- `GET /api/v1/research/sync/{sync-id}/quality`

These files and endpoints exist only in `local_research`. Public demo requests receive `403`, and real-data quality artifacts are excluded from Git and public OCI deployment.

## Score Trace

Local asset detail returns the exact data version, score version, configuration hash, moving averages, period returns, high ratio, annualized volatility, ADV60, peer ranks, eligibility flags, component sum, and final score used for the latest decision. This trace explains a calculation; it is not an investment recommendation or a data-provider guarantee.
