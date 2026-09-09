# Research workspace release evidence — 2026-09-10

Implemented the approved parameter-and-restricted-expression research workspace
on `feat/research-workspace-v1`, based on `fbdb44f`. This extends the existing
backtest engine; it does not certify a profitable strategy or paper eligibility.

## Files changed

- `backtest/workspace/`: immutable experiment resolution, exact Decimal grids,
  restricted expression compiler, draft revisions, durable jobs, worker, comparison and CLI.
- `backtest/workspace_ui/` and `apps/backtest_lab.py`: four research tabs, preview,
  explicit submission, progress/cancel/resume, heatmaps, curves, exact clone,
  persisted comparison conditions and research notes.
- Configuration, strategy and engine adapters: observable-only expression inputs,
  common risk controls, cooperative cancellation and host-wide worker lock.
- Focused backend/UI/independent acceptance tests, bounded validation scripts,
  example designs and operation documentation. Existing research schemas and
  Unix-only resource imports remain intact.

## Commands run

Authoritative runtime: existing GCP Ubuntu VM, Python 3.12, source directory
`/home/0woo/lobcap-workspace-v1`, Python executable
`/home/0woo/lobcap-mvp/.venv/bin/python`, `PYTHONPATH=src`.

```bash
python -m pytest -q
python scripts/validate_backtest_workspace.py --config configs/backtest/binance_recorded.yaml --workspace artifacts/backtest/workspace-final-matrix --output artifacts/backtest/workspace-final-matrix.json --mode matrix --check-collector
python scripts/validate_backtest_workspace_cancel.py --config configs/backtest/binance_recorded.yaml --workspace artifacts/backtest/workspace-trading-cancel --output artifacts/backtest/workspace-trading-cancel.json
```

The cancel script used a fresh `workspace-trading-cancel` directory; its result
is retained in the report below.
SSH/SCP transferred owned source files and small evidence reports. No collector
configuration, raw capture, accepted research artifact or cloud resource was changed.

## Official docs checked

This release retained the established Binance normalization and fill contracts.
No new exchange protocol claim was introduced. Runtime acceptance used the
installed Streamlit implementation and its AppTest API. The approved plan and
existing MVP documentation retain the external protocol references.

## Tests passed

- Final full Ubuntu suite: **191 passed, 10 warnings, 64.25 seconds**.
  Log: `/tmp/workspace-pytest-release.log`. Warnings are Streamlit's NumPy
  timedelta deprecation; no failures or skipped Linux regression substitutions.
- [Actual 20-run matrix](BACKTEST_WORKSPACE_MATRIX_VALIDATION.json): nine
  parameter combinations plus one expression, repeated in reverse order.
  All deterministic digests matched. Independent checks reconciled source-linked
  fills and Decimal ledger identities; no automatic terminal liquidation or
  short-sample Sharpe was introduced.
- [Subprocess cancellation/resume](BACKTEST_WORKSPACE_CANCEL_VALIDATION.json):
  canceled during trading in **0.108 seconds**, preserving **-0.004 BTC** and
  an active order in interrupted evidence. Resumed attempt 2 completed and
  checksums of the already completed run stayed unchanged.
- Real browser: saved/reloaded draft revisions, submitted and completed a
  nine-run expression grid, displayed heatmap and PnL/inventory curves, saved
  comparison conditions, cloned an exact run, changed only maker fee to 1bps,
  completed that run and saved a research note/candidate.
  After switching to port 8501, the saved note and candidate flag were restored
  in a fresh browser session. The saved cost comparison contains both parent
  and clone, fixed strategy identity and two overlay runs.
- Parent `c90a257de032-2c48e0c5` and clone `39e09b39780c-e5892e1c` both
  produced 92 fills and gross PnL -0.6568 USDT. The clone's additional
  0.73322302 USDT fee explains its net PnL -1.39002302 exactly.

The final UI-only changes (advanced-grid mode persistence and result refresh)
were followed by the full 191-test suite and browser clone run. The 20-run
matrix preceded those UI changes; execution/normalization/accounting code did
not change afterward. Code fingerprints intentionally include UI source, so
the matrix and final browser submission have different source fingerprints.

## Tests failed

No final Ubuntu test failed. Intermediate setup/test failures and their fixes
are recorded in `FAILURES.md`. A warmup-only cancellation trial was strengthened
and rerun during the trading window. An earlier validation matrix was stopped
before final resolver guards were installed; only the later complete 20-run
PASS report is claimed above.

## Runtime observations

- Slowest matrix run: **35.40 seconds**, below the 60-second target.
- Peak matrix process RSS: **413.90 MiB**, including in-process independent
  audits. Separate subprocess worker measurement: **39.30 MiB**. Both below
  512 MiB. Sample preparation was not re-benchmarked by this workspace release.
- Existing collector `lobcap@btcusdt.service` remained active through validation
  and app transition.
- Existing app process 350898 was gracefully replaced by the validated app
  process **381801**, loopback **127.0.0.1:8501**, health endpoint returned `ok`.
  Original `/home/0woo/lobcap-mvp` source was preserved for rollback.
- Local access uses SSH forwarding. It is not a public deployment and the
  tunnel must remain running; a lost tunnel caused the earlier connection issue.
- VM source was staged in a separate clone and retains honest dirty/source-hash
  provenance. Local reviewable commits do not pretend those earlier runs were
  produced from a clean release commit.

## Data written

New immutable runs append under the existing backtest runs location. New
workspace metadata lives in `artifacts/backtest/workspace/`; validation uses
separate `workspace-final-matrix` and `workspace-trading-cancel` directories.
Samples and historical research/comparison paths are linked to existing data.
No historical file was replaced. Small validation JSON reports are checked in;
full evidence remains on the VM.

## Open risks

Ten minutes supports workflow exploration, not independent strategy validation.
Candidate notes remain `INSUFFICIENT_EVIDENCE` for paper trading. L2 queue,
market impact, funding and account-specific costs remain the documented
model assumptions/limitations. Exploration does not measure hidden queue truth.
The app remains a loopback research tool, with no authentication or reboot
autostart service added. Git/source checksums reject resuming old unfinished
experiments after any source change; clone and resubmit instead.

## Recommended next action

Open the research workspace, write a hypothesis, preview a small grid, compare
identical execution assumptions and clone a result for the next hypothesis.
Preserve the exploration history before adding independent dates for candidate
selection. See the workspace guide for CLI reproduction.
