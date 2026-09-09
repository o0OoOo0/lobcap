# Recorded-arrival backtest lab

The current Korean page-based workflow supersedes the older tab layout below:
[strict review and usage guide](BACKTEST_STRICT_REVIEW.md).

Measured results and the implementation report are in
[BACKTEST_REALISM_COMPLETION.md](BACKTEST_REALISM_COMPLETION.md).

This extension separates the exchange's reconstructed state from what a
strategy has received. It also adds delayed private order reports, trade-volume
capacity stress, a cost/flow-aware diagnostic strategy, and explanations of
losses. The fixed 2026-09-06 14:10–14:20 UTC interval is unchanged. Its purpose
is to test assumptions and explain simulated execution, not establish live
profitability from ten minutes of observations.

The original engine and accepted artifacts remain available. See
[v1 semantics](BACKTEST_MVP.md) and [v1 acceptance](BACKTEST_MVP_COMPLETION.md).
The new default UI catalogue is `configs/backtest/binance_recorded.yaml`.

## What the strategy knows

The engine maintains two books and two portfolio views:

| State | Becomes available |
|---|---|
| Exchange book and public executions | Binance transaction time `T` |
| Observed book and public trades | Captured `recv_ns`, in recorded mode |
| Order activation/rejection/cancellation report | Exchange action time + `report_ms` |
| Private fill report and known inventory/cash | Fill time + `report_ms` |

Exchange reconstruction, queue estimates and future deliveries are private
to the engine. The strategy receives immutable observable market and portfolio
views. Its cash and inventory change only when private fill reports arrive.
The accounting ledger records exchange-time fills immediately; this permits
comparison of actual simulated exposure with the exposure the strategy knows.

An outstanding order retains its serial side slot and reserves its remaining
known quantity until a terminal report is delivered. A filled-but-unreported
order cannot be replaced early. Cancellation requests do not remove its risk.
An activation ACK that arrives after a local cancel request does not undo the
known cancel-pending state.

All exchange events at a timestamp precede effective cancels and entries.
Client deliveries/reports then precede decisions. Zero-delay commands and
reports are drained after the decision. A new order cannot consume the trade
that caused its decision, and fills win a tie with effective cancellation.
Maximum order age is a client cancellation: the client can act on activation
only once the ACK is known. The maximum-age deadline is therefore no earlier
than the ACK receipt. This is not an exchange-native order-expiry facility.

Recorded capture time is a proxy for arrival at a strategy running at the
collector's location. Exchange/capture clock alignment is assumed. Negative
capture delay is rejected. Within each public stream, receive-time regression
is held to the previous delivery deadline to preserve its sequence; the added
hold is counted in `clock_diagnostics.sequence_delivery_hold_ns`. Cross-stream
deliveries are merged by their availability time and stable replay index.

Equal-transaction-time depth packets are still coalesced by the normalizer.
Their combined state becomes observable at the **latest** constituent receipt.
The extension does not invent packet-level intermediate states or recover an
unobserved matching-engine ordering. Initial snapshot/overlap availability must
precede feature warmup. Its receipt evidence is bound into the sample checksums.

## Costs, capacity and interpretation

Maker orders execute at their own limit price. The lab does not add arbitrary
maker slippage to that price. Instead, `diagnostics.json` reports signed
execution shortfall against the observable midpoint at the quote decision:

`sum(side_sign × fill_quantity × (fill_price − decision_midpoint))`

Positive shortfall is worse than that benchmark. It is an execution diagnostic,
not an extra deduction from PnL. The loss view separately shows spread capture,
inventory MTM, signed fees and post-fill midpoint drift. Markout is also not
added to PnL. There is no automatic terminal liquidation, funding or taker
execution. Taker fees remain an explicitly inactive preserved setting.

`max_trade_participation` limits own filled quantity per public normal trade.
For a value below one, its cap is rounded down to the instrument's quantity
lot. Full public volume still consumes the modeled queue, but own fills are
capped. This conservative capacity stress can produce zero fills on small
trades; it does not estimate endogenous impact. At one, the established
fractional queue/partial-fill arithmetic is unchanged for research parity.

`impact_stress_bps` is a hypothetical additional cost applied to turnover
notional **in a separate diagnostic result**. It changes neither historical
prices/depth nor the trading ledger. `stress_adjusted_net_pnl` is labeled
separately from `net_pnl`; it is not a calibrated market-impact estimate.
The canonical execution digest therefore stays unchanged when only this
post-run stress changes. Effective configuration and output hashes do change.

The fee graph re-prices the same fills at several costs and rebates. Its
break-even maker fee is `gross_pnl / turnover_notional × 10,000`. This fixed-fill
calculation differs from rerunning a cost-aware strategy with a new fee.
Use a maker-fee sensitivity sweep for the latter.

