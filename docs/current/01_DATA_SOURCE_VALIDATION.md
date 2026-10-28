# Data Source Validation Ledger

Updated: 2026-09-06

A documentation claim is not operational until runtime-verified from the actual GCP Tokyo VM.

## A. Binance USDⓈ-M

### Official-document facts to use as current baseline

Official developer pages:
- Connect:
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- Important routing change:
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Important-WebSocket-Change-Notice
- Local order book:
  https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly

Documented baseline:
- base: `wss://fstream.binance.com`
- depth is routed to `/public`
- aggTrade is routed to `/market`
- diff depth supports `@100ms` and `@500ms`
- legacy unrouted Market streams should not be relied on
- one connection is valid for 24h
- server ping/pong behavior must be supported
- first snapshot overlap: `U <= lastUpdateId <= u`
- subsequent Futures continuity: `pu == previous u`
- depth quantities are absolute quantities, zero means delete

### Test A1 — Tokyo REST reachability

```bash
curl -sS -o /tmp/binance_ping.out -w '%{http_code}\n' \
  'https://fapi.binance.com/fapi/v1/ping'

curl -sS \
  'https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=5'
```

Record:
- UTC time
- HTTP status
- whether 451/403/other regional restriction occurs
- response fields

Runtime evidence (GCP Tokyo VM `<collector-vm>`):

```text
TEST: A1 — Tokyo REST reachability
STATUS: RUNTIME_VERIFIED
UTC_TIME: 2026-09-06T13:38:35.761213Z to 2026-09-06T13:38:35.800328Z
HOST: <collector-vm> (<gcp-zone>)
COMMAND_OR_SCRIPT: .venv/bin/python scripts/validate_binance_runtime.py
OFFICIAL_DOC_CLAIM: public USDⓈ-M REST ping and depth snapshot endpoints
RUNTIME_RESULT: ping HTTP 200 with {}; depth HTTP 200 with lastUpdateId, E, T, bids, asks; bid/ask levels are two-element arrays; neither response was 451 or 403
SAMPLE_PATH: artifacts/validation/binance_runtime_report.json
ERROR_IF_ANY: none
DECISION: Tokyo regional access passed for these two REST endpoints
```

Status: `RUNTIME_VERIFIED`

### Test A2 — Public depth WebSocket

Candidate:
`wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms`

Runtime requirements:
- >=100 depth events
- fields include `U`, `u`, `pu`
- save five raw payloads

Runtime evidence:

```text
TEST: A2 — Public depth WebSocket
STATUS: RUNTIME_VERIFIED
UTC_TIME: connected 2026-09-06T13:38:35.932021Z; completed 2026-09-06T13:40:16.302517Z
HOST: <collector-vm> (<gcp-zone>)
COMMAND_OR_SCRIPT: .venv/bin/python scripts/validate_binance_runtime.py
OFFICIAL_DOC_CLAIM: diff depth is routed through /public; @100ms is supported; Futures continuity is current.pu == previous.u
RUNTIME_RESULT: /public connected with websockets 16.1.1; received 932 depthUpdate payloads; all contained e,E,T,s,U,u,pu,b,a; observed extra ps,st fields were preserved; 931 consecutive pairs, 0 pu mismatches; one server PING and the matching automatic client PONG were observed; no unexpected close/error
SAMPLE_PATH: artifacts/validation/binance_depth_samples.jsonl (5 raw payloads); artifacts/validation/binance_runtime_report.json
ERROR_IF_ANY: none
DECISION: routed Public depth candidate passed
```

Status: `RUNTIME_VERIFIED`

### Test A3 — Market aggTrade WebSocket

Candidate:
`wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade`

Runtime requirements:
- >=10 aggTrade events
- preserve full raw payload, including IDs/range fields if present
- save five raw payloads

Runtime evidence:

```text
TEST: A3 — Market aggTrade WebSocket
STATUS: RUNTIME_VERIFIED
UTC_TIME: connected 2026-09-06T13:38:35.936744Z; completed 2026-09-06T13:39:18.688973Z
HOST: <collector-vm> (<gcp-zone>)
COMMAND_OR_SCRIPT: .venv/bin/python scripts/validate_binance_runtime.py
OFFICIAL_DOC_CLAIM: aggTrade is routed through /market
RUNTIME_RESULT: /market connected with websockets 16.1.1; received 150 aggTrade payloads; all contained e,E,a,s,p,q,f,l,T,m; observed extra nq,st fields were preserved; one server PING and the matching automatic client PONG were observed; no unexpected close/error
SAMPLE_PATH: artifacts/validation/binance_aggtrade_samples.jsonl (5 raw payloads); artifacts/validation/binance_runtime_report.json
ERROR_IF_ANY: none
DECISION: routed Market aggTrade candidate passed; full raw aggregate/first/last trade IDs, price, quantity, timestamps, and maker flag can be retained
```

Status: `RUNTIME_VERIFIED`

### A4 — Connection behavior and clock context

- `websockets 16.1.1` was the installed runtime version.
- Both routed connections received a real server PING control frame and emitted
  an automatic PONG with the same payload.
- No unexpected close or error occurred during the bounded validation.
- The documented 24-hour forced disconnect is `DOC_VERIFIED, NOT_RUNTIME_TESTED`.
- At validation start, `timedatectl` reported synchronized clock and active NTP.
  `chronyc tracking` reported `Leap status: Normal` and the system clock about
  0.000003727 seconds fast of NTP time.
