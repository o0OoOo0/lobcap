# Experiment Protocol and Reproducibility

Status: config-driven protocol after reproducible baseline

## 1. Sources of truth

For a reproducible experiment, record all of:

1. Git commit and optional annotated tag;
2. immutable config path and experiment version;
3. local or external input identity with SHA-256;
4. exact command and Python environment;
5. output paths and SHA-256;
6. start/end UTC timestamps and terminal status;
7. invariant and leakage checks.

Until the first baseline commit exists, manifests use `git_commit: UNKNOWN`.
They must be amended with the actual commit hash only by a new, honest commit;
historical hashes must not be fabricated.

## 2. Configuration rule

Files under `configs/experiments/` are executable inputs and the sole source of
truth for parameterized experiment settings. Runners require an explicit config
path, validate it centrally, and record raw and canonical fingerprints.

No current experiment uses randomness. `random_seed: null` means not
applicable, not an uncontrolled seed.

## 3. Artifact policy

Commit:

- code, tests, configs, docs, manifests;
- deterministic small fixtures where licensing permits;
- small JSON/Markdown summaries and checksums.

Do not commit:

- `/data` market capture;
- full/raw Tardis dumps;
- large L2 projections or gzip replay outputs;
- virtual environments and caches;
- credentials, tokens, auth files, or private keys.

An ignored input may still be reproducible when its source request and expected
SHA-256 are recorded. A checksum mismatch is a different dataset and must not
silently reuse the old manifest.

## 4. Manifest lifecycle

The schema is `experiments/manifest.schema.json`. Before a new run:

- copy or version the config;
- create a manifest with intended date/window and inputs;
- set `git_commit` to the actual clean-tree commit;
- set status to `PLANNED`.

During/after the run:

- record actual UTC start/end;
- record output checksums;
- record `PASS`, `FAIL`, or a precise partial status;
- do not overwrite prior accepted manifests when methodology changes.

Backfilled manifests use `UNKNOWN` for facts absent from preserved evidence.
Filesystem modification time is not substituted for a historical experiment
timestamp.

## 5. Reproduction commands

Use the project environment:

```bash
cd /home/0woo/lobcap
. .venv/bin/activate
python -m compileall -q src scripts tests
pytest -q
```

### 5C virtual truth

```bash
python -m lobcap.l3.virtual \
  --config configs/experiments/5c_virtual_truth.yaml \
  --verify-determinism
```

Expected input SHA-256:
`16792f94e6c30be4b8985aa96beec5c2b92ceedce28efc1b97d5c2d0041eb8af`.

Expected `virtual_orders.jsonl` SHA-256:
`593920dc416631c0f4924d9ff420ae6b8801580c85cdc50c9db06337773f28ca`.

### 5D fill comparison

```bash
python -m lobcap.l2.compare \
  --config configs/experiments/5d_fill_comparison.yaml \
  --verify-determinism
```

Expected key metrics:

| Metric | Expected |
|---|---:|
| placements | 64 |
| truth any-fill | 54/64 = 84.375% |
| alpha=0.00 any-fill FN | 14 |
| alpha=1.00 any-fill FP | 2 |
| TOUCH_FILL any-fill FP | 8 |

These values are tied to
`configs/experiments/5d_fill_comparison.yaml`,
`src/lobcap/l2/downsample.py`,
`src/lobcap/l2/fill_models.py`,
`src/lobcap/l2/compare.py`, the two input checksums in the manifest, and
`artifacts/fill_model_comparison/confusion_matrices.json`.

On 2026-09-07 the current code reproduced both 5C and 5D comparison artifacts
byte-for-byte in `/tmp`.

### 5E bounded gates

```bash
python scripts/validate_prompt5e_bounded.py one-window --label run1
python scripts/validate_prompt5e_bounded.py six-window
```

The full command exists but is not authorized by the current experiment state:

```bash
python -m lobcap.l2.multidate \
  --config configs/experiments/5e_multidate.yaml \
  --run-all
```

Do not run it until explicit approval and a baseline Git commit identify the
exact code/config. The completed scope is one date/six windows, not the planned
12 dates/72 windows.

## 6. Acceptance checks

Every L3-derived run must check sequence, reconstruction consistency, crossed
book, pending taker reconciliation, virtual-order invariants, Decimal bounds,
and deterministic rerun where specified.

Every L2 comparison must additionally check:

- no L3 imports in the model module;
- no order IDs or decomposition fields in L2 frames;
- identical placement signatures across models;
- no negative queue, overfill, or trade double count.

An unexplained sequence gap or methodology/config mismatch blocks promotion of
an output to an accepted experiment.

## 7. Current config/code parity audit

The following values were read from active Python and compared with the YAML:

| Setting | Active code | Config | Result |
|---|---|---|---|
| 5C trigger seconds | 5, 20, 35, 50 | same | MATCH |
| sides | buy, sell | same | MATCH |
| sizes BTC | 0.001, 0.01 | same | MATCH |
| TTL seconds | 1, 5, 10, 30 | same | MATCH |
| 5D alpha grid | 0, .25, .50, .75, 1 | same | MATCH |
| 5D touch model | TOUCH_FILL | same | MATCH |
| 5E dates | 2025-10 through 2026-09 first-of-month | same | MATCH |
| 5E windows | 00, 04, 08, 12, 16, 20 UTC; 10 minutes | same | MATCH |
| split | oldest 6 / next 3 / newest 3 dates | same | MATCH |
| random seed | no RNG | null/not applicable | MATCH |

There is no value mismatch. From the config-driven refactor onward, experiment
configuration files are the executable source of truth for parameterized
experiment settings. CLI execution requires an explicit `--config`; invalid or
unknown critical fields fail before processing. The machine-readable mapping is
`artifacts/reproducibility/config_refactor_audit.json`.

Both raw YAML bytes and the canonical parsed representation receive SHA-256
fingerprints. New-run manifests record both hashes, config path, Git state,
experiment name, and version. Whitespace/comments affect only the raw hash.

The network-free 5E planning command is:

```bash
python -m lobcap.l2.multidate \
  --config configs/experiments/5e_multidate.yaml \
  --dry-run
```

Repository-only unit reproduction uses the small fixtures in
`tests/fixtures/tardis_coinbase/`. Full 5C/5D metric reproduction additionally
requires the ignored 17.9 MB raw slice with the manifest checksum. Therefore
the result is traceable and checksum-reproducible, but not fully self-contained
in Git until an approved/licensed fixture distribution mechanism exists.
