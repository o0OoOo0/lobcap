# FAILURES.md

Nothing is silently excluded.

## 2026-09-10 — Bounded research implementation

- Auto-review rejected optional Ubuntu `git restore` synchronization because it might overwrite uncommitted work. The command did not execute and was not bypassed. Kept runtime files and Git HEAD intact; a read-only 34-file comparison with implementation commit 5ea375a passed after line-ending normalization.

- Native Windows pytest hit temporary-directory/cache ACL errors, including with a workspace basetemp. Moved authoritative checks to the existing Ubuntu runtime; no research portability code or ACLs changed.
- The first hedge fixture used a 10 bps guard with a synthetic 50 bps half-spread. Set the fixture guard to 100 bps; retained the production 10 bps rule and added separate guard rejection coverage.
- Browser automation failed to initialize its kernel assets (OS error 3). Streamlit AppTest covers navigation and form/CLI parity; localhost health is checked separately. Do not claim a new manual browser walkthrough.
- A documentation patch used a nonexistent README anchor and was rejected atomically. Read the actual heading and reapplied; no code was affected.
- Sandbox SSH wrapper produced no remote command execution. The explicit authorized SSH invocation succeeded; remote collector was active.

## 2026-09-09 — Strict lab usability and integrity review

- During staged deployment, Streamlit retained an older presentation module while reading the updated page, producing a missing-label key error. Restarted the lab process after all modules were synchronized; fresh-session saved-history rendering then passed.
- Browser verification found Streamlit removes a widget's state when its page is not rendered. Selecting an older run could be lost after visiting sensitivity. Added a separate durable selection value and a navigation regression test.
- The local SSH reconnect helper had exited, leaving port 8501 unavailable while the VM app remained healthy. Restarted the existing helper; verified localhost afterward. It is an active process, not an installed persistent service.
- Review found that sensitivity silently used the last submitted scenario, and retained results across catalogue changes. Replaced this with explicit saved-run/default selection and dataset-scoped state.
- A new regression test exposed that decimal strings `.5` and `0.50` have different historical configuration fingerprints. Duplicate sweep values now compare typed numeric values, without rewriting accepted provenance.
- The local environment has no Streamlit installation. UI tests run in the existing Ubuntu web environment; no Windows research portability changes were made.
- Ordinary run diagnostics overstated independent source verification. Their wording now distinguishes engine invariants from a separate source audit.

## 2026-09-09 — Recorded-arrival extension development

- Updating Streamlit source while its Python process retained the old imported configuration module produced `ImportError: cannot import name 'STRINGS'`. Restarted only the identified lab process; subsequent fresh browser connections use the new modules. The collector was not restarted.
- A late-fill test initially expired its order before the intended fill. Set its explicit test maximum age to 10 seconds; engine semantics unchanged.
- The zero-fill participation test initially indexed an absent in-memory `fills` channel. Corrected the assertion to accept an absent channel.
- Capacity stress now rounds its per-trade cap down to instrument lots, preventing the stress overlay from creating sub-lot fills. Baseline participation=1 retains established queue semantics.
- No raw data, collector state, or accepted v1 artifacts were changed or excluded.

## 2026-09-09 — Backtest MVP implementation validation

**Component:** bounded backtest lab, isolated feature worktree.
**Severity:** implementation validation failure; no source-data exclusion.

- Native Windows research collection has the existing Unix-only `resource`
  import limitation. Some Windows temporary-directory tests also encountered
  sandbox `WinError 5`. Ubuntu remains authoritative; no portability change
  was made to research code.
- Initial sample preparation incorrectly validated trade continuity before
  the selected bootstrap boundary. The normalizer now starts sequence checks
  within the selected bootstrap interval. Failed generated sample evidence
  was preserved; the fixed preparation passed the frozen interval audit.
- Acceptance attempt `acceptance-001` was interrupted after a run took
  **63.29 seconds**, exceeding the 60-second target. Peak RSS was approximately
  **37 MiB**. This incomplete attempt is not accepted evidence, even though
  its old report retained `RUNNING` after interruption.
- Profiling found repeated recursive blinding validation in the replay reader
  dominated execution. Full recursive checks now run during preparation;
  the reader enforces explicit root/nested field contracts. Cached BBO updates
  remove repeated whole-book scans. Commit `ee839ac` contains these fixes and
  records interrupted future attempts as `CANCELED`.
