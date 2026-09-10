# LP engine and simulator hardening v2

Status: implementation complete; Ubuntu acceptance evidence is recorded in the completion section after validation. Execution contract: `binance-own-orders-v2`. Existing normalized samples are unchanged.

## Team and scope

The user requested LP-style agents to implement the engine and simulator improvements. Three bounded roles worked on the shared feature branch:

| Role | Responsibility | Independent challenge |
|---|---|---|
| Execution research | Own-order lots, trade-through, causal participation budget | Volume bounds and lifecycle fixtures |
| Simulator and portfolio control | Observable clock parity, cancellation ordering, ledger validation | Cross-clock parity and end-boundary regressions |
| Model validation | Separately specified fixtures and pinned HftBacktest adapter | Order evidence and accounting checked separately |
| Integration | Provenance, source-based artifact audit, diagnostics/UI, Ubuntu acceptance | Regression and frozen-sample repeatability |

This release corrects demonstrated execution/scheduling issues and makes strategy-path redundancy observable. It does not implement new data collection, dynamic private-latency calibration, multi-order grids, multi-market factors, or a complete signal-prediction research platform. No public replay can establish actual own queue rank or endogenous impact.

## Execution contract

- Own fill quantity must be an integer multiple of instrument lot in every backtest path. The research `advance_queue` arithmetic remains unchanged; venue execution enforces own quantities afterward.
- Same-price trades consume modeled ahead quantity before own fills.
- A strict trade-through clears the inferred ahead queue. Our own fill remains bounded by the current trade's normal volume (`nq`), remaining own quantity, cumulative participation capacity and lot size. BBO movement alone does not create fills.
- Participation is an order-lifetime budget: `eligible_trade_volume * participation - already_filled`. Only side/price-eligible trades strictly after activation count. Fractional capacity carries forward, but cannot fill against a depth-only event and cannot transfer to a replacement.
- Packet boundaries can still affect timing and sub-lot queue-boundary opportunities. This removes repeated participation-cap rounding loss; it does not claim packet-invariant historical matching.
- Market events still win cancellation ties. Maximum-age requests precede decisions, while newly created zero-delay cancellation effects drain after the decision. A replacement waits until a later decision after termination.

The full-fill strict-through convention in HftBacktest differs intentionally from our observed-volume bound. This choice is explicit, not a claim that either replay recovers missing exchange chronology.

## Simulator and accounting

The baseline exchange scheduler now populates causal trade-flow observations like the zero-latency arrival scheduler. Previously selecting the scheduler could change the market view even with identical observations and delays.

Maximum-age zero-delay cancellations previously allowed earlier replacement in the baseline scheduler. The predecision pass now defers those newly created cancellations, followed by a postdecision drain at every internal timestamp.

Invalid fill side, nonfinite/nonpositive price or quantity fail before ledger mutation. Partial-fill cash, inventory, fees, markouts, exclusive sample-end treatment and delayed private report boundaries have focused regressions.

## Audit and user-visible evidence

`execution.jsonl.gz` now includes before/after capacity, cumulative eligible volume, queue bypass and separate cap/lot reductions, with source references. An independent auditor reconstructs capacity from normalized public trades rather than trusting these diagnostic fields. It retains the old per-event rule for old manifests.

Resolved configurations include the execution contract version, changing effective identities. Workspace comparisons reject mixed engine versions; historical results remain accessible. Cloning an old configuration creates a new run with current semantics, not a rerun of legacy behavior.

Diagnostics include a deterministic desired-quote path hash based on time, bid/ask and quantity. Strategy names/reasons are excluded. Identical quote paths under identical sample/scenario conditions are identified in the UI. This measures effective output duplication, not independent strategy quality or signal IC. The at-BBO statistic is explicitly not a measured clamp rate.

The UI exposes execution rules and downloadable execution/diagnostic artifacts. It continues to label ten-minute candidate evidence insufficient.

## Independent HftBacktest validation

The optional adapter pins `hftbacktest==2.4.4` in a separate virtual environment, never the application environment. Fixture inputs and hashes are persisted. The public trade maker side is converted to the opposite aggressor side for HftBacktest.

Our 24 symmetric fixture probes cover queue depletion, lots, trade-through, current-event volume and cumulative participation. Fourteen external comparisons check order execution separately from reported position. See [validation contract](EXECUTION_CONTRACT_VALIDATION.md).

An actual external partial-fill case reported 0.001 executed in order leaves, while reported position remained zero. The report intentionally retains `ACCOUNTING_DISCREPANCY` and a failing overall external verdict. This is an observed fixture-level incompatibility, not a completed independent PnL validation. Our ledger is not changed to reproduce it. The adapter is an offline diagnostic only and HftBacktest is not adopted as the application engine.

## Reproduction

Use Ubuntu and the existing prepared sample. Do not change `l2.multidate` for Windows portability.

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/validate_lp_engine.py --output artifacts/lp-core24.json
PYTHONPATH=src python scripts/validate_backtest_workspace.py --config configs/backtest/binance_recorded.yaml --workspace artifacts/backtest/lp-workspace-smoke --output artifacts/lp-workspace-smoke.json --mode smoke --check-collector
python scripts/validate_execution_contracts.py --hftbacktest --output artifacts/lp-hft-comparison.json
```

The final command requires the separate pinned environment and intentionally exits nonzero for the observed external accounting discrepancy. It must not be reported as parity PASS. Fresh output paths are required for bounded validators. Existing captures and accepted results are not overwritten.

## Completion evidence

Acceptance results and service status are appended after the final checks. Scope remains research simulation; live calibration and paper eligibility are not granted by this release.