- Receive-minus-exchange timestamp values in the report are diagnostic only and
  are not claimed as precise network-latency measurements.

## B. Tardis Coinbase raw L3

Official docs to verify against:
- https://docs.tardis.dev/api/http
- https://docs.tardis.dev/historical-data-details/coinbase

Official documentation verified on 2026-09-06:

- raw HTTP endpoint: `GET /v1/data-feeds/:exchange`;
- API key is optional for historical feeds on the first day of each month;
- exchange identifier: `coinbase`;
- raw messages retain Coinbase exchange-native format and add a Tardis local
  receive timestamp as the line prefix;
- available Coinbase channels include `received`, `open`, `done`, `match`,
  `change`, and `full_snapshot`;
- `full_snapshot` is generated by Tardis from Coinbase REST L3 because the
  Coinbase full WebSocket feed has no initial snapshot. It is marked
  `"type":"full_snapshot","generated":true` and carries a sequence used to
  overlap it with the captured lifecycle stream.

Runtime test on the Tokyo VM:

```text
TEST: unauthenticated Tardis Coinbase exchange-native raw L3, one-minute slice
STATUS: RUNTIME_VERIFIED
UTC_TIME: 2026-09-06T16:28:12Z
HOST: <collector-vm>
COMMAND_OR_SCRIPT: raw HTTP API plus scripts/analyze_tardis_coinbase_raw.py
OFFICIAL_DOC_CLAIM: first day of each month is available without an API key
RUNTIME_RESULT: HTTP 200 for coinbase BTC-USD, 2026-09-01 00:00:00Z through 00:01:00Z; no Authorization header; 43,696 exchange-native messages; 4,416,936 compressed response bytes and 17,910,927 decompressed bytes
SAMPLE_PATH: artifacts/validation/tardis_coinbase/raw_sample.jsonl
ERROR_IF_ANY: none
DECISION: FREE RAW L3 ACCESS RUNTIME_VERIFIED
```

Observed message counts:

- `full_snapshot`: 1
- `received`: 13,898
- `open`: 13,673
- `match`: 310
- `done`: 13,870 (`canceled`: 13,560; `filled`: 310)
- `change`: 1,944

The generated snapshot contained 63,945 bid orders and 46,001 ask orders.
Every entry was `[price, size, order_id]`. Its sequence was `135356475328`.
It appeared in capture order after the lifecycle stream had already advanced
to `135356476857`, so 1,529 earlier-buffered messages with larger sequence
numbers must be applied after initializing from the snapshot. The next message
was sequence `135356476858`.

Across all 43,695 lifecycle messages, sequence advanced by exactly one for all
43,694 comparable pairs: zero gaps, zero duplicates, and zero reversals. The
generated snapshot is an out-of-band reconstruction anchor and is not treated
as another lifecycle increment.

Actual order relationships included 13,673 `received -> open` IDs, 12,749
`open -> done(canceled)` IDs, and 113 maker IDs with `match -> done(filled)`.
Fifty maker orders had multiple match messages (maximum six), demonstrating
that partial fills can be tracked by maker ID and match size. All observed
maker and taker IDs were traceable to the snapshot or lifecycle stream.

Assessment:

- individual-order book membership and quantity reconstruction: `YES`;
- distinguish fills from cancels: `YES`;
- track partial fills: `YES`;
- exact FIFO truth for orders arriving after the snapshot: `YES`, using `open`
  sequence and Coinbase's documented first-come, first-serve matching;
- exact initial FIFO rank among same-price orders already present in the
  generated snapshot: `UNCERTAIN`. Snapshot tuples do not contain per-order
  arrival timestamps or sequences, and the official REST L3 page does not
  explicitly guarantee same-price array ordering.

Artifacts:

- `artifacts/validation/tardis_coinbase/raw_http_slice.txt`
- `artifacts/validation/tardis_coinbase/raw_sample.jsonl`
- `artifacts/validation/tardis_coinbase/schema_summary.json`
- `artifacts/validation/tardis_coinbase/runtime_report.json`
- `artifacts/validation/tardis_coinbase/http_headers.txt`

Status: `RUNTIME_VERIFIED`

## C. Bitfinex R0

Official:
https://docs.bitfinex.com/reference/ws-public-raw-books

Candidate WebSocket:
`wss://api-pub.bitfinex.com/ws/2`

Subscribe:
- channel `book`
- symbol `tBTCUSD`
- precision `R0`

Need runtime evidence for:
- snapshot
- updates
- ORDER_ID
- PRICE
- AMOUNT
- removal semantics

Do not infer exact fill-vs-cancel truth from removal alone.

Status: `PENDING`

## D. Bitstamp

Official:
https://www.bitstamp.net/api/

### D1 group=2
Verify BTC/USD order book with order IDs.

### D2 public order_data
Verify unauthenticated runtime access and actual response schema.

### D3 continuous order-event stream
Search current official docs and runtime-test only if documented.
Do not assume older community WebSocket channel names are current.

Status: `PENDING`

## E. Exclusions

Kraken:
- actual user signup/KYC failed for the user's country.
- Status: `FAILED / EXCLUDED`

Coinbase direct Exchange L3:
- Status: `NOT_CRITICAL`
- do not make project progress depend on institutional Exchange onboarding.

## Evidence template

For every test:

```text
TEST:
STATUS:
UTC_TIME:
HOST:
COMMAND_OR_SCRIPT:
OFFICIAL_DOC_CLAIM:
RUNTIME_RESULT:
SAMPLE_PATH:
ERROR_IF_ANY:
DECISION:
```