- A running Streamlit process retained old imported modules during an update
  and displayed an import error. Restarting only the loopback lab process
  loaded the committed version. No collector service was restarted.

Raw inputs, collector configuration and accepted research artifacts were not
modified. Attempt `acceptance-002` confirmed the performance correction but
was stopped to include final reporting fields and additional contract tests
in the complete matrix. Attempt `acceptance-003` lost its SSH output pipe and
recorded `BrokenPipeError: [Errno 32] Broken pipe`; the collector stayed active.
Its two complete core matrices and completed alpha/entry sweeps remain valid.
The resumable gate revalidates their configurations, dataset and accounting,
then reruns unfinished groups under a detached worker with a local log.
Final acceptance evidence is recorded in
`artifacts/backtest/validation/acceptance-004/report.json`; only a `PASS`
report constitutes acceptance. Full Linux regression at `ee839ac` passed:
**95 tests in 49.23 seconds**.

Final follow-up: **102 Linux tests passed in 47.57 seconds**. Acceptance 004
passed 51 runs after revalidating completed groups. Maximum run time was
33.274 seconds and worker peak RSS was 36.918 MiB. The independent audit
verified 14,103 source-linked fills; final preparation was byte-identical.

## Entry template

### YYYY-MM-DDTHH:MM:SSZ — title

**Component:**  
**Source:**  
**Severity:** info / warning / invalidates interval / invalidates day

#### What happened

#### Exact evidence

```text
paste exact runtime/log output
```

#### Data impact

- start:
- end:
- symbol:
- streams:
- sequence impact:
- reconstruction safe after resync? yes/no/unknown

#### Root cause

known / suspected / unknown

#### Action

#### Exclusion decision

keep / exclude interval / exclude day

#### Follow-up validation

### 2026-09-10 — Roadmap review search command
- A read-only rg command passed docs/current/BACKTEST* as a literal Windows path and returned OS error 123.
- Repeated using directory docs/current with -g 'BACKTEST*'; search succeeded. No runtime or source-data failure. Review only; no simulation executed.

### 2026-09-10 — Research workspace implementation validation
- Native Windows test attempts encountered the existing temporary-directory ACL error; UI tests also lacked Streamlit in the local Python environment. One initial import resolved the old editable checkout until PYTHONPATH was set. Authoritative validation moved to Ubuntu; unrelated Unix resource imports were not changed.
- First isolated Ubuntu full suite: 172 passed, 5 failed, 5 errors. Seven failures/errors came from a missing read-only link to the existing Coinbase raw fixture. Three used an intermediate QA test snapshot (reserved coefficient redeclaration and absent empty MemorySink channel). Linked the existing immutable fixture and transferred corrected tests; no data interval excluded.
- Initial artifact-directory link was created inside an existing tracked directory. Replaced that unused symlink with explicit links to existing samples and historical runs/research/comparisons. Original files remain unchanged.
- Independent QA found same-name scenarios could mix different fee assumptions in comparisons, and the UI bypassed the corrected backend check. Grouping now uses actual execution assumption hashes and explicit strategy-fixed sensitivity; added regression coverage.
- QA also corrected test-only expectations for audit-label differences between built-in and expression strategies. Quote/fill/accounting equality remains required.
- Final validation results and any remaining limitations are recorded in BACKTEST_RESEARCH_WORKSPACE_COMPLETION.md.
- After resuming the session the local SSH tunnel had ended although the VM app was healthy. Restored port forwarding; no server/data changes were needed for that connection issue.
- Browser automation initially clicked while a multiselect popup remained open; dismissed it with Escape and used a filtered keyboard selection. These were UI automation retries, not simulation failures.
- The first cancellation integration trial stopped during warmup. Strengthened the script to wait until trading and require non-null inventory; rerun passed with -0.004 BTC retained and no liquidation.
- Initial commit attempts failed because this worktree had no Git author identity. Used the repository's established recent author identity as command-local Git options; global configuration was not changed.
- Temporary staging-app cleanup first failed because PowerShell expanded a remote readlink substitution locally. Reissued with a literal single-quoted remote command, checked the exact staging PID cwd and stopped only the temporary 8502 app. Release health and collector stayed active.
