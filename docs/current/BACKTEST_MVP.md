# Market-Making Backtest Lab MVP v1

The lab replays one immutable Binance USD-M BTCUSDT sample under three simple
market-making strategies, configurable L2 fill assumptions, entry/cancel
latencies, and signed maker fees. It is a diagnostic research demo, not a
live trading system or evidence of ETF LP profitability.

## Runtime and quick start

Ubuntu is authoritative. The isolated feature checkout is
`/home/0woo/lobcap-mvp`; the collector continues using `/home/0woo/lobcap`.
No collector, systemd, firewall or cloud changes are required.

```bash
cd /home/0woo/lobcap-mvp
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[web]' 'pytest>=8,<9'
.venv/bin/python -m lobcap.backtest prepare --config configs/backtest/binance_demo.yaml
.venv/bin/python -m lobcap.backtest run --config configs/backtest/binance_demo.yaml
.venv/bin/python -m lobcap.backtest sweep --config configs/backtest/binance_demo.yaml --core-matrix
.venv/bin/python -m lobcap.backtest sweep --config configs/backtest/binance_demo.yaml --parameter entry_ms --values 0,10,50,100,250
.venv/bin/python -m streamlit run apps/backtest_lab.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

From Windows, forward the existing SSH connection and open localhost:

```powershell
ssh -N -L 8501:127.0.0.1:8501 0woo@lob-collector-01.asia-northeast1-a.project-2878cd89-af59-4d8a-aef
```

Open `http://127.0.0.1:8501`. Streamlit executes simulations only on explicit
form submission. The UI and CLI call the same runner. A process-wide lock
allows one job; sweeps contain at most 24 serial variants. Changing the UI or
opening another tab does not rerun a backtest.

The native Windows research suite imports Unix-only `resource`; this MVP does
not patch that research module. Local pure-core checks are supplementary.

## Dataset contract and provenance

The shipped configuration freezes 2026-09-06 14:10–14:20 UTC, with 30 seconds
of feature warmup. Tick size 0.10 USDT and lot size 0.001 BTC are validated
against this sample, not asserted as perpetual exchange metadata.

The raw inputs remain under `/data/market=futures/symbol=btcusdt/`. Preparation
uses the snapshot preceding warmup, the overlapping depth packet, subsequent
continuous depth, aggregate trades and acquisition lifecycle records. It
reuses `rebuild.OrderBook` and `binance_protocol` rules. Gaps, corrupt data,
timestamp regression, missing normal trade quantity, crossed books, lifecycle
boundaries, and unaligned independent snapshots block acceptance. Failures are
preserved; retry archives a failed sample directory under a unique suffix.
Successful samples are immutable and checksum-verified before reuse.

The audited window contains 5,881 depth packets and 8,043 aggregate trades,
with two matching independent top-50 snapshots. This is a bounded gate, not
a full-day data-quality certification or Prompt 5E execution.

Preparation emits an aggregate initial book and streaming replay events.
`L2Trade` and `L2LevelUpdate` remain the existing blinded types. A separate
replay index identifies global processing order; source references retain
file/line and exchange sequence lineage. Raw Binance payloads do not reach
the strategy or execution engine. LOBMSG remains a book-building format.

### Exchange-time convention

V1 models zero feed and fill-notification latency. Recorded receipt times are
diagnostics, not the simulation clock. Observed trade receive delays reached
roughly 2.9 seconds in the selected window; order-latency results must not be
presented as measured end-to-end trading latency.

Use transaction timestamp `T` and preserve sequence order within each stream.
Trades precede depth at equal timestamps. Equal-time depth packets are
applied in update-ID order and exposed as one final batch. Neither stream's
sequence numbers are comparable with the other stream's sequence numbers.

For interval `(previous_depth_time, current_depth_time]`, at each side/price:

```
D = max(previous visible quantity - current visible quantity, 0)
V = sum(normal trade volume in this interval)
C = max(D - V, 0)
```

