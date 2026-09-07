# P04 — One-hour stability and systemd deployment

**Record type: reconstructed task specification.** It summarizes the actual
deployment task and retained runtime evidence.

## Objective

- preserve existing ten-minute data and verify collector absence before test;
- run BTCUSDT foreground collection for one hour;
- observe count, continuity, queue/error events, resource use, file growth, and
  UTC hour rolling;
- deploy only after the one-hour gate passes;
- enable only `lobcap@btcusdt.service`;
- verify ten minutes of systemd collection;
- reboot once and verify `/data` remount, automatic service recovery, new
  acquisition segment, and post-reboot data growth.

## Safety boundary

No ETH/SOL, other exchanges, resource resizing, billing changes, or data
deletion.

## Retained evidence

`docs/current/03_GCP_DEPLOY_CURRENT.md`, `systemd/lobcap@.service`, and
`artifacts/stability/`. The deployed BTC collector remains operational.
