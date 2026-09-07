# Prompt 5E bounded methodology repair

Generated: 2026-09-07T19:13:52Z

## Result

PASS for the bounded methodology gate. This is not an OOS calibration result.
The 12-date/72-window runner was not executed.

## Structural result

- Legacy `A_raw > C` rows: 68
- Caused by hidden priority loss: 68
- Implementation/alignment bugs: 0
- Other unexplained: 0
- `A_visible`: 637.96680544
- `C`: 1535.13005204
- `A_visible > C` violations: 0
- `H_hidden`: 0.00644298
- Hidden priority-loss events: 39
- Hidden priority-loss share: 0.000008925725833043870139354106119
- Bounded one-date `alpha_visible`: 0.4155783443834091954996550561

`H_hidden` is an L3-only diagnostic and is not an L2 model input. No clipping
was used.

## Snapshot and sequence result

- Raw capture gaps: 1 (explained reconnect boundary)
- Applied lifecycle gaps: 0
- Applied duplicates: 0
- Applied reversals: 0
- Acquisition segments: 2
- Unexplained audit gaps: 0

The generated replacement snapshot is an out-of-band acquisition anchor, not
a lifecycle increment or duplicate state mutation.

## Gates

- One-window: PASS
- Six-window: PASS (6/6)
- Deterministic rerun: PASS
- Reconstruction: PASS
- Validation failures: 0

## Provenance

- Git base: `ecf23494c958996feefff3fcdf0ef0bfc8ddd0e9` (`git_dirty=true`)
- Config: `configs/experiments/5e_multidate.yaml`
- Raw config SHA-256: `8a31740c02d63420dfd452799db8382e18742f28e978a3798a7dda6e15299e3b`
- Canonical config SHA-256: `274f5c4bf9a160dd6ddf6b5cd8fe23d4bb5dcf4a2ab0f2bb333580b1ede9d62a`
- Tardis date: `2025-10-01`
- Six-window response SHA-256: `58e6150860e1d48d67e72e3ac7c9f8939b9f383685342d87820e3077190507df`
