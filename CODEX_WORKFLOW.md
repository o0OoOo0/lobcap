# Codex Workflow — Remote GCP Project

## Source priority

1. runtime evidence
2. current official docs
3. `docs/current/`
4. `src/` + tests
5. legacy files

## Safety/operation rules

- Existing VM only.
- Do not create/resize/delete GCP resources.
- Do not change billing.
- Public market data only for current phases.
- No trading API keys.
- Never put secrets in git.
- Never delete raw data.
- Never silently ignore a sequence gap, reconnect, queue overflow, corrupted file, or excluded interval.
- Do not enable long-running systemd service until short tests pass.
- Every failure goes to `FAILURES.md`.

## Legacy policy

`docs/legacy/` and `legacy_code/` are frozen historical references.

Do not modify them.

If they disagree with current docs or official runtime facts, current/runtime wins.

## Current code policy

`src/` is the active implementation.

The v2 code is a starter implementation and must still be audited/runtime-tested.

## Task completion report

Every Codex task ends with:

```text
Files changed:
Commands run:
Official docs checked:
Tests passed:
Tests failed:
Runtime observations:
Data written:
Open risks:
Recommended next action:
```

## Gates

### 0 bootstrap
Read all current docs. No network test yet.

### 1 unit/static validation
- compile Python
- run pytest
- inspect schema
- inspect filesystem

### 2 external runtime validation
- Binance first
- Tardis
- Bitfinex
- Bitstamp

### 3 Binance 10 min
foreground only

### 4 Binance 1 hour
foreground or transient service

### 5 systemd + reboot
only if prior gates pass

### 6 full UTC day quality
only then scale symbols
