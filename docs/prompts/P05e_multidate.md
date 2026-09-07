# P05E — Multi-date structural calibration and OOS evaluation

**Record type: reconstructed task specification.** The original user task and
subsequent bounded methodology audit were available during reconstruction;
this is a concise operational record, not a verbatim transcript.

## Planned design

- twelve first-of-month Coinbase dates;
- six deterministic ten-minute windows per date;
- chronological date split: oldest 6 calibration, next 3 validation, newest 3
  final test;
- stream raw HTTP responses through L3 truth, blinded L2, predictions, and
  compact output without retaining full raw days;
- calibrate `alpha_visible=sum(A_visible)/sum(C)` on calibration dates;
- freeze alpha before final test;
- report per-window/per-date metrics and structural heterogeneity.

## Methodology amendment

`A_visible` contains only ahead depletion visible as L2 old-level non-trade
depletion. `H_hidden` contains same-price size-increase priority reset and
other L3-only loss, never enters the L2 model, and is not clipped into C.

A reconnect snapshot starts a new acquisition segment. Gaps inside evaluation
windows remain blocking.

## Current status

The one-window and all six 2025-10-01 windows passed under the bounded v13
record. The remaining eleven dates and full 72-window OOS procedure have not
run and require explicit approval.

Evidence and config:
`experiments/5e_multidate/manifest.json`,
`configs/experiments/5e_multidate.yaml`, and
`artifacts/multidate_calibration/bounded/six_window_gate_2025-10-01_summary.json`.
