# Backtest MVP completion — 2026-09-09

The Binance BTCUSDT MVP is implemented on `feat/backtest-mvp-v1` in the
Windows checkout and the isolated Ubuntu worktree. The loopback Streamlit
application is available through the existing SSH tunnel at
<http://127.0.0.1:8501/>. The branch has not been pushed to GitHub.

## Files changed

Added the UI-independent `src/lobcap/backtest/` package, strict backtest
configuration, frozen demo config, Streamlit app, focused tests, bounded
validator and independent source audit. Modified shared fill/config helpers,
packaging, ignore rules and documentation. The eight implementation stages
were committed separately, followed by validation fixes. Collector code,
systemd, legacy code, research configurations and accepted research artifacts
were not changed.

## Commands run

Installed the optional web dependencies in the isolated Ubuntu `.venv`;
ran `.venv/bin/python -m pytest -q -p no:cacheprovider`; prepared the frozen
sample; ran `scripts/validate_backtest_bounded.py` and
`scripts/audit_backtest_evidence.py`; repeated preparation in a new generated
directory; submitted the default UI form and reran its exported config using
`python -m lobcap.backtest run`. Checked Git status, output hashes and collector
status. The operational commands are in [BACKTEST_MVP.md](BACKTEST_MVP.md).

## Official docs checked

- [Binance snapshot overlap and pu continuity](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly).
- [Binance normal trade quantity and maker side](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market).
- [Streamlit explicit form submission](https://docs.streamlit.io/develop/concepts/architecture/forms).

## Tests passed

- **102 Linux tests passed in 47.57 seconds**, including the existing 60
  research tests and 42 MVP tests. Existing research outputs retain their
  tested fill semantics; no full OOS research run was invoked.
- **51 bounded runs accepted:** the 12 core scenarios twice, plus 27 variants
  across six one-variable sweeps. Both core matrices have identical digests
  independent of requested order. Every ledger and final cash/inventory
  identity reconciled.
- **14,103 fills independently source-checked**, including artifact hashes,
  maker side, timestamp, execution price, normal trade volume, lifecycle
  timing and signed fees. No historical trade volume was reused within a run.
- **141 sample-end-censored markouts** remain visible in the accepted runs.
  The supported sample has no Sharpe estimate and no terminal liquidation.
- Fresh raw-source preparation at `e3472dd` produced byte-identical initial
  book, replay and validation artifacts. This verifies the final bootstrap
  regression guard without changing the frozen sample or replay semantics.
- Actual UI run `5981471d2716-898cc4d0` and CLI rerun
  `5981471d2716-f5ce92e6` have the same effective config, sample and digest:
  `f723f938e9ba732896202fba7d1948d9b9da9f9f02aa94c235c6dc52fd21396d`.
- The UI sensitivity form completed a serial alpha `0, 0.5` sweep, displayed
  its chart and table, and exposed the comparison download. Its fresh run IDs
  are `8e63410d8350-02271f93` and `5981471d2716-5b58dbb5`.

## Tests failed

No unresolved final regression or acceptance failures. Development failures
and interrupted attempts are recorded in [FAILURES.md](../../FAILURES.md).
The first attempt exceeded the speed target; profiling and replay changes
fixed it. An SSH disconnect interrupted another attempt with BrokenPipeError.
The final resumed gate revalidated complete groups and reran incomplete ones;
it did not count an incomplete matrix as passed.

## Runtime observations

The authoritative VM remained the existing Ubuntu host. Across all 51
accepted runs, maximum prepared-run wall time was **33.274 seconds** and
maximum worker peak RSS was **36.918 MiB**, below the 60-second/512-MiB targets.
Accepted run outputs totaled **18,822,868 bytes**. Fresh preparation was
measured separately at **109.222 seconds**. The actual UI run took **8.577
seconds**, with application-process peak RSS **185.555 MiB**.

The frozen trading interval still contains 5,881 depth packets and 8,043
aggregate trades, no detected continuity gaps/crossed books, and two matching
independent snapshots. The collector was active before and after validation.

## Data written

Generated data stays under `artifacts/backtest/`. The authoritative accepted
report is `validation/acceptance-004/report.json`, with `source_audit.json`,
`final_preparation.json` and `ui_cli_parity.json` alongside it. Local copies
are available in the Windows checkout. A compact checked-in evidence summary
is [BACKTEST_MVP_VALIDATION.json](BACKTEST_MVP_VALIDATION.json).

The final report transparently references completed groups from attempt 003
at `9228d95` and the resumed groups at `e3472dd`. The intervening code changes
added a malformed-input guard and audit/resume tooling; execution and
accounting outputs are unchanged. Original failed/interrupted reports remain
preserved. No raw source file or accepted research artifact was overwritten.

## Open risks

The documented v1 assumptions remain: inferred depth/trade alignment,
aggregate queue estimates, zero feed/notification latency, maker-only fills,
and excluded funding, impact, margin/liquidation and hidden liquidity. These
bounded diagnostics do not establish live profitability. Windows research
collection still has its pre-existing Unix `resource` dependency; Ubuntu is
authoritative.

## Recommended next action

Open the lab, inspect the saved comparisons and source-linked Audit view,
and review the feature branch using the reproduction guide. The application
is bound to loopback and uses SSH forwarding; no infrastructure change is
needed.