`nq` is normal trade volume; `q` includes RPI volume. Require `0 <= nq <= q`.
`m=true` means maker buy. Each normal trade consumes modeled queue once at
trade time. Only the residual `C` advances queue under alpha when the closing
depth batch arrives. Depletion alone cannot fill. An order activated inside
the interval receives no interval-wide non-trade credit. Record skipped
credit, D/V/C, excess trades over net depletion and excluded RPI quantities.

This is net-depletion attribution, not recovery of individual cancellations.
Replenishment, hidden liquidity and ordering inside batches remain unknown.
Initial queue is the latest published level quantity at activation.

## Deterministic execution

At each market timestamp or scheduled deadline:

1. Apply all market events, evaluate previously active orders and book fills.
2. Apply existing cancellation deadlines, then entry deadlines.
3. Issue order-age cancellations due now.
4. On a decision tick, derive causal features, call the strategy, apply risk
   controls, and reconcile desired quotes.
5. Drain new zero-delay cancellations/submissions, cancellation first.
6. Resolve due metric observations using state available by that time.

Market fills win cancellation ties. Newly activated orders cannot fill on
any event at their activation timestamp. A decision never fills against the
event that caused it. Events before an internal deadline are processed first;
deadlines between events execute at their exact scheduled time.

Each side has at most one submitted/live order. Replacement is serial: cancel
the old order, respect cancel latency, and recompute a replacement at the next
decision tick after termination. Unchanged price/remaining size keeps priority.
Partial fills reduce both remaining quantity and reserved exposure. A cancel
that arrives before entry suppresses entry. A fully filled order makes its
later cancel a recorded no-op. Marketable orders are rejected at activation;
v1 never silently converts a maker quote to a taker order.

At `[start,end)` termination, unresolved orders are censored and ending
inventory is retained. No terminal trades are invented.

## Strategies and risk

The strategy API accepts frozen `StrategyParameters`, `MarketView`, and
`PortfolioView` values. It has no replay iterator, execution queues, fill-model
settings or L3 access. Built-in centers are:

- Fixed spread: midpoint.
- Inventory skew: midpoint minus skew ticks × tick × inventory / limit.
- Microprice: midpoint plus signal strength × (microprice - midpoint).

Microprice is `(ask*bid_size + bid*ask_size)/(bid_size+ask_size)`; imbalance is
`(bid_size-ask_size)/(bid_size+ask_size)`. Quotes are rounded outward, restricted
to at/behind BBO and known snapshot coverage, and sized in integer lots.

Shared risk controls enforce worst-case inventory including pending and
cancel-pending exposure. They can suppress quoting, widen by the configured
half-spread, or halve size using explicit volatility thresholds. Volatility
is the population standard deviation of 30 past one-second log returns in
basis points; incomplete/stale history cannot satisfy an enabled volatility
gate. Stale books, insufficient spread and unknown coverage suppress quotes.
Maximum age requests cancellation rather than instantly removing exposure.

Default size is 0.001 BTC, inventory limit 0.01 BTC, half-spread 0.5 tick,
decision interval 250 ms and maximum age 5 s. These are editable diagnostic
parameters, not optimized settings. Zero fees are also a diagnostic setting.

## Accounting and diagnostics

For maker sign s=+1 buy/-1 sell, quantity v, price p and signed fee rate f:

```
inventory += s*v
cash -= s*p*v + p*v*f
equity = cash + inventory*midpoint
net PnL = equity - initial equity
gross PnL = net PnL + signed fees
spread capture += s*v*(midpoint_at_fill-p)
inventory MTM += inventory_held_during_move * midpoint_change
gross PnL = spread capture + inventory MTM
```

Cash is a synthetic linear-futures trading ledger, not an exchange wallet.
Fees are charged on notional; negative values are rebates. Taker fees are
preserved but inactive. Funding, margin, liquidation, impact and hedging are
excluded. No generic passive-maker slippage is applied.

