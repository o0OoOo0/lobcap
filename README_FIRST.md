# LOB Market-Making Simulation — Codex Remote Bootstrap v2

Updated: 2026-09-06

This pack is designed to be uploaded as-is to:

`/home/0woo/lobcap`

on the existing GCP Tokyo VM.

## Important status

The VM/infrastructure setup is already done.

Current environment:
- GCP project: `project-2878cd89-af59-4d8a-aef`
- VM: `lob-collector-01`
- zone: `asia-northeast1-a`
- OS: Ubuntu 24.04 LTS
- user: `0woo`
- project root: `/home/0woo/lobcap`
- data root: `/data`
- current data disk: approximately 100 GB

The existing GCP VM may incur billable usage. Codex must **not create additional GCP resources, change machine/disk/network configuration, or modify billing** without explicit user approval.

## What changed from the old project pack

Known stale/incorrect assumptions were separated from the current implementation.

Most important corrections:
1. Binance USDⓈ-M Futures depth continuity uses `pu == previous u`, not Spot-style `U == previous u + 1`.
2. First Futures event after a REST snapshot uses `U <= lastUpdateId <= u`.
3. Binance Futures WebSocket routing changed in 2026:
   - depth -> `/public`
   - aggTrade -> `/market`
4. `@depth@0ms` is not used. Current official documented fast depth option is `@100ms`.
5. Depth and aggTrade are separate connections/tasks.
6. Connection lifecycle and sequence discontinuities must be persisted immediately.
7. Raw exchange payloads are preserved rather than manually dropping useful fields.
8. Snapshot reconciliation is specified as a real shadow-book comparison, not a placeholder counter.
9. LOBMSG v1 is restricted to book-building input: snapshot markers + snapshot levels + L2 level/delete events. Trades remain a separate replay stream for now.
10. `legacy_code/l3_queue.py` is explicitly a prototype and must not be used as L3 ground truth until actual Tardis runtime schema has been validated.
11. Oracle deployment docs are legacy. Current deployment is GCP Tokyo / user `0woo`.

## Read order for Codex

1. `docs/current/00_CURRENT_PLAN.md`
2. `docs/current/01_DATA_SOURCE_VALIDATION.md`
3. `docs/current/02_BINANCE_PHASE0_SPEC.md`
4. `docs/current/03_GCP_DEPLOY_CURRENT.md`
5. `docs/current/04_L3_PHASE05_SPEC.md`
6. `docs/current/05_LOBMSG_SPEC.md`
7. `CODEX_WORKFLOW.md`
8. `CODEX_PROMPTS.md`
9. legacy docs/code only as historical context

## What is already implemented in `src/`

The v2 pack contains a reviewed starter implementation for:
- Binance protocol helpers
- Binance USDⓈ-M raw collector
- daily quality checker
- offline L2 rebuild + snapshot shadow reconciliation
- healthcheck script
- protocol unit tests

These are **starter implementations, not runtime-certified production code**.

Codex must run Prompt 1 and Prompt 2 before any long-running service is enabled.

## L3 status

No new production L3 parser is included intentionally.

Reason:
the exact Tardis/Coinbase raw runtime payload must first be downloaded and inspected. Building a parser from memory would repeat the mistake this project is trying to avoid.

The old `legacy_code/l3_queue.py` is kept only as a research sketch.

## Local verification already performed on this pack

Before packaging:
- Python compile check passed
- unit tests passed

These are offline tests only. External exchange/vendor access is still `PENDING`
until Prompt 2 is run from the actual GCP VM.
