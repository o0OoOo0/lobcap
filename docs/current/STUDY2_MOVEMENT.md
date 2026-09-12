# Study 2 B1 — movement, excursion, and quote-horizon research

## Scope

Study 2 begins with quote-geometry evidence. This phase reuses the accepted
recorded replay, Signal Research P0 freshness policy, sealed multi-day catalogue,
and immutable Study 1 MM-1 order artifacts. It does not add ladder orders or
change execution, queue/fill, accounting, strategy, collector, or application
code.

Only the eight September 7–8 DEVELOPMENT windows are opened. September 9
validation and September 10 pilot OOS remain sealed. The attached research plan
sets the direction; repository data and causal contracts remain authoritative.

## Input freeze and regimes

Before any future movement is measured, a 100 ms causal input pass extracts
`abs(mid_return_1000ms)` from the existing core feature registry. Its unit is
bps. The pooled DEVELOPMENT 1/3 and 2/3 quantiles define LOW, NORMAL, and HIGH
recent-movement regimes. The thresholds must be distinct and every regime must
contain observations. They are then frozen in the movement protocol.

This classification is available at each analysis time and does not use the
subsequent movement being evaluated. It is a short-horizon movement regime, not
a full volatility model or a production regime controller.

## Movement definitions

At each 100 ms analysis time `t`, the anchor is the causal client midpoint known
at `t`. For `h` in 100 ms, 500 ms, 1 s, and 5 s:

```text
final displacement = (truth_mid(t+h) - client_mid(t)) / tick
absolute displacement = abs(final displacement)
up excursion = max(0, max_u (truth_mid(t+u) - client_mid(t)) / tick)
down excursion = max(0, max_u (client_mid(t) - truth_mid(t+u)) / tick)
maximum absolute excursion = max(up excursion, down excursion)
```

The path covers exchange-truth midpoint states from the decision through the
horizon. Up/down excursions map to long MFE/MAE respectively; for a short quote
the mapping reverses. This keeps side interpretation explicit without creating
hypothetical fills.

The accepted P0 horizon-specific anchor/target freshness policy is reused.
Anchor, target, and every intermediate path state must have known depth
coverage. Sample-end, stale, missing, acquisition-boundary, and unknown-coverage
observations retain separate statuses and do not enter distributions.

## Distribution contract

For signed displacement, absolute displacement, up excursion, down excursion,
and maximum absolute excursion, every pooled, per-date, and per-regime report
contains count, mean, population standard deviation, minimum, maximum, and
p50/p75/p90/p95/p97/p99.

Both overlapping 100 ms-grid observations and per-window non-overlapping
observations are retained. Overlapping rows are descriptive. Ladder spacing uses
the non-overlapping maximum-absolute-excursion distribution.

## Existing order exposure

The phase requires the accepted VPIN Study 1 report and reuses each window's
MM-1 `orders.jsonl.gz`. It verifies the report is DEVELOPMENT-only, paths stay
under the accepted study root, samples match catalogue seals, run evidence is
rankable, and ledgers reconcile. The simulator is not rerun.

Distributions are reconstructed for activation-to-terminal lifetime,
submission-to-terminal time, activation-to-first-fill time, and replacement
downtime. Censored active orders contribute their observed exposure through
sample end. Unresolved replacement intervals are counted separately.

## Spacing candidate

The primary movement horizon is the available horizon nearest the pooled median
MM-1 active order lifetime. At that horizon, non-overlapping maximum excursion
p50/p75/p90/p97 form raw spacing candidates. Values are rounded outward to
positive integer ticks and duplicates are removed. Pooled and LOW/NORMAL/HIGH
geometries are reported.

These levels are research geometry only. They do not establish fill probability,
profitability, a required number of levels, or permission to implement a ladder.
Phase B2 still requires the separately audited multi-order execution extension
with shared event volume/participation budgets and aggregate inventory
reservation.

## Validation

The standalone validator source-replays both causal regime inputs and every
movement/excursion row, refits thresholds, rebuilds all distributions, rereads
the original MM-1 order artifacts, and recomputes horizon and spacing selection.
It does not rerun strategy execution.

```bash
PYTHONPATH=src python scripts/run_study2_movement.py \
  --config configs/backtest/binance_recorded.yaml \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json \
  --study1-report /path/to/study1-vpin-v1/study1_report.json \
  --output /path/to/NEW_STUDY2_MOVEMENT

PYTHONPATH=src python scripts/validate_study2_movement.py \
  --report /path/to/NEW_STUDY2_MOVEMENT/study2_movement_report.json \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json \
  --study1-report /path/to/study1-vpin-v1/study1_report.json
```

Outputs are create-once and require a clean feature-branch checkout. Statistical
significance, validation/OOS performance, paper eligibility, and production
readiness are not claimed.
