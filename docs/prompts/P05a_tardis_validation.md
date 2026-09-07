# P05A — Tardis/Coinbase raw L3 validation

**Record type: reconstructed task specification.** The original user-provided
task was available as an external attachment during reconstruction, but this
file intentionally stores a concise specification rather than claiming
verbatim archival.

## Objective

- verify current Tardis free first-of-month access without an API key;
- retrieve only a small Coinbase exchange-native raw L3 slice;
- observe generated full snapshot and received/open/match/done/change schemas;
- test lifecycle sequence and snapshot-buffer alignment;
- determine whether order entry, fill/cancel distinction, partial fills, and
  per-order reconstruction are supported;
- preserve bounded schema evidence.

## Safety boundary

Do not implement the production book, download a full day, or touch Binance.

## Retained evidence

`artifacts/validation/tardis_coinbase/runtime_report.json`,
`schema_summary.json`, and a raw input identified by SHA-256
`16792f94e6c30be4b8985aa96beec5c2b92ceedce28efc1b97d5c2d0041eb8af`.
