# 5D — Blinded L2 fill-assumption comparison

5D projects the verified Coinbase replay to aggregate L2, strips order IDs and
L3 decomposition from model inputs, evaluates a fixed alpha grid plus
`TOUCH_FILL`, and joins L3 truth only after predictions are frozen.

Reproduce:

```bash
python -m lobcap.l2.compare \
  --raw artifacts/validation/tardis_coinbase/raw_http_slice.txt \
  --truth artifacts/virtual_truth/virtual_orders.jsonl \
  --out /tmp/lobcap-5d \
  --verify-determinism
```

Expected key metrics:

- placements: 64
- truth any-fill: 54/64 = 84.375%
- alpha=0.00 any-fill false negatives: 14
- alpha=1.00 any-fill false positives: 2
- TOUCH_FILL any-fill false positives: 8

These values are defined by the config, input checksums in the manifest,
`src/lobcap/l2/{downsample,fill_models,compare}.py`, and the checked-in
confusion matrix. They were reproduced byte-for-byte with the current code.
