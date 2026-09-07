# 5C — L3-derived shadow-fill truth

This experiment inserts 64 independent, passive BBO virtual orders into a
fixed one-minute Coinbase L3 replay. It produces counterfactual shadow-fill
labels, not observed fills of real submitted orders.

Reproduce:

```bash
python -m lobcap.l3.virtual \
  --config configs/experiments/5c_virtual_truth.yaml \
  --verify-determinism
```

The raw input is intentionally ignored by Git. Obtain a byte-identical input
whose SHA-256 matches the manifest, or supply a separately versioned fixture.
Current code reproduced the checked-in truth output byte-for-byte on
2026-09-07. Expected headline output: 64 placements, 38 full fills, 12
partial-only, 10 no-fill, and 4 censored.
