# Coinbase/Tardis raw L3 regression fixtures

These messages were extracted from the runtime-verified Tardis `coinbase`
`BTC-USD` slice for `2026-09-01T00:00:00Z` through `00:01:00Z`.

- `overlap.jsonl` contains source lines 2225–2232 followed by the generated
  snapshot from source line 3754. The snapshot arrays are pruned to three
  verbatim order tuples needed by this test; its metadata and lifecycle
  messages are unchanged. This keeps the fixture small while retaining the
  observed late-snapshot capture order.
- `partial_filled.jsonl` contains verbatim source lines 2299, 2300, 2881,
  2921, 3178, and 3179 for one order that opens, receives three partial
  matches, and finishes with `done(reason=filled)`.
- `change_priority.jsonl` contains verbatim modify-order events from source
  lines 2427 and 5884: same-price size decrease and same-price size increase.

The full runtime sample remains under
`artifacts/validation/tardis_coinbase/raw_http_slice.txt` and is not copied
here.
