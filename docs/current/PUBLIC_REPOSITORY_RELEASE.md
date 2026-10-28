# Public repository release audit

Date: 2026-10-28

The public landing page now describes the implemented replay, execution,
accounting, strategy, signal-research, and UI layers. Development prompts,
workflow notes, the remote bootstrap, and the failure journal moved from the
repository root to `docs/internal/`.

The landing page also includes two bounded workbench captures: the strategy and
execution editor beside reconciled result metrics, and the order/fill/inventory/
PnL timeline used to audit a run.

## Current-tree privacy and size checks

- Replaced the current VM project ID, hostname, zone, user, and absolute home
  paths in active documentation, validation summaries, and service templates
  with portable placeholders or generic installation paths.
- No tracked credential pattern, private runtime home, raw compressed capture,
  or file larger than 5 MiB was found by `scripts/audit_public_repo.py`.
- Modified JSON evidence files parse with Python's case-sensitive JSON parser.
- Local README and internal-document links resolve.
- Frozen `docs/legacy/` and `legacy_code/` remain historical references.

The automated audit checks the current index and worktree. Earlier commits retain
historical infrastructure labels because rewriting published history would change
every affected commit and invalidate recorded provenance. A prior local audit
scanned every reachable Git blob and commit message for credential candidates
without printing values and found no secret requiring revocation. Infrastructure
labels are not credentials; full historical removal would require a separately
approved coordinated history rewrite.

## Validation

- Windows focused checks: 26 passed, 1 skipped. The skip is the existing Windows
  symlink case; the authoritative Linux run covers it.
- Ubuntu isolated-worktree focused checks: 27 passed.
- Ubuntu full regression suite: 466 passed, 2 existing Streamlit/NumPy warnings,
  124.88 seconds.
- A second Ubuntu checkout containing only tracked public files passed 459 tests
  and explicitly skipped the 7 tests that require the excluded Tardis raw slice.
- Linking the retained runtime slice into that isolated checkout made the four
  affected modules pass all 37 tests, confirming the skips do not mask a
  regression when the optional data is available.
- The BTCUSDT collector remained active with zero restarts before and after the
  Linux suite.
- Public-repository audit, Python compilation, JSON parsing, Markdown-link checks,
  and `git diff --check` passed.

No raw market data, generated run payload, credential, service restart, or cloud
infrastructure change was introduced by this cleanup.
