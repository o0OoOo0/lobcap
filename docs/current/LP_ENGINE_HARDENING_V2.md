# LP engine and simulator hardening v2

Status: deployed after Ubuntu acceptance. External HftBacktest accounting remains a documented failed comparison, not a release dependency. Execution contract: `binance-own-orders-v2`. Existing normalized samples are unchanged.

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

Our 26 symmetric fixture probes cover queue depletion, lots, trade-through, current-event volume and cumulative participation. Sixteen external comparisons check order execution separately from reported position. See [validation contract](EXECUTION_CONTRACT_VALIDATION.md).

An actual external partial-fill case reported 0.001 executed in order leaves, while reported position remained zero. The report intentionally retains `ACCOUNTING_DISCREPANCY` and a failing overall external verdict. This is an observed fixture-level incompatibility, not a completed independent PnL validation. Our ledger is not changed to reproduce it. The adapter is an offline diagnostic only and HftBacktest is not adopted as the application engine.

The partial-then-full follow-up also retained a discrepancy: 0.003 executed versus 0.002 reported position, on both sides. All 16 external execution quantities meet the explicitly specified external contracts, including two intentional through-volume differences, but four position checks fail. Full evidence: [LP_HFT_COMPARISON_V2.json](LP_HFT_COMPARISON_V2.json); isolated dependency versions: [requirements](LP_HFT_VALIDATION_REQUIREMENTS.txt).

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

### Files changed

Execution, scheduler, ledger input guards, config provenance, comparison/diagnostic UI, independent contract and evidence auditors, focused tests, and validation documentation. Research L2 queue arithmetic, collectors, raw captures and accepted research outputs were not changed.

### Commands run

Full Ubuntu pytest, compileall, pip check, `validate_lp_engine.py`, workspace smoke validator, isolated HftBacktest comparison, and saved-real-result Streamlit AppTest. Git bundles created a separate clean release checkout. SSH/SCP transferred source and bounded evidence. The existing app process was gracefully replaced only after acceptance; no collector service operation was performed.

### Official docs checked

HftBacktest fill/API/constants/initialization documentation, pinned package release and published processor source; see the linked validation guide and earlier full-site review. The runtime discrepancy is retained independently of source interpretation.

### Tests passed

- Clean release `c7c4ee782b7cea4fe95c252abe6ce42f64eb9596`: **249 passed**, 10 existing Streamlit/NumPy deprecation warnings, 61.07 seconds. VM log `/tmp/lp-release-pytest.log`.
- Compileall and pip check passed.
- [24 frozen-sample runs](LP_CORE24_VALIDATION_V2.json): 3 baseline strategies × 4 fill assumptions, repeated in reverse order. Digests match; public-volume and every ledger identity audit pass. Maximum per-run wall time **38.689 seconds**, process peak RSS **424.781 MiB** including independent audit allocations; output total **13,142,882 bytes**. Preparation was not rebenchmarked. Engine validation preceded final comparison-only changes and added external fixtures; no production execution/accounting code changed afterward.
- [Clean-release workspace smoke](LP_WORKSPACE_VALIDATION_V2.json): four real sample runs, inventory strategy and expression repeated in reverse. Deterministic, source/ledger audits pass; maximum per-run wall 10.475 seconds, peak RSS 413.691 MiB.
- [Saved-result UI](LP_UI_VALIDATION_V2.json): three application pages plus the new v2 result panel; 13 downloads present, no AppTest errors. This is AppTest evidence, not a claim of manual browser testing.
- 26 internal contract probes and all 16 explicitly specified external order-execution quantities behave as specified. This is distinct from external portfolio reconciliation below.

### Tests failed

- External HftBacktest 2.4.4 portfolio reconciliation: **four failures** (buy/sell partial and partial-then-full). Overall external report remains `ACCOUNTING_DISCREPANCY`; independent PnL parity is not approved.
- No final project pytest failures. Intermediate missing fixture/mock issues, Windows test tooling failures and connection problems are recorded in `docs/internal/FAILURES.md`.

### Runtime observations

- [Deployment](LP_DEPLOYMENT_V2.json): app PID 381801 replaced by **409096**, cwd `/home/<vm-user>/lobcap-lp-release-v2`, commit `c7c4ee7`, loopback `127.0.0.1:8501`.
- Staging PID 409034 was stopped after release health passed.
- Collector remained active, **PID 608, NRestarts 0** at final inspection. No collector configuration or service restart.
- Local SSH forwarding restored after a transport reset; local health endpoint returned `ok`. Tunnel is a live session and must remain running. No public endpoint or reboot autostart was added.

### Data written

New validation runs in isolated staging and append-only shared run storage; small evidence JSON in this documentation directory. Existing samples/history are linked into release. Optional HftBacktest dependencies live only in `/tmp/lobcap-hft-validation-v244`, with exact versions preserved. Existing app dependencies were unchanged.

### Open risks

Actual queue, private latency and impact remain unmeasured. HftBacktest accounting mismatch prevents treating it as a verified PnL oracle. Ten-minute reuse remains exploratory. Pending old workspace jobs cannot resume under new code; clone and resubmit. Old completed results stay available, separated by execution version. Source commits are on `feat/research-workspace-v1`; no main merge or force push was performed.

### Recommended next action

Use the deployed v2 engine for new experiments and compare desired-quote duplication and execution evidence. Continue the separate data expansion and then implement causal feature/signal evaluation and empirical latency inputs. Live calibration and paper eligibility are not granted by this release.