Fill rate counts activated orders with any observed fill. Partial-only and
full-fill rates use the same activated-order denominator; quantity fill rate
uses activated quantity. Canceled, rejected, pending and censored states are
separately reported. Mean inventory is time-weighted. Drawdown includes every
ledger transition, not only the chart's one-second samples.
Spread capture also reports quantity-weighted USDT per BTC and basis points
(each fill's contribution divided by its own execution price). Replacement
downtime sums both sides from replacement cancellation until next activation,
or until sample end if no replacement activates.

Markout at 100 ms, 1 s and 5 s is `s*(future_mid-fill_price)`. Post-fill drift
is `s*(future_mid-mid_at_fill)`; negative drift indicates adverse selection.
Use the last midpoint at/before the target, at most 250 ms old. Targets at or
beyond sample end are censored. Every horizon reports observations, valid
count, coverage and volume-weighted values. Markout is never added to PnL.

Fixed conditional buckets cover held inventory, spread, decision-time distance
behind the same-side BBO,
imbalance, trailing volatility, UTC time and execution assumptions. Dimensions
overlap and must not be summed together. Inventory MTM is partitioned using
inventory held during the price move; fill evidence retains decision and
pre-fill conditions.

Returns use one-minute equity buckets. Fewer than 60 complete buckets produces
`Unavailable — insufficient sample`; thus all supported 10–30 minute samples
show no Sharpe. There is no annualized tick-PnL statistic.

## Artifacts and reproduction

`artifacts/backtest/` is ignored and separate from accepted research outputs.
Samples retain source and normalized checksums. Each run writes an executable
effective `config.yaml`, resolved settings, the existing manifest structure,
summary, invariants, compressed decisions/order events/fills/queue transitions/
ledger, one-second chart CSV, markout CSV and conditional summaries.

Run manifests preserve Git/dirty state, source configuration hashes, effective
configuration hash, dataset identity, environment versions, exact reproduction
command, timings, process peak RSS and artifact hashes. Decimal values use
strings. Canonical digests exclude run UUIDs and wall-clock metadata. Sample
identity and effective configuration distinguish runs even if outcomes match.
Never present a dirty run as clean evidence.

The exported config can be rerun using the command in its manifest. Sweeps
start with fresh state for every variant and preserve all results rather than
selecting a winner. Compared runs must have the same sample checksum. PnL
sign changes only bracket observed break-even regions.

## Validation

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_backtest_bounded.py \
  --config configs/backtest/binance_demo.yaml \
  --output artifacts/backtest/validation/run-001
.venv/bin/python scripts/audit_backtest_evidence.py \
  --config configs/backtest/binance_demo.yaml \
  --acceptance-report artifacts/backtest/validation/run-001/report.json \
  --output artifacts/backtest/validation/run-001/source_audit.json
```

The full research tests require the existing ignored Coinbase raw slice at
`artifacts/validation/tardis_coinbase/raw_http_slice.txt`. In the feature
worktree it may be a read-only reference to the original checkout's file;
the research outputs themselves are not regenerated or overwritten.

The bounded validator checks the sample, runs the 12 core scenarios twice in
opposite order, checks digests, runs six one-factor sensitivity sets, and
independently reconciles every ledger and fill inventory. It asserts a
60-second prepared-run limit and 512 MiB process peak RSS limit on Linux,
and verifies the collector stays active. Detailed evidence is preserved in
the requested output directory. Failures retain their reports.
The independent source audit checks every fill against normalized public
trade side, timestamp, price and normal quantity; checks activation and
termination bounds, signed fees, and visible end-of-sample censorship.
If an SSH session interrupts validation, run the gate with
`--resume-report <interrupted-report.json>` and a new output directory.
It rechecks completed groups against the same sample and effective settings,
reconciles their artifacts, and reruns incomplete groups. For remote sessions,
redirect worker output to a VM-local log under `nohup` so a disconnected
terminal cannot break the validation process.

Official references: [Binance book synchronization](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly),
[depth fields](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public),
[trade fields](https://developers.binance.info/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market),
[Streamlit forms](https://docs.streamlit.io/develop/concepts/architecture/forms).
