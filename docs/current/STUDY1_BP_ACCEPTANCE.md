# Study 1 BP DEVELOPMENT execution evidence

## Frozen scope and provenance

The research-only BP extension uses the existing simulator and expression
policy; see [protocol and acceptance rules](STUDY1_BP.md). Runtime commit was
`5113a8fce39b60516e473f2129f68ad40df5351e` on `feat/signal-research-v1`.
The original MM-1 inventory coefficient is 1 tick; BP coefficients are 0, +1
and -1. No search or outcome-based replacement was performed.

- Runtime host: existing `<collector-vm>` Ubuntu VM.
- Checkout: `/home/<vm-user>/lobcap-signal-p0-acceptance`.
- Output: `artifacts/backtest/research/study1-bp-v1` under that checkout.
- Report SHA256: `fc919f53ef12aa76181244f391d06f009bd95f5a0bafba31863a59a9eca99ac0`.
- Protocol digest: `d7af09357e0f6d2d74a5871d5621f2d9b9930d8989503c8e869f0a0b004c3c96`.
- P0 report SHA256: `e68f5148ceea431b782fb75af81402e319a8c9d4673023daa58d0b0b86bd65ff`.
- Catalogue SHA256: `cc676289c752418edb37ad9adc650dd4e6c611d19bd1bf4170f15fde6a765aae`.

All eight fixed September 7-8 DEVELOPMENT windows were used, with 20-minute
execution periods and 5-minute warmup. September 9 validation and September 10
pilot OOS outcomes remain locked. The accepted P0 multi-day prerequisite was
validated before execution. Replay, execution, queue/fill, accounting, GLFT,
collector and application implementation were not modified; main was untouched.

## Verification

Local focused regressions: **75 passed**. Ubuntu full suite: **441 passed** in
109.51 seconds, with two existing Streamlit/NumPy deprecation warnings.

The runner completed all **32 executions / 8 windows**, including economic
zero-control parity, original-ledger review, and source-replayed causal BP
parity at every actual decision. Total wall time was **38:06.77**; maximum
worker RSS was **70.83203125 MiB**, below the 512 MiB gate. The service used a
one-core CPU quota, 768 MiB memory limit and Nice=10; reported cgroup peak was
191.5 MiB with no swap. These cgroup and individual-process memory measures
are different quantities.

Standalone artifact and causal revalidation: **PASS**, at the same runtime
commit, in 8 minutes 25.954 seconds. The validator rebuilt paired metrics from
original run artifacts and independently replayed features; it did not rerun
execution or calibrate the fill model. All zero controls matched and all ledgers
reconciled. See [machine-readable evidence](STUDY1_BP_VALIDATION.json).
`ubuntu-tests.log`, `run.log` and `review.log` are preserved under the VM output
directory; local copies and the original report are in
`E:/dev/lobcap-study1-bp-evidence`. Log hashes are included in the evidence JSON.
Collector PID 517216 remained active with NRestarts=0 at final verification.

## Frozen zero-fee diagnostic result

The following sums are independent reset-window accounting in USDT, not a
continuous account equity curve or a statistical significance claim.

| Case | Sum net PnL | Delta from MM-1 | Better / worse / equal windows |
|---|---:|---:|---:|
| MM-1 inventory skew | -7.777200 | 0 | reference |
| MM-2-zero | -7.777200 | 0 | 0 / 0 / 8 |
| MM-2 BP +1 | -8.103750 | -0.326550 | 5 / 3 / 0 |
| MM-2-reversed BP -1 | -8.199850 | -0.422650 | 4 / 4 / 0 |

| Date | BP +1 net delta | BP -1 net delta |
|---|---:|---:|
| 2026-09-07 | -0.132950 | -0.531150 |
| 2026-09-08 | -0.193600 | +0.108500 |

BP +1 improved more windows than it worsened, but its losses outweighed those
improvements. Its spread capture delta was +0.077550 USDT and inventory MTM
delta was -0.404100 USDT. Mean per-window maximum drawdown increased by
0.0247625 USDT. Reset-window turnover decreased by 20,991.073600 USDT.
BP -1 was also worse in aggregate; being better than this reversed control
does not establish superiority to MM-1. The rerun MM-1 net sum agrees with
the prior baseline study; the primary contrasts use only same-commit runs.

## Input and quote-path evidence

Each case made 38,400 decisions across eight windows. The existing causal
feature engine found 33,331 usable observations and 5,069 unusable observations.
The latter were coverage-limited: 411 in September 7 00:10, 992 in September 8
06:10, and 3,666 in September 8 18:10. No desired quotes were produced on those
unusable observations. This does not assert that previously live orders could
not fill while cancellation was pending.

BP +1 differed from MM-1 in 16,369 desired quote price/quantity paths; BP -1
differed in 15,707. The zero control differed in zero paths, with matching
economic lifecycle and accounting fingerprints. These path differences include
later inventory feedback, so they are not counts of isolated instantaneous BP
effects. Rounding, maker-price clamping and common risk controls remained active;
per-case stage-change counts are retained in the report. Causal parity compares
timestamps exactly and Decimal feature values at the offline v3 28-digit
precision, with runtime 50-digit representations rounded for comparison.

## Interpretation and next step

BP +1 is **implemented and accepted for research comparison**, not promoted as
a better default strategy. The zero-fee scenario, uncalibrated queue and
participation assumptions, two development dates, coverage losses, and existing
markout/MTM definitions limit the conclusion. No coefficient was retuned after
seeing these outcomes. Paper eligibility remains **INSUFFICIENT_EVIDENCE**.

The next incremental component is VPIN: add a causal volume-bucket feature
through the existing trade/feature path, freeze trade classification, bucket
size, completed-bucket availability and warmup rules, validate inputs before
strategy use, then add a separately configured toxicity control with a zero
control and paired comparison. The current repository has no dedicated VPIN
implementation. Predictive evidence and execution utility remain separate
questions. Cost/latency/queue sensitivities also need a frozen protocol. Study 2
still needs the previously identified multi-order execution extension.
