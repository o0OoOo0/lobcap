# Recorded-arrival extension completion — 2026-09-09

The strengthened lab is implemented on `feat/backtest-mvp-v1` and runs in the
existing isolated Ubuntu checkout. The collector still runs from its original
checkout. Open <http://127.0.0.1:8501/> through the loopback SSH tunnel.

Operation and exact assumptions are in [BACKTEST_REALISM.md](BACKTEST_REALISM.md).
The compact measured evidence is [BACKTEST_REALISM_VALIDATION.json](BACKTEST_REALISM_VALIDATION.json).
The branch has not been pushed to GitHub.

## Files changed

Added `backtest/arrival.py`, `backtest/diagnostics.py`, the recorded sample
configuration, focused arrival tests and the bounded arrival validator.
Extended configuration, replay bootstrap evidence, shared execution capacity
limits, strategy features, artifact channels, runner and Streamlit views.
Added documentation and recorded development failures. Research fill arithmetic,
the original engine's canonical output, raw capture, collector services,
legacy code and accepted research artifacts remain intact.

The interface now includes loss diagnosis, per-order timelines, observed versus
exchange state charts, a fourth flow/cost-aware strategy, report-delay and
capacity sensitivity, and separate hypothetical impact-cost results. New runs
are selected for diagnosis, with filled orders first in the timeline selector.

## Commands run

- Full Ubuntu `.venv/bin/python -m pytest -q`: **120 passed in 58.00 seconds**.
- `scripts/validate_backtest_arrival.py --output artifacts/backtest/validation/arrival-001`:
  16 configurations, reverse-order repeat and nine sensitivity variants.
- Original default configuration rerun with exact canonical digest comparison.
- Real browser default form submission, exported configuration rerun through
  `python -m lobcap.backtest run`, and client/ledger/source verification.
- Real browser `report_ms=0,20` sensitivity submission: **Completed 2/2**,
  saved comparison table and chart displayed after rerender.
- Final focused Streamlit tests after result-selection changes: **2 passed in
  5.96 seconds** on `92c5c89`; app health,
  Git status, checksums and collector status checks.

## Official docs checked

[HftBacktest's fixed-replay and queue assumptions](https://hftbacktest.readthedocs.io/en/latest/order_fill.html)
and [Streamlit forms](https://docs.streamlit.io/develop/concepts/architecture/forms).
Existing Binance normalization sources and semantics are retained in the v1
documentation; no new exchange-order API was introduced.

## Tests passed

The full regression suite includes the existing research tests. New cases cover
delayed observable books, fill reports and reservations, serial cancellation
ties, maximum age after ACK, receipt ordering, causal trade-flow expiry,
participation caps, repeat determinism, strict configuration, bootstrap receipt
integrity, explicit cost stress and UI/CLI form parity.

All **41 matrix/sensitivity runs passed**. Reverse-order repeats produced
identical digests for matching configurations. An independent audit checked
**11,175 fills** against public source trades and artifact hashes, including
side, price, time, quantity availability, activation boundary and modeled fees.
All ledger identities and client-visible inventory/reservations reconciled.
There were **100 sample-end-censored markout rows** across the matrix.

The original default digest remains:

`f723f938e9ba732896202fba7d1948d9b9da9f9f02aa94c235c6dc52fd21396d`

The real browser default run and exported-config CLI run both produced:

`fe87ef329ae21dca8ea8d2efff655db5a07877b28e7d333aa7b3cf556389ef2a`

The matrix was executed on clean commit `9324df5`; final full tests and
real-data UI/CLI parity were executed on `d47d333`. Subsequent changes affect
result selection and documentation, not the execution engine. Per-run manifests
retain their exact commits rather than being rewritten to claim later provenance.

## Tests failed

No unresolved test or acceptance failures. Development fixture corrections and
the stale Streamlit module reload incident are recorded in [docs/internal/FAILURES.md](../../docs/internal/FAILURES.md).
Native Windows full research collection still encounters Unix-only `resource`;
no unrelated portability patch was made. Ubuntu is authoritative.

## Runtime observations

| Measurement | Result |
|---|---:|
| New sample preparation | 39.134 seconds |
| Maximum matrix run wall time | 40.706 seconds |
| Matrix process peak RSS | 43.320 MiB |
| Matrix total CPU time | 897.205 seconds |
| Matrix output bytes, as recorded in run manifests | 19,333,660 |
| Default browser run | 10.529 seconds / 190.809 MiB process peak |
| Matching CLI run | 10.216 seconds / 38.617 MiB process peak |

The below-60-second and below-512-MiB targets were met. Preparation is measured
separately. Backtests were run serially; no sweep worker concurrency was added.
The collector remained active. Only the lab's Streamlit process was restarted
to reload Python modules. No systemd, firewall or cloud configuration changed.

The prepared arrival sample retains the same raw input paths/checksums as v1:
5,881 depth packets and 8,043 aggregate trades in the trading window, two
independent matching top-50 snapshots, no gaps or crossed books. Maximum
recorded delays remain 818.131 ms for depth and 2,902.617 ms for trades.
No receive-sequence holds were needed in this sample.

The default recorded scenario is still negative: **−0.519900 USDT**, comprising
**−0.020200** spread capture, **−0.499700** inventory MTM and **zero** modeled
fees. Ending inventory is −0.0040 BTC. Maximum filled-but-unreported inventory
was 0.001 BTC. This is an explained outcome, not a result changed to look profitable.

All 16 core outcomes are in the compact evidence. Ten are negative; six are
positive under zero modeled maker fees. The recorded flow-guard results are
only +0.0303/+0.0307 USDT for the two fill assumptions. These small single-window
results are not an out-of-sample or live-profitability validation.

## Data written

New normalized sample:

`artifacts/backtest/samples/binance_btcusdt_20260906_1410_1420_arrival/`

Its fingerprint is `43fb3fb4cfe6ca9e1ef28b1540146117c67f46381460469fce627ad27427366f`.
New immutable runs/comparisons include full decisions, orders, fills, ledger,
queue transitions, delayed-report timelines and diagnostics. Acceptance evidence
is under `artifacts/backtest/validation/arrival-001/`, including `report.json`,
`source_audit.json` and `ui_cli.json`. Compact summaries are committed; payloads
stay ignored. Original v1 sample/run artifacts were not overwritten.

## Open risks

Captured arrival timestamps proxy one collector's network location. Private
latencies, participation and flow thresholds are assumptions. L2 cannot reveal
actual own-order queue priority; exact fill realism remains uncalibrated.
Historical market paths do not respond to our orders. Impact is therefore an
explicit additional cost stress, not endogenous market simulation. Funding,
hedging, margin liquidation and terminal liquidation are absent. Ten minutes
cannot establish strategy robustness, and short-sample Sharpe remains unavailable.

## Recommended next action

Use the current lab's loss and order evidence views to compare saved scenarios.
Any later strategy evaluation should freeze settings before testing independent
periods. Calibrating live execution would require separate own-order and latency
observations; it is not a prerequisite for reproducing the completed bounded lab.
