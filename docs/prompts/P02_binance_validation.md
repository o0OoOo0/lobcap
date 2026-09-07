# P02 — Binance runtime validation

**Record type: reconstructed task specification.** It summarizes the actual
runtime task and evidence; it is not a verbatim prompt transcript.

## Objective

- verify Tokyo access to Binance USDⓈ-M REST ping and BTCUSDT depth;
- connect to routed `/public` depth and `/market` aggTrade streams;
- retain representative raw payloads;
- validate `pu == previous u` across depth messages;
- distinguish runtime-verified behavior from 24-hour disconnect behavior that
  a short test cannot exercise;
- record clock synchronization and update the validation ledger.

## Safety boundary

No ten-minute collector, long-term data accumulation, ETH/SOL, or systemd.

## Retained evidence

- `artifacts/validation/binance_runtime_report.json`
- `artifacts/validation/binance_depth_samples.jsonl`
- `artifacts/validation/binance_aggtrade_samples.jsonl`
- `docs/current/01_DATA_SOURCE_VALIDATION.md`
