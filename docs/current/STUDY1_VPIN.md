# Study 1 VPIN v1: frozen research protocol

## Architecture and scope

This extends `feat/signal-research-v1` from verified local/remote HEAD `3ef2363`.
The user authorized continuing the incremental research path. The attached plan
is research context; it does not override repository contracts or unlock OOS.
Existing prepared samples, recorded delivery, causal feature scheduling, P0
freshness policies, original-run review, and Study 1 runner/validator are reused.
Core feature registry v3 and cache v2 identities remain unchanged. VPIN uses an
optional feature-engine provider and separate source-verified artifacts.

The existing `StrategyPolicy.observe_trade` receives delivered normal-volume
trades. Its VPIN memory shares the same calculator with the optional research
feature provider. The recorded arrival adapter additionally passes receive
timestamps and source references to that callback. Quote rounding, maker price
clamping, common risk checks, execution, accounting and queue/fill logic are
unchanged. VPIN v1 accepts recorded mode only. Inactive VPIN settings are omitted
from resolved legacy configurations to preserve previous run identities.

## Versioned definition

`normal-volume-vpin-v1` / `vpin_normal_volume` is an **aggressor-classified
normal-volume imbalance proxy**, not a calibrated informed-trading probability.
VPIN literature motivates equal-volume imbalance aggregation; classification
and parameters here are explicitly project-specific. See
[Abad and Yague, 2012](https://www.elsevier.es/en-revista-the-spanish-review-financial-economics-332-pdf-download-S2173126812000344).

The repository's `replay.py` normalizer already requires Binance `nq`, excludes
`q - nq`, and maps the buyer-maker flag to resting maker side. VPIN consumes
`event.trade.size` from that existing contract, never `total_trade_quantity`.
Maker sell means aggressive buy; maker buy means aggressive sell. This differs
from return-based bulk-volume classification. The legacy Binance documentation
URL redirected to a landing page during this audit; current API schema claims
are not inferred from that landing page or unrelated connectors.

Frozen first experiment:

- Bucket volume: **1 BTC**; rolling count: **20 completed buckets**.
- Value: sum of absolute buy-minus-sell volume over those buckets / 20 BTC.
- Split a trade exactly at bucket boundaries; carry its residual forward.
- A large same-side trade can complete multiple buckets at the same availability
  time. They remain distinct volume buckets, not independent information events.
- Reset all state at each prepared sample's bootstrap; consume available warmup
  trades. No cross-window or acquisition-boundary carryover.
- Until 20 buckets are complete, status is WARMUP and value is unavailable.
- A partial bucket never changes the published value or refreshes its source
  timestamp. Zero normal-volume trades do not refresh it either.
- A published value becomes STALE when either the latest completed bucket's
  source exchange age or client availability age exceeds **5 seconds**.
- Preserve original receive time, actual delivery time, completion source refs,
  oldest retained completion time, total consumed volume and residual volume.
- Fixed 50-digit Decimal arithmetic; bounded memory retains at most 20 buckets.
  Every snapshot verifies consumed volume equals completed volume plus residual.

These parameters are implementation hypotheses frozen before this study's
outcomes, not fitted to improve previous BP losses. Alternate scales require a
new explicit experiment and new artifact directory.

## Prerequisite before any execution

The runner first validates the accepted P0 multi-day report and all eight
September 7-8 DEVELOPMENT sample seals, then freezes the complete protocol.
It generates VPIN inputs at the existing **100 ms research cadence**, including
warmup and stale statuses, in isolated window workers. All eight input stages
must pass before any strategy execution is launched. Source volume conservation
and at least one available VPIN observation per window are required.

Offline toxicity diagnostics reuse existing CLIENT_FORWARD_RETURN labels and
the accepted P0 anchor/target freshness and coverage rules. The dependent
variable is the **absolute midpoint move in ticks** at 100 ms, 500 ms, 1 s and
5 s. This is a movement-risk proxy, not fill-conditioned adverse selection or a
directional hit-rate test. Report Pearson/Spearman associations and mean absolute
move in fixed VPIN bins with edges 0.2, 0.4, 0.6, 0.8, for overlapping and
non-overlapping observations. Empty bins and unavailable labels remain visible;
no per-window boundary is bridged and no statistical significance is claimed.
The diagnostics do not need to show a positive association to permit a clearly
labelled research comparison; they do not authorize promotion.

## Execution comparison

| Case | Existing inventory center | Additional half width |
|---|---|---|
| MM-1 | inventory skew = 1 | none |
| MM-3-zero | same | 0, including warmup/stale periods |
| MM-3 | same | 1 tick times VPIN |
| MM-3-constant | same | 0.5 tick, with the same VPIN availability gate |

Active and constant controls both pause desired quotes if VPIN is WARMUP/STALE.
The constant case is a fixed-width comparator, not a randomized placebo and not
a matched-average-width control. The zero coefficient must reproduce MM-1
economic summaries, orders, fills, execution, risk, ledger, timeseries and
markouts exactly under existing policy-name normalization. No VPIN diagnostic
or nonzero economic difference is discarded to force equality.

Existing maker and common risk policies remain active. Pending cancellations
may still fill. Per-decision traces retain the full VPIN state. The validator
replays inputs at actual 250 ms strategy decisions and compares state values,
volumes, bucket counts and provenance exactly. It also verifies withdrawal when
unavailable and zero-control quote-path equality. BP is not included in MM-3;
the combined MM-4 experiment remains separate.

Primary contrast: MM-3 minus MM-1 net PnL per independently reset window. Report
the constant control alongside it, including per-date results. Reuse original
run metrics and their existing markout/MTM definitions. Fees remain the frozen
zero-cost diagnostic; queue/participation remain uncalibrated assumptions.

```bash
PYTHONPATH=src python scripts/run_study1_baselines.py --stage vpin \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json \
  --output /path/to/NEW_VPIN_STUDY
PYTHONPATH=src python scripts/validate_study1_baselines.py \
  --report /path/to/NEW_VPIN_STUDY/study1_report.json \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development_report.json
```

The standalone validator source-replays the 100 ms prerequisite, rebuilds
toxicity diagnostics, and verifies its compressed artifact checksum before
accepting execution results. Validation/OOS remain locked; default strategy and
paper eligibility are not promoted by diagnostic rankings. Runtime evidence
must identify the exact code commit separately from later documentation commits.
