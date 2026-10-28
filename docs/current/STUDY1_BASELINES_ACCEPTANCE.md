# Study 1 baseline acceptance and development findings

**PASS**: eight fixed DEVELOPMENT windows, 24 existing-engine executions,
all zero-coefficient controls equivalent to MM-0, and all ledgers reconciled.
Standalone validation reconstructed the paired review and aggregation from the
original run artifacts. This completes the MM-0/MM-1 baseline connection and
paired multi-day infrastructure, not signal promotion or Study 2 execution.

## Evidence

- Runtime commit: `36af9bb1d4dfd7b03bc26d24c2712d2578d93622`.
- Read-only validator commit: `a480c13b6da2b633742233a740cb4dd310529863`.
- Report SHA256:
  `507551aeda1e1cd33a077262d1e75f566800cd839789f86b6e74b0ce75b5934e`.
- Protocol SHA256:
  `43ef795a913c4e9f2b0db0d21c4cb0984680cc83204a60fa2f26c26e15fbec51`.
- P0 prerequisite: accepted v3 report, SHA256
  `e68f5148ceea431b782fb75af81402e319a8c9d4673023daa58d0b0b86bd65ff`.
- Catalogue SHA256:
  `cc676289c752418edb37ad9adc650dd4e6c611d19bd1bf4170f15fde6a765aae`.
- Data-host output:
  `/home/<vm-user>/lobcap-signal-p0-acceptance/artifacts/backtest/research/study1-baselines-v1`.
- Runtime 22 min 11.99 s, exit 0, no swap; largest worker **70.46484375 MiB**.
  Unit peak 159.0 MiB includes the parent and workers. Limits were MemoryMax=768M,
  CPUQuota=100%, Nice=10, RuntimeMaxSec=14400.
- Runtime commit Ubuntu regression: **429 passed**, two existing Streamlit/NumPy
  deprecation warnings, 133.70 s. Final validator commit Ubuntu regression:
  **434 passed**, the same two warnings, 199.20 s. Machine-readable evidence
  is retained in `STUDY1_BASELINES_VALIDATION.json`.
- Focused extension tests: **24 passed** including exchange/recorded zero parity,
  nonzero order changes, real sweep/review integration, seal drift, missing and
  duplicate windows, parameter drift, report/path/memory/role corruption, and
  P0 prerequisite and overwrite guards.

The remote checkout stayed at the runtime commit throughout all 24 simulations.
The validator was installed only after they exited. Existing simulator, queue,
accounting, GLFT, collector and application code were not changed; main was not
modified. Original per-run summaries, traces, diagnostics and manifests remain
available. The standalone validator is an artifact review, not an independent
source-volume audit or fill-model calibration.
Collector PID 517216 remained active with NRestarts=0 through completion.

## Result under the frozen zero-fee scenario

Amounts below sum independent reset-window accounting; they are **not** a
continuous account equity curve. Net deltas are MM-1 minus MM-0, in USDT.

| Date | Windows | Sum net delta | Positive / negative / zero windows |
|---|---:|---:|---:|
| 2026-09-07 | 4 | +0.457850 | 1 / 3 / 0 |
| 2026-09-08 | 4 | -0.530100 | 1 / 3 / 0 |
| Both | 8 | -0.072250 | 2 / 6 / 0 |

MM-0 net summed to **-7.704950 USDT**, MM-1 to **-7.777200 USDT**.
The aggregate difference consists of +0.002200 spread capture and -0.074450
inventory MTM, with zero fees. Mean per-window drawdown decreased by 0.10128125
USDT, while turnover summed across reset windows decreased by 11,018.255100
USDT. These are different objectives: a modest average drawdown reduction does
not establish a net-PnL improvement. Both policies lost money in this diagnostic.
The day-level direction also changed, so the default one-tick inventory
coefficient is not promoted as a superior strategy.

## Coverage and interpretation limits

The existing common risk control stopped quote generation for unknown depth
coverage on 411/4,800 decisions in September 7 00:10, 992/4,800 in September 8
06:10, and 3,666/4,800 in September 8 18:10. Counts matched MM-0 and MM-1 in
each window. These are counts at the existing 250 ms strategy decision cadence,
not elapsed-time coverage or the 100 ms predictive grid's label coverage.

All eight pairs met the existing filled-order and reconciled-ledger evidence
thresholds. That does not remove coverage limitations. In particular, inventory
MTM and existing engine markouts keep their existing definitions; they are not
certified to share P0's offline label freshness/coverage rules. Low-coverage
windows remain visible and were not replaced with better-looking data.

Fees were assumed zero, queue alpha and participation were not empirically
calibrated, only two development dates were evaluated, and no statistical
significance is claimed. Lower turnover can affect relative ranking under
different fee assumptions; any cost-aware conclusion requires an explicitly
frozen sensitivity experiment. No validation/OOS outcomes were opened.

## Next gate

P0-B retains BP/VPIN signals as not promoted. Before adding them to quote
generation, address coverage/history quality and require causal offline/runtime
parity under a frozen protocol. For the baseline itself, freeze cost/latency/
queue sensitivities before further evaluation; do not tune the inventory
coefficient to recover these eight windows' PnL. Study 2 still requires the
separate shared-budget, order-identity, priority and reservation extension of the
existing execution engine. Paper eligibility remains INSUFFICIENT_EVIDENCE.
