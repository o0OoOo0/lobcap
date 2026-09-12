# Study 1 completion protocol

## Scope

This protocol completes the remaining Study 1 attribution work on top of the
accepted replay, execution, queue/fill, accounting, Signal Research, BP, and
VPIN infrastructure. It does not replace those components. The simulator still
allows one live order per side; multi-order execution remains a Study 2 task.

The attached research-plan document supplies research direction. Repository
contracts, catalogue roles, causal delivery rules, and accepted P0 evidence are
authoritative for execution.

## Two-stage freeze before market outcomes

The input protocol is written first. All eight sealed DEVELOPMENT windows must
pass the existing 100 ms causal VPIN prerequisite. A separate 250 ms replay,
matching the strategy decision grid, computes the pooled mean of AVAILABLE VPIN
values. It uses no fills, labels, inventory, or PnL. Only then is the execution
protocol written with the exact mean, cases, stress scenarios, and gates.

The mean-width constant has the same intended extra half-width as the average
adaptive input over AVAILABLE decisions. Both cases use the same availability
gate. This is an input-distribution match; later common risk controls and
inventory feedback can still produce different realized paths.

## Phase 1 — VPIN decomposition

| Case | VPIN gate | Extra half-width |
|---|---|---|
| MM-1 | no | 0 |
| MM-3-zero | bypassed | 0; exact implementation control |
| MM-3-gate-only | active | 0 |
| MM-3-mean-width | active | DEVELOPMENT 250 ms mean VPIN × 1 tick |
| MM-3-vpin | active | current VPIN × 1 tick |

These contrasts separate implementation identity, availability withdrawal,
average widening, and value-dependent widening. Every comparison is paired
within independently reset windows.

## Phase 2 — BP and VPIN ablation

| Case | Inventory | BP center shift | VPIN widening |
|---|---:|---:|---:|
| MM-1 | yes | 0 | 0 |
| MM-4-zero | yes | 0 | 0; composite implementation control |
| MM-2-bp | yes | +1 × imbalance_1 | 0 |
| MM-3-vpin | yes | 0 | 1 tick × VPIN |
| MM-4-bp-vpin | yes | +1 × imbalance_1 | 1 tick × VPIN |
| MM-4-reversed-bp-vpin | yes | −1 × imbalance_1 | 1 tick × VPIN |

The combined policy adds the existing causal top-level imbalance center shift
to accepted VPIN policy memory. It reads no queue state or future label. The
report includes `(MM-4 − MM-2) − (MM-3 − MM-1)` for each accounting metric.
The sign-reversed case is fixed as a placebo-direction control.

## Phase 3 — execution-assumption robustness

MM-1, MM-2-bp, MM-3-vpin, and MM-4-bp-vpin are compared under six fixed
DEVELOPMENT scenarios:

| Scenario | Changed assumptions |
|---|---|
| baseline | repository recorded-clock defaults |
| fees_1bps | maker and taker cost = 1 bp |
| latency_50_50_100ms | entry/cancel/report = 50/50/100 ms |
| queue_alpha_0_25 | queue depletion α = 0.25 |
| queue_alpha_0_75 | queue depletion α = 0.75 |
| joint_fee_latency_queue | 1 bp costs, 50/50/100 ms, α = 0.75 |

These are sensitivity assumptions, not measured Binance account fees, latency,
or queue calibration. Phase 2 supplies the baseline. The five changed scenarios
add 160 executions. DEVELOPMENT totals 248 executions: 40 decomposition, 48
combined, and 160 sensitivity runs.

## Sequential validation and OOS gates

Validation remains sealed unless MM-4 has positive paired net-PnL delta from
MM-1 in baseline, on both DEVELOPMENT dates, in at least four of six scenarios,
and in joint stress, with no worse mean window maximum drawdown. Every condition
must pass.

If that gate passes, validation runs frozen MM-1 versus MM-4 under baseline and
joint stress. Pilot OOS remains sealed unless validation has positive pooled
delta, at least three of four positive windows, positive joint-stress delta, and
no worse mean drawdown. OOS is then one final baseline comparison. A failed gate
is a completed negative result and keeps later roles closed.

No result claims statistical significance, paper eligibility, production
readiness, or signal promotion. A standalone validator source-replays VPIN,
rebuilds causal decision audits and ledger comparisons from original artifacts,
recalculates every aggregation and gate, and does not rerun execution.

## Commands

```bash
PYTHONPATH=src python scripts/run_study1_completion.py \
  --config configs/backtest/binance_recorded.yaml \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json \
  --output /path/to/NEW_STUDY1_COMPLETION

PYTHONPATH=src python scripts/validate_study1_completion.py \
  --report /path/to/NEW_STUDY1_COMPLETION/study1_completion_report.json \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json
```

Output is create-once and requires a clean feature-branch checkout. Main, the
collector, prepared samples, prior studies, and application state are unchanged.
