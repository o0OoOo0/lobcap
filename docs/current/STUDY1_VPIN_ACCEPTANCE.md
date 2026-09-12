# Study 1 VPIN DEVELOPMENT evidence

## Runtime and protocol

Runtime commit: `abad814965658da42bc69511d9104c60d1f55e4b` on
`feat/signal-research-v1`, explicitly approved by the user for transfer and
validation on the existing `lob-collector-01` VM. Checkout:
`/home/0woo/lobcap-signal-p0-acceptance`; output under that checkout:
`artifacts/backtest/research/study1-vpin-v1`.

See [the frozen VPIN protocol](STUDY1_VPIN.md) for source semantics, bucket
rules, clocks, settings and controls. Existing P0 multi-day acceptance and all
eight September 7-8 DEVELOPMENT seals were checked before execution. Locked
validation/OOS outcomes were not opened. The simulator, accounting and queue/
fill model were reused. The strategy memory and recorded trade callback were
extended; core feature registry v3 and cache v2 identities were preserved.

## Test results

- Focused Windows regressions: 84 passed; after adding pooled prerequisite
  diagnostics, all 12 VPIN tests passed again.
- Ubuntu full suite: **453 passed**, two existing Streamlit/NumPy deprecation
  warnings, 141.98 seconds.
- Optional Windows full-suite attempt: collection stopped on two Linux
  `resource` imports. Excluding those two files yielded 429 passed, 1 skipped,
  2 failed and 5 errors (another resource import, Tardis artifact hash/missing
  fixture problems, and a Streamlit session-state KeyError). This is not a full
  Windows acceptance; no production/UI/fixture changes were made to conceal it.

Local logs and source reports are retained in
`E:/dev/lobcap-study1-vpin-evidence`.

## Multi-day VPIN prerequisite

All **eight prerequisite workers passed before the first strategy run**.
The input uses 1 BTC buckets, 20 completed buckets, normal quantity (`nq`) and
aggressor side from existing normalized trades, with a 5-second completion-age
limit. Five minutes of prepared warmup precede each 20-minute evaluation.

Of 96,000 research observations at 100 ms cadence, **74,599 were AVAILABLE** and
**21,401 were STALE**. None remained in WARMUP during these evaluation windows;
that does not eliminate the implemented completed-bucket warmup requirement.
Stale shares vary materially across windows and must be considered alongside
the strategy's quote-pause behavior.

| Window (UTC) | Available / 12,000 | Stale | Consumed normal BTC, including warmup |
|---|---:|---:|---:|
| Sep 7 00:10 | 10,289 | 1,711 | 2,225.202 |
| Sep 7 06:10 | 8,757 | 3,243 | 829.740 |
| Sep 7 12:10 | 8,108 | 3,892 | 830.424 |
| Sep 7 18:10 | 6,823 | 5,177 | 1,199.838 |
| Sep 8 00:10 | 9,085 | 2,915 | 1,541.261 |
| Sep 8 06:10 | 11,635 | 365 | 4,605.685 |
| Sep 8 12:10 | 10,688 | 1,312 | 2,496.670 |
| Sep 8 18:10 | 9,214 | 2,786 | 968.890 |

The dependent variable is absolute client-forward midpoint change in ticks,
using the accepted P0 anchor/target freshness and coverage rules. It is not
fill-conditioned adverse selection. At the 1-second horizon, separately
non-overlapping samples yielded 6,504 valid pairs: 3,372 on Sep 7 and 3,132 on
Sep 8. Pearson correlations were approximately **0.0224** and **0.0668** by
date, **0.0459** pooled. Window-level Spearman ranged from -0.00014 to 0.12553.
The effect is small and is not a statistical significance result.

For the pooled 1-second non-overlapping diagnostic, VPIN bins below 0.4 had
zero observations; [0.4,0.6), [0.6,0.8), and [0.8,1] contained 84, 2,530 and
3,890 pairs, with mean absolute moves of 2.75, 10.2494 and 12.0387 ticks.
Sparse/empty bins do not establish a well-supported full-range monotonic
relationship. Non-overlapping observations are still not independent dates.
All four horizons and overlapping counterparts remain in the source report.

## Execution and standalone revalidation

The runner completed **32 executions / eight windows**, including original
ledger review, zero-control economic parity and exact VPIN state/provenance
comparison at actual 250 ms strategy decisions. Total wall time, including all
prerequisites, was **52:55.60**. Maximum individual worker RSS was
**70.71484375 MiB**, below the 512 MiB acceptance gate. The service had a one-core
CPU quota, 768 MiB memory cap, Nice=10, and reported cgroup peak of 275.4 MiB
with no swap; process RSS and cgroup memory are different measurements.

Standalone source/artifact revalidation: **PASS**, at the same `abad814`
runtime commit, in **24 minutes 35.079 seconds**. It regenerated all 100 ms
feature rows and toxicity diagnostics from source, verified prerequisite hashes,
replayed actual-decision VPIN states, and rebuilt original paired reviews and
aggregates. All 32 ledgers reconciled and all zero controls matched. Execution
itself was not rerun by this validator. The shared calculator plus independent
replay adapters and hand-calculated tests do not constitute a separate raw
exchange-volume audit or fill-model calibration.