The default recorded preset uses 10 ms entry/cancel latency, 20 ms private
report delay, 10% participation, zero maker fees and zero impact stress.
These are editable **diagnostic assumptions**, not measured account settings.
The baseline catalogue remains zero-feed/zero-report delay with participation
one. Compare clock modes on the same arrival sample and identical other
settings to isolate the feed-clock effect; switching entire presets changes
several assumptions at once.

## Fourth strategy: Flow guard

The original fixed-spread, inventory-skew and microprice formulas remain.
`flow_guard` uses observed microprice, inventory skew and the following gates:

- A trailing 30-second volatility estimate from fixed one-second observations
  is required. Missing/stale observations suppress quoting during warmup.
- Its half-spread is at least the configured half-spread, and at least
  `mid × (positive_maker_bps + volatility_bps × multiplier) / 10,000`.
- Public normal trades received in the preceding second form signed aggressive
  flow. Trades already more than a second old at delivery are excluded.
- Above the positive flow threshold it suppresses the ask; below the negative
  threshold it suppresses the bid, provided minimum observed volume is met.

It uses no queue/alpha state or future information. Thresholds are exposed in
the form and have not been optimized on the ten-minute sample. The strategy
can trade less, earn less or lose more. A zero-trade result is not evidence of
an economically useful strategy.

## Using the finished views

Configure explicitly submits one scenario. Compare overlays saved runs on
identical sample checksums. **손실 진단** explains PnL components, adverse
selection, decision-price shortfall, fee break-even and hypothetical impact
costs. Its state comparison charts show observed versus exchange midpoint and
known versus actual simulated inventory at one-second resolution.

**주문 타임라인** selects an own order and displays client commands, exchange
actions and delayed reports. It also exposes public trade references, queue
transitions and the exact fills for that order. Queue estimates are audit-only;
they are absent from the strategy's API and private client reports.

Sensitivity changes one variable at a time, including clock, report delay,
participation and hypothetical impact cost. All variants get fresh state and
are shown, including negative or zero-trade outcomes. Each submission is
limited to 24 serial variants. There is no navigation-triggered recomputation.
Audit retains full downloadable evidence and provenance.

Additional artifacts are `diagnostics.json`, `timeline.jsonl.gz` and
`observations.jsonl.gz`. Timeline/observation channels are produced by the
dual-clock engine; the original engine retains its prior canonical output.
End-of-sample unresolved private reports and market deliveries are counted.
Censoring is a simulation boundary, not a fictitious exchange report.

## Ubuntu reproduction

Use the existing isolated checkout `/home/0woo/lobcap-mvp`, not the collector's
checkout. The optional web dependencies and CLI setup are in the v1 guide.

```bash
cd /home/0woo/lobcap-mvp
.venv/bin/python -m pytest -q
.venv/bin/python -m lobcap.backtest prepare --config configs/backtest/binance_recorded.yaml
.venv/bin/python -m lobcap.backtest run --config configs/backtest/binance_recorded.yaml
.venv/bin/python -m lobcap.backtest sweep --config configs/backtest/binance_recorded.yaml --parameter clock_mode --values exchange,recorded
.venv/bin/python -m lobcap.backtest sweep --config configs/backtest/binance_recorded.yaml --parameter report_ms --values 0,20,100
.venv/bin/python scripts/validate_backtest_arrival.py --output artifacts/backtest/validation/arrival-new
.venv/bin/python -m streamlit run apps/backtest_lab.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

The validation output directory must be new. The validator runs a 16-scenario
matrix, repeats it in reverse order, then runs three three-value sensitivities:
41 runs plus one exact v1 digest regression. It verifies source-linked fills,
ledger identities, client-visible inventory/reservations, artifact hashes,
repeat determinism and resource targets. It checks the collector remains active.
UI form parity has focused tests; real-data UI/CLI parity is recorded in the
completion evidence.

Windows access uses the existing loopback SSH tunnel:

```powershell
ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -L 127.0.0.1:8501:127.0.0.1:8501 0woo@lob-collector-01.asia-northeast1-a.project-2878cd89-af59-4d8a-aef
```

Open <http://127.0.0.1:8501/> while the SSH process is running. The workstation's
local reconnect helper restores this tunnel on transient disconnects; the app
remains bound to VM loopback. No firewall, systemd or infrastructure changes
are needed. Native Windows full research collection still requires Unix
`resource`; Ubuntu remains authoritative.

## Accuracy boundary

Source consistency, causal delivery, deterministic output and exact accounting
are testable with these captures. The exact fills of a real own order are not:
L2 data does not expose queue priority/hidden order history, and our hypothetical
orders do not change the recorded market. Own-order acknowledgments, private
fills and account-route latency observations would be needed to calibrate that
part of the model. More independent periods are needed to assess strategy
robustness. This extension makes those boundaries explicit rather than turning
a negative sample into a profitability claim.

Primary references: [HftBacktest replay/queue limitations](https://hftbacktest.readthedocs.io/en/latest/order_fill.html),
[Streamlit form submission semantics](https://docs.streamlit.io/develop/concepts/architecture/forms),
and the Binance source specifications linked in the v1 guide.
