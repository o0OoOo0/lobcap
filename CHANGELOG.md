# Changelog

This changelog records project milestones from retained runtime evidence. It
does not imply historical Git commits existed.

## Unreleased — Reproducibility baseline
- Refactored 5C, 5D, and 5E runners to load versioned YAML through one typed, fail-fast configuration layer.
- Added raw and canonical config fingerprints plus automatic new-run manifest provenance.
- Added explicit config CLI paths and a network-free 5E dry-run plan; the full 12-date experiment remains unexecuted.
- Preserved byte-identical 5C/5D outputs and existing empirical metrics.

- Added current fill-model and experiment protocol specifications.
- Added an explicit decision log.
- Frozen 5C, 5D, and 5E experiment settings in machine-readable YAML.
- Added backfilled manifests with input/output checksums and honest `UNKNOWN`
  values where historical metadata is absent.
- Added reconstructed prompt task records.
- Added Git ignore rules for environments, raw data, bulky derived output, and
  credentials.

## 2026-09-07 — Multi-date methodology work

- Defined semantic `A_visible` and `H_hidden` without numerical clipping.
- Corrected hidden priority-loss share to use total observed L3 ahead reduction.
- Explained the 2025-10-01 reconnect gap and replacement snapshot as a new
  acquisition segment.
- Passed deterministic one-window and same-date six-window gates.
- Did not start the full 12-date/72-window runner.

## 2026-09-07 — L2 fill comparison

- Added blinded L3-to-L2 projection and fixed alpha model family.
- Verified no L3 identity/outcome leakage into fill-model inputs.
- Recorded 64-placement deterministic metrics, including alpha=0 FN 14,
  alpha=1 FP 2, and TOUCH_FILL FP 8.

## 2026-09-06 — Shadow-fill engine

- Added independent no-market-impact virtual-order branches at BBO.
- Added ahead/behind lifecycle tracking, partial fills, same-price-behind and
  trade-through evidence, TTL outcomes, and invariants.
- Produced 64 deterministic L3-derived shadow-fill labels.

## 2026-09-06 — Deterministic L3 reconstruction

- Added Coinbase native message parsing and generated-snapshot buffering.
- Added order-level Decimal book state, lifecycle handling, sequence checks,
  and consistency reports.

## 2026-09-06 — Coinbase/Tardis L3 access

- Runtime-verified unauthenticated first-of-month raw Coinbase L3 access.
- Preserved bounded schema and snapshot/lifecycle evidence for 2026-09-01.

## 2026-09-06 — Binance collector validation and deployment

- Runtime-verified Tokyo REST, routed depth, and aggTrade access.
- Passed ten-minute and one-hour BTCUSDT capture tests with schema-v2 storage.
- Installed and reboot-tested `lobcap@btcusdt.service`.
- Left only the BTCUSDT service enabled; ETHUSDT/SOLUSDT remain inactive.
