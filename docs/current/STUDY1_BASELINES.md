# Study 1 baseline and inventory-control pairs

## Research decision

The P0-A multi-day pipeline passed at runtime commit 4f309df. P0-B review retains
all signals as **not promoted**: only two development dates were evaluated,
coverage varies substantially, microprice/imbalance are redundant, and rolling
history quality needs review. None of these predictive observations establishes
quote-path survival or net profitability. Validation and pilot OOS remain locked.
MM-0/MM-1 can proceed without a promoted predictive signal.

## Frozen baseline experiment

`run_study1_baselines.py` validates the accepted P0 report and every selected
catalogue sample before it writes a protocol or starts simulation. It opens all
and only PASS DEVELOPMENT windows, retaining quality exclusions without replacing
them. The immutable protocol binds the P0 report hash, catalogue hash, clean Git
commit, configurations and exact window IDs before execution.

| Case | Existing policy | Inventory coefficient |
|---|---|---|
| MM-0 | fixed_spread | 0 |
| MM-1-zero | inventory_skew | 0 |
| MM-1 | inventory_skew | configured positive coefficient |

The existing policy shifts quote center by
`-inventory_skew_ticks * tick * client_inventory / max_inventory`. It is not a
new size-control strategy. All other settings are shared: quote size and spread,
initial capital/inventory, inventory limits, fees, latency, participation, queue,
decision cadence, quote start, partial-fill and replacement policy. Signal
strength is zero; volatility controls and expressions are rejected. The default
recorded configuration uses one tick of inventory skew at the inventory limit,
0.5 tick half-spread, 0.001 BTC size, 0.01 BTC inventory limit, 10/10/20 ms
entry/cancel/report delays, alpha=0.5, participation=0.1 and unverified zero fees.
These are DEVELOPMENT diagnostic assumptions, not calibrated production values.

The 100 ms predictive grid is not reused as a strategy decision clock. Existing
sample warmup and the configured strategy schedule remain authoritative.

## Reuse and acceptance

Each window calls the existing `runner.run_sweep`, which calls the existing
replay, scheduler, execution, accounting, diagnostics and immutable artifact
writers. Existing `run_record` and `build_scorecard` perform comparison guards and
ledger reconciliation. No execution core, GLFT or fill-model implementation is
changed. The zero control must reproduce MM-0 summaries, orders, fills, ledger,
execution, risk, timeseries and markouts. Only embedded strategy names and exact
numeric text representations are normalized in that comparison; all economic
values, timing, order identity and other fields remain checked.

Every window uses a separate bounded worker. Results retain complete original
run directories and summaries, including quote availability, markout coverage,
inventory and clock diagnostics. No-fill windows remain in paired output and
are explicitly marked as having insufficient execution evidence.

The primary descriptive endpoint is the MM-1 minus MM-0 net-PnL difference per
reset window. Component deltas cover spread capture, inventory MTM and fees;
secondary deltas include drawdown, inventory and turnover. Per-date and pooled
reports show paired counts, means and signs. Only additive accounting metrics
are summed across independent reset windows. Drawdown, inventory and fill rates
are not added, and no continuous equity curve, annualized Sharpe, liquidation
profit or statistical significance is fabricated. Ending inventory is marked
to market under the existing engine, not automatically liquidated.

```bash
PYTHONPATH=src python scripts/run_study1_baselines.py \
  --config configs/backtest/binance_recorded.yaml \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development-coverage-v3/development_report.json \
  --output /path/to/NEW_STUDY1_DIRECTORY
```

The output must be new. Failures preserve partial artifacts and a FAIL progress
record; a final report appears only after every selected pair and control passes.
Real-data runtime acceptance for this extension must be recorded at its exact
execution commit separately from the previous P0 acceptance.

Read-only revalidation rebuilds the review and paired aggregation from original
run artifacts, rechecks the protocol and P0/sample provenance, validates that run
paths belong to each window, and reports existing decision-reason/quote-path
diagnostics. It does not rerun the simulator or independently recalibrate fills.

```bash
PYTHONPATH=src python scripts/validate_study1_baselines.py \
  --report /path/to/STUDY1_DIRECTORY/study1_report.json \
  --catalogue /path/to/catalogue_manifest.json \
  --p0-report /path/to/development-coverage-v3/development_report.json
```
