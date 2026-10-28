# Codex Prompts v2

Run one prompt at a time.

---

## Prompt 1 — Bootstrap, audit, unit tests

```text
You are on the existing remote GCP Tokyo VM.

Project root:
  /home/<vm-user>/lobcap
Data root:
  /data
User:
  <vm-user>

Read these first:
- docs/internal/REMOTE_BOOTSTRAP.md
- docs/current/00_CURRENT_PLAN.md
- docs/current/01_DATA_SOURCE_VALIDATION.md
- docs/current/02_BINANCE_PHASE0_SPEC.md
- docs/current/03_GCP_DEPLOY_CURRENT.md
- docs/current/04_L3_PHASE05_SPEC.md
- docs/current/05_LOBMSG_SPEC.md
- docs/internal/CODEX_WORKFLOW.md

Then inspect:
- src/
- tests/
- docs/legacy/
- legacy_code/

Important:
legacy docs/code are frozen historical references and contain known stale assumptions.

Explicitly verify that the active/current design has fixed or addressed these known issues:
1. Futures continuity is pu == previous u, not U == previous u + 1.
2. First event overlap is U <= lastUpdateId <= u.
3. Binance depth and aggTrade use current routed /public and /market connections.
4. @depth@0ms is not used; current documented 100ms candidate is used for validation.
5. reconnect/process lifecycle is persistently recorded.
6. raw exchange payload is preserved.
7. operational gaps are not saved only at graceful shutdown.
8. snapshot reconciliation is real shadow-book comparison, not a placeholder counter.
9. LOBMSG starts with a snapshot and is self-contained for L2 book reconstruction.
10. L3 legacy code is not treated as production.
11. current infrastructure is GCP Tokyo / <vm-user> / /home/<vm-user>, not Oracle / ubuntu.
12. cloud rule means no NEW resource/billing changes, not that the existing Tokyo VM is free.
13. clock/NTP state is checked before interpreting latency.
14. target-day rebuild supports/clearly handles previous-day warmup.

Tasks:
- run hostname, whoami, pwd, df -h /data, findmnt /data, timedatectl status
- create/activate .venv if needed
- install requirements.txt and `pip install -e .`
- run python compile checks
- run pytest
- audit active code against current docs
- do NOT access external exchange endpoints yet
- do NOT run long-running collectors
- do NOT edit legacy files

If you find a defect in active code, fix src/tests/current docs only and rerun tests.

Finish with the standard task completion report.
```

---

## Prompt 2 — Runtime source validation

```text
Now execute docs/current/01_DATA_SOURCE_VALIDATION.md from the actual GCP Tokyo VM.

Priority:
1. Binance REST
2. Binance Public depth WS
3. Binance Market aggTrade WS
4. Tardis Coinbase raw historical L3 first-of-month free-access test
5. Bitfinex R0
6. Bitstamp group=2 + public order_data + official continuous-stream investigation

Rules:
- use CURRENT OFFICIAL docs, not search snippets/community posts
- distinguish DOC_VERIFIED from RUNTIME_VERIFIED
- save only small validation samples under artifacts/validation/
- no full-day L3 download yet
- no long-running service
- no private trading API
- exact runtime errors go to docs/internal/FAILURES.md

Binance blocking checks:
- Tokyo region is not assumed safe; explicitly test for 451/403
- depth must actually receive >=100 events
- aggTrade must actually receive >=10 events
- verify U/u/pu fields on depth
- verify NTP status before interpreting latency

Tardis:
- prove raw/exchange-native L3 without API key on a first day of month
- do not use normalized L2 CSV as proof
- inspect actual message schemas before writing production L3 code

Bitfinex:
- verify R0 ORDER_ID/PRICE/AMOUNT
- do not infer exact fill/cancel from removal

Bitstamp:
- verify current official group=2 and public order_data
- do not trust old live_orders channel names unless current docs + runtime verify them

Update the validation ledger with evidence.

Stop after the validation report.
Do not start continuous collection yet.
```

---

## Prompt 3 — Binance 10-minute integration test

```text
Proceed only if Binance REST + Public depth + Market aggTrade runtime validation passed.

Use the active src implementation.

Before running:
- review current official Binance Connect, routing-change, and local-order-book docs one more time
- confirm @depth@100ms is currently supported
- confirm depth /public and aggTrade /market

Run BTCUSDT only for 10 minutes in foreground.

Validate:
- depth count
- aggTrade count
- snapshot count
- lifecycle events
- sequence_gap events using pu
- reconnect events
- bounded WebSocket buffering (no `max_queue=None`) and any downstream gap/reconnect evidence
- gzip readability
- schema v2 envelope
- raw payload preservation
- CPU/RAM
- bytes written
- /data free space

Then run the active quality checker on the short sample in diagnostic mode.

Do NOT create or enable systemd.
Stop and report.
```

---

## Prompt 4 — One-hour stability, then systemd

```text
Proceed only if Prompt 3 passed.

Run BTCUSDT for one hour.

Measure:
- depth/trade msg rate
- reconnects
- pu gaps
- CPU/RAM
- write throughput
- compressed bytes/hour
- file integrity
- lifecycle log
- writer queue/backpressure

If and only if the hour is clean:
- create /etc/systemd/system/lobcap@.service using docs/current/03_GCP_DEPLOY_CURRENT.md
- use user <vm-user>
- RequireMountsFor=/data
- use /home/<vm-user>/lobcap/.venv
- enable only lobcap@btcusdt
- reboot once
- verify /data remount
- verify service resumes
- verify new data arrives

Do not add ETH/SOL.
```

---

## Prompt 5 — L3 ground-truth prototype

```text
Proceed while Binance BTC is collecting, but only after a L3 source was RUNTIME_VERIFIED in Prompt 2.

Read docs/current/04_L3_PHASE05_SPEC.md.

Do NOT repair legacy_code/l3_queue.py in place.

Create a new production prototype under src/lobcap/l3/ based on ACTUAL saved runtime samples.

First implement:
- exact parser/schema adapter
- snapshot loader
- sequence validation
- FIFO order book
- unit tests built from saved real samples

Then implement virtual orders with:
- side check
- partial fill
- own remaining size
- ahead IDs
- ahead fill vs cancellation accounting
- first/full fill time

Then:
- downsample the exact same L3 stream to L2
- implement pessimistic
- calibrated-neutral alpha model
- queue-optimistic alpha=1 diagnostic
- separate touch-fill upper bound

Run only a small historical window.
Produce a small truth-vs-model confusion report.

Do not transfer parameters to Binance yet.
```

---

## Prompt 6 — Full UTC day quality and reconstruction

```text
After BTCUSDT has been collecting across a full UTC day:

1. select the first complete UTC day, not a partial deployment date
2. run quality checks
3. run offline reconstruction
4. use previous-day warmup if available
5. perform independent snapshot shadow-book reconciliation
6. report:
   - coverage
   - message counts
   - snapshots
   - pu gaps
   - process/connect/reconnect events
   - latency percentiles with clock-sync status
   - crossed books
   - snapshot checks/pass/mismatch/skipped
   - compressed bytes/day
7. record any invalid interval/day in docs/internal/FAILURES.md

Only if the day passes:
- project 30/90-day storage
- add ETHUSDT
- add SOLUSDT as separate services

Do not claim Phase 0 complete if snapshot reconciliation has no valid checks.
```
