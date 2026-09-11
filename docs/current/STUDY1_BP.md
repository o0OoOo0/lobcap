# Study 1 Book Pressure research extension

## Scope and frozen experiment

This extends the accepted MM-0/MM-1 work on `feat/signal-research-v1` at e22a886.
The user authorized continuing incremental component research. Adding BP to an
offline experiment is not promotion to a validated or paper-ready strategy.
VPIN remains a subsequent volume-bucket/toxicity research step.

The existing expression policy implements the tick-valued center shift:

`bp_beta * imbalance - inventory_skew_ticks * inventory / max_inventory`

Here `imbalance` is the current client-observed `(bid_size - ask_size) /
(bid_size + ask_size)`, matching the existing `imbalance_1` feature on usable
observations. Inventory is client-known inventory. No future labels or cached
outcomes enter the quote path. Replay, execution, accounting, risk control,
expression evaluation and queue/fill models are reused without modification.

| Case | Existing policy | BP coefficient |
|---|---|---:|
| MM-1 | inventory_skew | absent |
| MM-2-zero | expression with inventory term | 0 |
| MM-2 | expression with inventory term | +1 by default |
| MM-2-reversed | same expression | -1 by default |

The positive coefficient comes from the input config's `signal_strength` and is
frozen before opening execution outcomes. The existing recorded config sets it
to 1; the inventory coefficient stays 1. No coefficient search is performed.
The primary descriptive contrast is MM-2 minus MM-1 net PnL per reset window;
reversed minus MM-1 is a separate control. The reference is rerun at the same code
commit as all treatments. The prior MM-1 results are not mixed across commits.
Cash, inventory limits, size, spread, fees, clocks, quote start, decision cadence,
participation and queue assumptions remain shared. Recorded clock is required,
volatility controls remain inactive and no caller-supplied expression is allowed.

## Prerequisites and acceptance

The accepted P0 report and all selected sample seals must validate first. The
existing Study 1 runner and standalone validator now accept `--stage bp`; their
default baseline behavior and old report schema remain supported. The eight
fixed September 7–8 DEVELOPMENT windows are retained, including low-coverage
windows. Validation and pilot OOS remain locked.

For every window, existing original-run ledger review must pass. The expression
zero control must match MM-1 economic summaries, orders, fills, ledger, execution,
risk, timeseries and markouts. Only policy-name metadata and zero-valued
expression diagnostic counters / the normal `signal_reason=quote` annotation
are normalized; nonzero clipping or unavailable counters cannot be discarded.

The original causal feature engine independently replays normalized data at each
actual strategy decision (250 ms by default), comparing:

- decision time, depth source exchange time, and client availability time exactly;
- client midpoint and imbalance values at the existing offline v3 28-digit
  Decimal precision (runtime uses 50 digits; this is a representation alignment);
- no desired quotes when coverage is unknown or freshness fails;
- zero-control desired quote paths and positive/reversed path differences;
- the existing trace's tick rounding, price-policy and risk stage changes.

The engine's exchange-age stale-book rule is stricter than the offline feature's
client-availability-age rule under delayed feeds. Both are required for a usable
BP observation. The allowed stale threshold cannot exceed the v3 500 ms bound.
The combined expression clamp must be nonbinding over the allowed inventory and
BP range; price rounding, maker clamping and risk gates still apply and are
reported. Pending orders may fill during cancellation under the existing model;
"no desired quote" does not assert instantaneous withdrawal of live orders.

This does not repair missing historical depth. It prevents using untrusted BP
for desired quotes and exposes lost observations. Existing PnL/markout definitions
remain separate from P0 labels. Window sums are independent reset diagnostics,
not continuous-account returns or significance estimates.

```bash
PYTHONPATH=src python scripts/run_study1_baselines.py --stage bp \
  --config configs/backtest/binance_recorded.yaml \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development-coverage-v3/development_report.json \
  --output /path/to/NEW_BP_STUDY

PYTHONPATH=src python scripts/validate_study1_baselines.py \
  --report /path/to/NEW_BP_STUDY/study1_report.json \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development-coverage-v3/development_report.json
```

The standalone validator regenerates causal parity from source samples as well
as rebuilding paired metrics from original run artifacts. All 32 runs and eight
window workers must pass before final acceptance. Runtime evidence must be
recorded separately at the exact execution commit. Paper eligibility remains
INSUFFICIENT_EVIDENCE regardless of the diagnostic ranking.
