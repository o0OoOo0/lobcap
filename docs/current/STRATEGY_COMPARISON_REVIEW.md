# Strategy comparison review — 2026-09-11

Reviewed the comparison suite introduced at `cb82b07` and `3f08c3b` against
the existing execution, accounting, and comparison contracts.

## Corrected findings

- The scorecard bypassed the established comparison guard. Matching sample and
  execution labels alone did not freeze capital, inventory limits, quote start,
  research policy, or clean code provenance. It now uses the shared guard.
- Rankings trusted summary PnL while checking a different ledger's internal
  reconciliation. Summary PnL, attribution, ending inventory, drawdown, and peak
  inventory must now agree with the reviewed ledger. Missing/non-finite metrics
  cannot silently become zero. Unreconciled runs remain visible and unranked.
- Identical outcomes received different ranks according to random run IDs.
  Equal net PnL, drawdown, and peak inventory now share a rank.
- A selected job unrelated to the displayed run could populate its scorecard.
  The group now comes from the displayed run's job membership. Partial jobs
  carry an explicit provisional-result warning.
- Scenario sensitivity was routed through strategy ranking and could abort the
  result panel. Only strategy comparison kinds enter the scorecard; pairwise
  sensitivity remains available independently.
- A selector inside a Streamlit form was disabled according to another form
  field that could not trigger a rerender. Strategy selection is now editable
  and is consumed only on explicit suite submission.
- Charts silently selected the first six runs and gave parameter variants the
  same legend. Users can select up to six runs and legends identify the run.
- Maker strategy suites reject attached hedge policies, preventing a hedging
  no-trade control from being labeled a pure maker control.

## Scope and remaining limitations

This is a bounded strategy comparison interface, not a completed multi-day
signal research platform. Signal IC and the three forward-return labels exist
as offline APIs but are not wired into this strategy scorecard. No automatic
paper eligibility is granted. The default minimum of one filled order is only
an execution-presence check, not a statistical sufficiency criterion. The
existing execution markout's 250 ms tolerance remains distinct from Signal Lab's
explicit horizon-specific freshness policy.

The test runner uses a short, dedicated `--basetemp` on Windows. This avoids
the earlier nested temporary-path failures without changing research modules.
Authoritative Ubuntu and real-sample acceptance was completed on the isolated
`/home/0woo/lobcap-signal-v1` checkout at commit `5da0244`.

## Validation result

- Focused initial review: 60 passed, one unavailable symlink test skipped.
- Complete local backtest suite: 301 passed, one skipped, one failure.
- Clean code commit `e67016b`: 38 strategy-suite/workbench tests passed, including
  actual serial runs and strict scorecard reconciliation. The remaining legacy
  navigation test failed in Streamlit 1.37.1 AppTest while collecting widget
  state after a page transition (`SessionState` missing widget key). This is an
  unresolved local legacy UI validation failure, not an all-green regression.
- Ubuntu focused tests: 59 passed. Full Ubuntu suite: 363 passed with two
  existing Streamlit deprecation warnings; peak memory was 292.8 MiB.
- The exact six-policy UI suite completed on the frozen recorded 10-minute
  sample. Every ledger reconciled and the no-trade control remained unranked.
  GLFT-microprice ranked first in this single development sample at -0.305900
  USDT, so the UI explicitly reports that even the leader lost money and remains
  ineligible for paper trading. See `signal-strategy-suite-validation.json`.
- Streamlit was deployed to VM loopback port 8501 from the isolated checkout.
  The collector remained PID 608, active, with zero restarts. See
  `signal-research-deployment.json`.