See [machine-readable acceptance evidence](STUDY1_VPIN_VALIDATION.json).
`ubuntu-tests.log`, `run.log`, and `review.log` are preserved in the VM result
directory and local evidence directory, with hashes in that JSON. Collector
PID 517216 remained active with NRestarts=0 at final verification. The VM
checkout remains clean at the exact approved execution commit; later commits
only record evidence on the feature branch. Main and the application were not
modified.

| Evidence | SHA256 |
|---|---|
| Study report | `da2ebe4142d53f553cd5f0194d430600833e3c170593de506caf30e2ab6c45e9` |
| Protocol digest | `a93c97235ebb2e17fd9e2185e4d4e37ed6132346c8e18494739b1ef652719553` |
| VPIN prerequisite report | `05d8245e27fda5543ee4c408ecf1f4618c4aaae360fd3ffbfec011f104add22c` |

## Frozen zero-fee execution results

Amounts sum independently reset windows in USDT, not a continuous equity curve.
Both treatments improved six windows and worsened two, but all aggregate net
results remained negative.

| Case | Sum net PnL | Delta from MM-1 | Filled / activated orders | Mean window maximum drawdown |
|---|---:|---:|---:|---:|
| MM-1 inventory skew | -7.777200 | reference | 1,534 / 5,018 | 1.270700 |
| MM-3-zero | -7.777200 | 0 | 1,534 / 5,018 | 1.270700 |
| MM-3 VPIN widening | -7.522350 | +0.254850 | 1,183 / 4,376 | 1.18678125 |
| MM-3-constant | -7.287700 | +0.489500 | 1,264 / 4,452 | 1.16873125 |

| Date | VPIN minus MM-1 | Constant minus MM-1 | VPIN minus constant |
|---|---:|---:|---:|
| 2026-09-07 | +0.231000 | +0.230650 | +0.000350 |
| 2026-09-08 | +0.023850 | +0.258850 | -0.235000 |
| Both | +0.254850 | +0.489500 | -0.234650 |

VPIN's net delta comprised +0.086250 spread capture and +0.168600 inventory MTM.
Its reset-window turnover fell by 27,809.945300 USDT. The constant control's
net delta comprised +0.038700 spread capture and +0.450800 inventory MTM, with
turnover down 21,396.962500 USDT. Thus lower turnover and modest loss/drawdown
reductions do not establish an advantage from the VPIN value itself.
The rerun MM-1 net sum agrees with earlier Study 1 stages; primary contrasts
always use same-commit runs, not mixed historical results.

The engine's volume-weighted signed 1-second midpoint drift was -5.98308 for
MM-1, -6.64142 for VPIN, and -6.34193 for the constant control (USDT/BTC price
units, not ticks). All corresponding 1-second fill observations were available
under the existing engine definition. The 100 ms and 5 s measures also became
more negative for VPIN than MM-1. Fill populations changed, so these are
descriptive conditional comparisons, not matched-fill causal estimates. They
do not support claiming improved fill toxicity merely from lower aggregate
losses. Engine markouts keep their existing definitions; P0's freshness and
coverage rules apply to the offline prerequisite labels, not retroactively to
these engine markouts.

## Decision-level effects

Each case had 38,400 actual decisions. VPIN states were AVAILABLE 29,875 times
and STALE 8,525 times, with no evaluation-period WARMUP states. Active and
constant controls had the same final reason counts: 7,718 `vpin_stale`, 5,069
`unknown_depth_coverage`, and 25,613 `quote`. The 807 remaining stale states
overlapped coverage failures, whose common-risk reason takes precedence. These
counts must not be added as disjoint populations. A `quote` reason does not
guarantee two live orders after sizing, coverage and asynchronous lifecycle.

VPIN changed 31,387 desired price/quantity paths versus MM-1; the constant
control changed 29,080. The zero control changed zero, with matching economic
summaries and lifecycle fingerprints. Differences include later inventory
feedback. Pending orders can still fill during cancellation; desired quote
withdrawal is not instantaneous removal from the exchange.

## Interpretation and next comparison

The implementation is available for incremental component research. **VPIN is
not promoted as a superior default strategy**, and paper eligibility remains
INSUFFICIENT_EVIDENCE. Its weak movement-risk association, sparse low-value bins,
poorer result than the constant control, and more negative conditional markouts
do not establish useful adaptive toxicity control under these settings.

To attribute the observed loss reduction, the next frozen ablation should add
an availability-gate-only control and a development-fitted, then frozen,
mean-width-matched constant control. The present 0.5-tick control was not matched
to VPIN's mean width. Cost/latency/queue sensitivity remains necessary; current
fees are zero and fill assumptions are uncalibrated. MM-4 (inventory + BP + VPIN)
can then be compared as a separate interaction experiment using this existing
infrastructure. Do not retune bucket/width settings to recover these windows'
losses or unlock validation/OOS based on this diagnostic ranking.
