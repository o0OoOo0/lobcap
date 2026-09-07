# P05B — Deterministic Coinbase L3 reconstruction

**Record type: reconstructed task specification.** This is not a verbatim
prompt transcript.

## Objective

- parse the verified Tardis-native schema, not the legacy prototype;
- buffer lifecycle events until generated snapshot arrival;
- initialize at snapshot sequence, discard older/equal buffered events, and
  replay later sequences;
- maintain per-order side, price, and Decimal remaining size;
- handle received/open, maker matches, done, size changes, and priority moves;
- enforce sequence, nonnegative size, membership, aggregate, and uncrossed-book
  invariants;
- prove deterministic replay.

## Retained evidence

`src/lobcap/l3/{schema,book,reconstruct}.py`,
`artifacts/l3_reconstruction/reconstruction_report.json`, and
`invariant_report.json`.
