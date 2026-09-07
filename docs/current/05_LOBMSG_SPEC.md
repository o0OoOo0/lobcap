# LOBMSG v1 — FPGA Book-Builder Input

Updated: 2026-09-06

## 1. Purpose

LOBMSG v1 exists for software-vs-FPGA **L2 book reconstruction**.

It is not the complete market-simulation event stream.

Trades remain in the separate raw/replay trade dataset for Phase 0.

## 2. 48-byte layout

Little-endian:

```text
offset size field
0      1    msg_type
1      1    side
2      6    padding
8      8    price_ticks
16     8    qty_lots
24     8    seq
32     8    ts_exch_ns
40     8    ts_recv_ns
```

## 3. Message types v1

```text
0 LEVEL
1 DELETE
2 reserved
3 SNAP_BEGIN
4 SNAP_END
```

`2` is reserved; do not emit TRADE in v1.

## 4. Self-contained reconstruction rule

A `.lobmsg` file intended for bit-exact book building must begin from a valid snapshot:

1. `SNAP_BEGIN` with `seq = lastUpdateId`;
2. one `LEVEL` message for every included bid/ask snapshot level;
3. `SNAP_END`;
4. then ordered diff `LEVEL`/`DELETE` messages.

Therefore the old behavior that emitted only depth diffs is insufficient.

## 5. Snapshot metadata

For SNAP markers:
- side = 0
- price = 0
- qty = 0
- seq = snapshot `lastUpdateId`
- `ts_recv_ns` = REST snapshot receive time
- `ts_exch_ns` = exchange snapshot event/transaction timestamp if provided, otherwise 0

## 6. Diff metadata

For each changed level:
- side: 0 bid / 1 ask
- seq = depth event final update ID `u`
- timestamps from the depth event/envelope

## 7. Tick/lot

Use integer representation based on date-valid exchange metadata.

If a price or quantity is not exactly representable under configured tick/lot:
- fail loudly;
- do not round silently.

## 8. Future v2

A later simulator-wide event format may add:
- trades;
- connection gaps;
- latency markers;
- virtual order events.

Do not expand FPGA v1 until the software replay API is stable.
