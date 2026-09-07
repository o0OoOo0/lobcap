# P05D — Truth versus blinded L2 fill assumptions

**Record type: reconstructed task specification.** It summarizes the
implemented comparison and retained results.

## Objective

- downsample the same L3 replay into an identity-stripped L2 projection;
- ensure models cannot import or receive L3 truth/decomposition;
- apply identical placements to alpha 0/0.25/0.50/0.75/1 and TOUCH_FILL;
- compare any-fill, full-fill, quantity, bias, and timing errors;
- join L3 decomposition only for post-hoc diagnostics;
- verify deterministic replay and leakage invariants.

## Frozen expected metrics

- 64 placements;
- truth any-fill 54/64 = 84.375%;
- alpha=0 any-fill FN 14;
- alpha=1 any-fill FP 2;
- TOUCH_FILL any-fill FP 8.

The exact config/checksum/code/artifact linkage is in
`experiments/5d_fill_comparison/manifest.json`.
