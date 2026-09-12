# LOB Market-Making Simulation — Current Plan v4

Updated: 2026-09-12 (research branch checkpoint; older infrastructure observations retain their dates)

## Research branch checkpoint — feat/signal-research-v1

Signal Research P0-A now has accepted multi-day DEVELOPMENT evidence; see
[P0 acceptance](SIGNAL_RESEARCH_P0_ACCEPTANCE.md). Study 1 MM-0/MM-1 baseline
orchestration and paired multi-day summaries reuse the existing simulator and
passed 24 executions over eight windows plus 434 Ubuntu regressions; see
[Study 1 acceptance](STUDY1_BASELINES_ACCEPTANCE.md). The default inventory skew
did not establish net-PnL superiority. The research-only BP extension now reuses
the existing expression policy and has completed 32 executions with causal
input and zero-control checks, plus 441 Ubuntu regressions; standalone
artifact and causal revalidation also passed. See [BP evidence](STUDY1_BP_ACCEPTANCE.md). BP +1 reduced
aggregate reset-window net PnL by 0.326550 USDT versus MM-1 under the frozen
zero-fee scenario. BP remains unpromoted; VPIN causal feature construction and
paired execution comparison are next. Validation/OOS remain locked, and Study 2
multi-order execution is still future work.

## 1. Research identity

The project asks:

> How much can a market-making backtest lie because of fill assumptions, queue position, latency, fees, and data/replay errors?

The evaluation system is itself an object of study.

Main path:
1. collect continuous Binance USDⓈ-M L2 + trades;
2. build an auditable replay/reconstruction pipeline;
3. parameterize L2 fill assumptions;
4. use actual L3/MBO data to generate queue/fill ground truth;
5. measure false fills and missed fills;
6. calibrate an improved L2 queue model;
7. stress queue × latency × fee assumptions;
8. add inventory-aware MM and fair-value/hedge experiments;
9. keep FPGA as a parallel book-builder/feed-handler track.

## 2. Source-of-truth hierarchy

When information conflicts:

1. runtime evidence from the actual GCP Tokyo VM;
2. current official exchange/vendor documentation;
3. `docs/current/`;
4. source code + tests in `src/`;
5. `docs/legacy/` and `legacy_code/`;
6. memory/assumptions.

Never silently copy a legacy API endpoint, sequence rule, update rate, free-access claim, or cloud assumption.

Validation labels:
- `DOC_VERIFIED`
- `RUNTIME_VERIFIED`
- `FAILED`
- `PENDING`
- `NOT_CRITICAL`

## 3. Current infrastructure

Actual environment:
- GCP Tokyo
- VM `lob-collector-01`
- zone `asia-northeast1-a`
- Ubuntu 24.04
- user `0woo`
- project `/home/0woo/lobcap`
- raw data `/data`

The existing VM may be billable. Do not create new cloud resources or modify billing/configuration without explicit approval.

Oracle docs are historical only.

## 4. Data-source roles

### Binance USDⓈ-M Futures — primary continuous dataset

First symbol: BTCUSDT.

Collect:
- diff depth
- aggTrade
- REST depth snapshots
- collector lifecycle/event log

Current documented WebSocket routing:
- depth: Public routed endpoint
- aggTrade: Market routed endpoint

Use current official documented `@100ms` depth for the first runtime test.

The collector captures; it does not rebuild the book.

### Tardis Coinbase raw historical L3 — primary Phase 0.5 ground-truth candidate

Purpose:
- reconstruct order-level FIFO queues;
- place counterfactual virtual orders;
- generate fill/no-fill and time-to-fill labels;
- downsample the same stream to L2 and test L2 fill assumptions.

Free access must be runtime-tested before adoption.

### Bitfinex R0 — optional continuous order-level auxiliary

Useful for:
- individual order IDs;
- queue/lifetime/cancellation structure;
- robustness across venues.

Do not call removals exact fill-vs-cancel truth unless additional evidence supports it.

### Bitstamp — optional order-level auxiliary

Current official docs expose:
- order-book `group=2` with order IDs;
- public historical order event gap recovery endpoint.

A continuous public order-event route must be independently verified before relying on it.

### Excluded from critical path

- Kraken: actual Korean-resident onboarding/KYC failed.
- Coinbase direct Exchange L3: do not block the project on institutional Exchange onboarding.
- private trading APIs: not needed for Phase 0/0.5.

## 5. Execution gates

### Gate A — current environment + code audit
- inspect VM
- run unit tests
- verify schema expectations
- no long-running collector

### Gate B — runtime data-source validation
Priority:
1. Binance REST and WS from Tokyo
2. Tardis first-of-month raw Coinbase L3
3. Bitfinex R0
4. Bitstamp endpoints

### Gate C — Binance 10-minute foreground test
Pass:
- depth > 0
- aggTrade > 0
- snapshot > 0
- lifecycle log exists
- gzip files readable
- no unhandled exception
- schema correct
- no silent queue overflow

### Gate D — 1-hour stability
Measure:
- message rate
- CPU/RAM
- disk rate
- reconnects
- gaps
- compressed bytes/hour

### Gate E — systemd + reboot test
Only after C/D pass.

### Gate F — one full UTC day
Use the first full UTC day, not the partial deployment date, for the 23h+ daily quality gate.

### Gate G — expand symbols
Only after BTC passes:
- ETHUSDT
- SOLUSDT

## 6. Phase 0 completion

Required:
- one full BTCUSDT day passes data-quality checks;
- futures `pu` continuity is used correctly;
- process/connect/disconnect/reconnect events are persistent;
- offline book reconstruction works;
- independent periodic snapshots produce successful shadow-book comparisons;
- unexplained crossed books = 0;
- LOBMSG v1 is self-contained for L2 book reconstruction;
- actual bytes/day measured;
- healthcheck + restart path documented;
- failures/exclusions are recorded.

## 7. Phase 0.5 completion

Required:
- at least one L3 source is runtime-verified;
- L3 snapshot + lifecycle stream reconstructs a FIFO book;
- sequence/gap behavior is validated;
- virtual-order partial fill logic is implemented;
- L3 truth labels are generated;
- same events are downsampled to L2;
- pessimistic / calibrated-neutral / optimistic fill models are defined mathematically;
- confusion matrix + false-fill/missed-fill rates are reported;
- venue transfer to Binance is labeled explicitly as a transfer assumption.
