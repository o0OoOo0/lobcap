# Decision Log

This is a decision record, not a reconstructed Git history. Earlier decisions
are dated by runtime evidence where available; no commit existed at the time.

## D001 — Runtime evidence outranks project memory

Status: accepted  
Date: 2026-09-06

Actual VM/runtime evidence and current official source documentation outrank
legacy code and old deployment notes.

## D002 — Preserve raw exchange payloads at collection

Status: accepted  
Date: 2026-09-06

The Binance collector stores a schema-v2 envelope and complete exchange
payload. Reconstruction and normalization are downstream operations.

## D003 — Use L3-derived shadow-fill terminology

Status: accepted  
Date: 2026-09-06

Virtual fills are no-market-impact counterfactual labels, not absolute fill
ground truth. Independent virtual branches do not interact.

## D004 — Sequence defines placement order

Status: accepted  
Date: 2026-09-06

Placement occurs after an applied lifecycle sequence. Time-triggered schedules
are resolved to the first applied event at or after the target and persist the
resulting sequence.

## D005 — Limit initial truth experiment to passive BBO

Status: accepted  
Date: 2026-09-06

The initial experiment quotes reconstructed best bid/ask only, avoiding deeper
price-priority questions while testing FIFO queue semantics.

## D006 — Blind L2 models from L3 identity and outcomes

Status: accepted  
Date: 2026-09-07

L2 models consume aggregate updates/trades and sanitized placements. L3 truth
and ahead decomposition join only after predictions are frozen.

## D007 — Structural alpha is not fit to classification accuracy

Status: accepted  
Date: 2026-09-07

Primary alpha calibration uses the aggregate ratio
`sum(A_visible)/sum(C)` on calibration dates. Accuracy-based selection from
test dates is prohibited.

## D008 — Separate visible depletion from hidden priority loss

Status: accepted  
Date: 2026-09-07  
Methodology: `prompt5e-v13`

Cancellation, same-price decrease, and price-change-away old-level depletion
form `A_visible`. Same-price size-increase priority reset and other L3-only
effects form `H_hidden`. Hidden loss is diagnostic only. Numerical clipping is
not allowed.

## D009 — Reconnect snapshot starts a new acquisition segment

Status: accepted  
Date: 2026-09-07

A capture gap outside an evaluation window may recover only through a new
generated snapshot: discard stale state and buffered sequences at/below the
snapshot, then replay later buffered events. A gap inside a frozen evaluation
window remains blocking.

## D010 — Do not launch the full 5E run yet

Status: active hold  
Date: 2026-09-07

The one-window and same-date six-window gates passed. The 12-date/72-window
runner still requires explicit user approval and must begin from a versioned
code/config baseline.

## D011 — Versioned configs are executable experiment settings

Status: accepted
Date: 2026-09-07

From this version onward, experiment configuration files are the executable
source of truth for parameterized experiment settings. The shared loader uses
strict typed validation and runners require an explicit config path. Python
keeps methodology algorithms and compatibility routing, but not an independent
experiment parameter grid.

Canonical parsed-config SHA-256 is the primary semantic fingerprint. Raw-file
SHA-256 is retained to detect textual edits. New-run manifests store both.

## D012 — Start honest Git history with one baseline

Status: proposed  
Date: 2026-09-07

Because there is no Git repository or historical commits, do not fabricate a
sequence of past commits. After review, create one baseline commit containing
the current code, tests, specs, configs, manifests, prompt summaries, and small
evidence. Subsequent methodology/code changes receive normal commits and
annotated experiment tags.
