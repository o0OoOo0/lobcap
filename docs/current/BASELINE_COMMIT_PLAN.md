# Baseline Commit and Push Plan

Current state: `/home/0woo/lobcap` is not a Git repository. There is no branch,
remote, commit, or tag. This plan does not authorize creating any of them.

## Baseline contents

Include in the first reviewed baseline:

- root project metadata and workflow files;
- `src/` active implementation;
- `tests/` including small deterministic Coinbase fixtures;
- `scripts/` and `systemd/` source-controlled operational files;
- `configs/experiments/`;
- `experiments/` manifests, schema, and README files;
- `docs/current/`, `docs/prompts/`, and clearly labeled `docs/legacy/`;
- small JSON/JSONL/Markdown runtime summaries under `artifacts/`;
- `legacy_code/` as explicitly non-active historical material.

The temporary-index audit currently finds 129 prospective files after the
addition of this plan. Review the full `git status --short` immediately after
any future `git init`; do not assume this count remains stable.

## Exclusions

The root `.gitignore` excludes:

- `.venv/`, Python caches, test caches, and egg metadata;
- `/data/` and market-data formats;
- `artifacts/validation/tardis_coinbase/raw_http_slice.txt`;
- `artifacts/validation/tardis_coinbase/raw_sample.jsonl`;
- `artifacts/fill_model_comparison/l2_projection.jsonl`;
- `artifacts/multidate_calibration/**/*.json.gz`;
- compressed JSONL/CSV captures;
- secrets, private keys, environment files, credentials, auth, and token files;
- the redundant `lobcap_codex_v2.zip` bootstrap bundle.

## Proposed commit sequence

1. After explicit approval, initialize `main` and make exactly one honest
   baseline commit:
   `chore: establish reproducible lobcap baseline`.
2. In a later commit, make YAML the executable configuration source and add
   config/code parity tests:
   `refactor: load experiments from versioned configs`.
3. Only after separate approval and the actual full run, add immutable 5E
   outputs/manifests:
   `experiment: record 12-date Prompt 5E OOS evaluation`.

Do not split old milestones into fake historical commits.

## Proposed tags

- On the approved baseline commit:
  annotated tag `v0.1.0-repro-baseline`.
- Do not retrospectively tag 5C/5D as if commits existed then.
- After a future accepted full 5E run:
  annotated tag `exp-5e-v1` or a date-qualified successor chosen before run.

## Push gate

Before push:

1. confirm repository URL, visibility, and owner;
2. configure Git author name/email;
3. initialize `main` only with explicit approval;
4. run tests, secret scan, large-file scan, and `git diff --cached`;
5. verify ignored raw files with `git check-ignore -v`;
6. show the baseline commit tree and request explicit push approval;
7. add an existing user-specified remote—never create one implicitly;
8. push only after approval.
