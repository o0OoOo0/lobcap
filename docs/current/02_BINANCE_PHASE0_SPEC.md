# Binance USDⓈ-M Phase 0 Protocol and Storage Specification

Updated: 2026-09-06

## 1. Scope

Phase 0 collector target:
- BTCUSDT USDⓈ-M perpetual
- depth diff
- aggTrade
- REST depth snapshots
- lifecycle/event log

No orders. No API key.

## 2. WebSocket routing

Current documented routing:
- high-frequency depth -> `/public`
- aggTrade -> `/market`

Therefore use separate connections/tasks.

Initial candidates:
- depth: `wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms`
- trades: `wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade`

Runtime verification is still mandatory.

## 3. Futures sequence semantics

Do not use Spot-style `U == previous_u + 1`.

For reconstruction from snapshot `L = lastUpdateId`:

1. buffer depth events;
2. fetch snapshot;
3. discard events with `u < L`;
4. first processed event satisfies `U <= L <= u`;
5. after that, every event must satisfy:
   `event.pu == previous_event.u`;
6. mismatch means continuity is broken and reconstruction must resync from a new snapshot;
7. quantities are absolute;
8. zero quantity deletes a level.

Collector does not reconstruct, but it records enough metadata to diagnose these rules.

## 4. Raw storage schema v2

### Depth

```json
{
  "schema_version": 2,
  "kind": "depth",
  "venue": "binance",
  "market": "futures",
  "symbol": "btcusdt",
  "connection_id": "...",
  "ts_recv_ns": 0,
  "payload": {}
}
```

`payload` is the complete exchange payload.

### Trade

Same envelope, `kind = "trade"`.

### Snapshot

```json
{
  "schema_version": 2,
  "kind": "snapshot",
  "venue": "binance",
  "market": "futures",
  "symbol": "btcusdt",
  "ts_req_ns": 0,
  "ts_recv_ns": 0,
  "payload": {}
}
```

### Lifecycle event

Operational events are uncompressed append-only JSONL and fsynced:

```json
{
  "schema_version": 2,
  "kind": "event",
  "ts_ns": 0,
  "stream": "depth",
  "event": "connect|disconnect|error|reconnect_sleep|sequence_gap|process_start|process_stop",
  "details": {}
}
```

Purpose:
- a reconnect is visible even if the first post-reconnect sequence happens to look continuous;
- gaps are not lost just because the process later receives SIGKILL/OOM.

## 5. Rolling and durability

High-frequency data:
- UTC hourly path partition
- gzip JSONL
- batch into complete concatenated gzip members rather than keeping one gzip stream open for the entire hour
- this limits crash loss to the current in-memory batch and avoids invalidating all previously closed gzip members

Lifecycle log:
- plain JSONL
- flush + fsync each rare operational event

## 6. Bounded buffering

Do not use an unbounded WebSocket/application queue.

Use bounded WebSocket buffering; never use `max_queue=None`.

The current starter collector relies on WebSocket backpressure rather than an
application-level drop queue. If disk/write stalls cause disconnects or `pu`
gaps, those lifecycle/continuity events make the interval diagnosable.
If a future application queue is introduced, it must expose overflow metrics
and must never silently drop.

## 7. Clock

Latency metrics require VM clock discipline.

Before using exchange timestamp -> receive timestamp latency:
- check `timedatectl status`;
- preferably inspect `chronyc tracking` if chrony is present;
- record NTP state in validation artifact.

Do not interpret p99 latency as network latency if clock sync is unhealthy.

## 8. Quality semantics

For Futures depth:
- continuity uses `pu == prev_u`;
- process/reconnect events are reported separately;
- an explicit durable `process_start` begins a new acquisition segment, so
  offline quality checks do not compare `pu` across separate processes;
- a WebSocket reconnect within the same process does not reset continuity and
  remains subject to the `pu == prev_u` check;
- one full UTC day is used for the 23h+ daily gate;
- use finer coverage bins than “number of distinct hours”.

## 9. Snapshot reconciliation

The old placeholder `snap_checks == 0` is invalid.

Use a shadow-book check:

1. main book is reconstructed continuously from an earlier valid snapshot;
2. for each periodic independent snapshot with ID `L_s`, create a shadow book from that snapshot;
3. find the first depth event satisfying `U <= L_s <= u`;
4. apply that same event to the shadow;
5. after the main book applies the same event, compare main vs shadow at the same final `u`;
6. compare top N levels (default 50) to avoid REST depth-limit boundary artifacts;
7. record:
   - snapshot ID
   - compare event `u`
   - top-N match/mismatch
   - first mismatch
   - crossed state
8. snapshots that cannot be aligned are explicitly `SKIPPED`, not counted as PASS.

## 10. Day-boundary warmup

A date partition is not automatically a valid reconstruction start.

Preferred:
- use previous day's last valid snapshot as warmup;
- replay previous-day tail events after that snapshot;
- continue through the target UTC day.

If no previous-day warmup exists, reconstruct only from the first same-day snapshot and mark the prefix unavailable.

## 11. Phase 0 blockers

Do not enable 24/7 service until:
- Tokyo REST passes
- Public depth passes
- Market aggTrade passes
- v2 schema samples are valid
- unit tests pass
- 10-minute integration test passes
