# P05C — L3-derived shadow-fill labels

**Record type: reconstructed task specification.** It captures implemented
requirements but is not a verbatim prompt.

## Objective

- define labels as no-market-impact counterfactual shadow fills;
- treat each virtual order as an independent branch;
- place at BBO immediately after a resolved lifecycle sequence;
- snapshot all same-level resting actual orders as ahead;
- track trade, cancellation, size-decrease, and priority-loss depletion;
- allocate fills from same-price behind matches and trade-through evidence;
- support partial fills, TTL, censoring, Decimal state, and invariants;
- run a deterministic 64-placement bounded sample.

## Frozen settings and evidence

`configs/experiments/5c_virtual_truth.yaml` and
`experiments/5c_virtual_truth/manifest.json` define the current frozen record.
Expected truth output SHA-256 is
`593920dc416631c0f4924d9ff420ae6b8801580c85cdc50c9db06337773f28ca`.
