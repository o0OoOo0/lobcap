# P03 — BTCUSDT ten-minute collector integration

**Record type: reconstructed task specification.** It is a concise task record,
not a verbatim prompt.

## Objective

- run the active Binance collector in foreground for ten minutes;
- write BTCUSDT depth, aggTrade, snapshot, and lifecycle data to `/data`;
- stop with SIGTERM and verify graceful shutdown;
- check full depth continuity, schema-v2 envelope, raw payload preservation,
  gzip integrity, diagnostic quality, and provisional storage rate;
- leave no foreground collector process behind.

## Safety boundary

No systemd creation, one-hour test, additional symbols, strategies, or L3 work.

## Retained evidence

`artifacts/integration/`, `FAILURES.md`, and the deployment notes describing
the corrected `/data` ownership.
