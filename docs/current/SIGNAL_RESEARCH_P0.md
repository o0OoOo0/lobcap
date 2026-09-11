# Signal Research P0 integrity and catalogue

This release moves Signal Research from a single-sample feature prototype to a
sealed multi-day research foundation. It does not select a strategy or make a
profitability claim.

## Fixed pilot catalogue

`sample_catalogue.fixed_pilot_windows()` fixes four 20-minute windows per day at
00:10, 06:10, 12:10 and 18:10 UTC, each with five minutes of warmup:

- 2026-09-07 and 2026-09-08: `DEVELOPMENT`
- 2026-09-09: `VALIDATION_LOCKED`
- 2026-09-10: `PILOT_OOS_LOCKED`

Each window is retained as `PASS` or `EXCLUDED`; a failed interval is never
silently replaced. A PASS seal binds exact bounds, raw and normalized hashes,
normalization/schema versions, one acquisition segment, reconstruction evidence
and operational diagnostics. Locked windows expose continuity evidence but keep
descriptive and predictive market outcomes sealed.

Build it serially on the Ubuntu data host:

```bash
PYTHONPATH=src python scripts/build_signal_catalogue.py \
  --config configs/backtest/binance_recorded.yaml
```

The command writes only new prepared samples and catalogue artifacts below
`artifacts/backtest`. It does not alter raw capture files or the collector.

## Feature contract

`feature_registry.py` is the metadata source of truth for the frozen core
feature set. Tick-valued signals use `ticks`, rolling signals expose `WARMUP`,
and N-level signals require N live levels on both sides within reconstructed
coverage. Missing depth is `INSUFFICIENT_DEPTH`, never zero. Mid-return points
retain their actual lookback anchor and age.

Feature cache v2 binds the sample, schedule, clock, tick, bootstrap receipt,
acquisition segment, normalization/schema versions, code fingerprint, canonical
feature definitions and feature configuration. Version-1 caches fail closed.

## Analysis boundary

Freshness thresholds may be frozen only from exact DEVELOPMENT windows spanning
at least two dates. The threshold is the worst per-window p95 for each horizon,
so a high-volume window cannot mask a slower one. The policy artifact retains
all input cadence evidence.

Predictive rows now include feature dispersion and quantiles, Pearson and
Spearman IC, sign hit rate, non-overlapping diagnostics, and optional buckets
using externally supplied frozen edges. Significance remains `NOT_CLAIMED`.
Pairwise feature correlation and coverage overlap are available for redundancy
review.

Every execution manifest records the selected scheduler implementation.
Comparison fails closed when scheduler identity is absent or differs. The
strategy-dependent routing remains visible until an output-parity proof supports
a safe refactor.

The next data step is to build the catalogue on Ubuntu, inspect only Sep 7–8
cadence, freeze freshness and bucket policies, then generate per-window,
per-date and pooled DEVELOPMENT reports. Validation and pilot OOS remain closed
until the human review gate.
