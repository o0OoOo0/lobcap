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

### 2026-09-10 — HftBacktest documentation review tooling
- Read-only broad rg encountered existing temporary-directory ACL denials; narrowed searches to the active checkout. A literal wildcard path also caused Windows OS error 123; used directory searches with -g.
- Windows default python resolved the Store alias. Used the bundled Python runtime. BeautifulSoup was unavailable; the crawler was rewritten with the standard library.
- The documentation crawl wrote all 45 successful records before console title output hit cp949 UnicodeEncodeError. Verified the persisted UTF-8 inventory instead; no downloaded page was lost or excluded.
- A guessed contents.html URL could not be opened. Crawled actual linked latest documentation instead. No engine tests, market-data changes or service operations were performed in this review.

### 2026-09-10 — LP engine hardening v2 validation
- Default sandbox SSH resolved a deny shim; used the approved escalated SSH tool path. Read attempts for guessed workspace/ui.py, test_workspace.py and conftest.py found no file; located actual modules with rg.
- Bundled Python lacked pytest. Focused Windows tests used the existing venv but temporary-directory ACL errors prevented tmp_path cases; authoritative validation moved to Ubuntu without research portability edits.
- First isolated Ubuntu suite: 235 passed, 3 failed, 2 errors. Four failures/errors required frozen virtual_truth fixtures; one mock run lacked the newly required manifest. Updated the mock and supplied existing immutable fixtures.
- Second suite: 237 passed, 1 failed, 2 errors. Symlink-resolved truth paths violated the research repository-relative provenance contract; replaced only the staging symlink with a copied fixture. The affected 25 research tests then passed.
- A transient local patch indentation error in the test mock was corrected before transfer/testing.
- Initial external HftBacktest 2.4.4 comparison failed two partial-fill position cases. Added separate order-leaves evidence; order execution matches, while position remains zero. Retain ACCOUNTING_DISCREPANCY, not parity PASS.
- Validation agent source URL guesses returned 404/safe-open errors; used actual linked published source. No source issue was hidden or converted to a passing external comparison.
- A read-only glob for staging data paths matched no directory; existing sample links were found under artifacts/backtest instead.
- External partial-then-full follow-up also failed portfolio reconciliation: executed 0.003, reported position 0.002 on both sides. All 16 external order quantities meet their explicit expected contracts; four portfolio checks remain failed.
- An SSH transport reset interrupted an artifact transfer and the waiting session after the 24-run validator had completed. Confirmed the persisted report was PASS, retransferred only the partial temporary bundle, and enabled connection/keepalive timeouts.
- Local Get-NetTCPConnection stalled and was canceled. A bounded HTTP check found the old SSH forward absent; restored forwarding and verified local health returned ok. No collector restart or data loss was involved.
- Final clean-release Ubuntu suite: 249 passed; compileall and pip check passed. Twenty-four core runs and four workspace runs passed independent audits and reverse-order digest checks. Streamlit deprecation warnings remain nonfatal.

### 2026-09-10 — Local app access recovery
- User could not open the deployed app. Local health check failed with connection refused; VM 8501 health returned ok and app logs showed a running server. The tool-owned SSH tunnel had ended.
- Started a separate hidden Windows OpenSSH process (PID 21104), with connection timeout, forwarding failure detection and keepalives. Verified local health ok and HTTP 200. No app or collector restart.
- This connection is a background process, not reboot autostart. Machine restart or network failure can still require reconnecting.

### 2026-09-10 — Research flow redesign validation
- Initial tooling reads included a nonexistent run path and an incorrect virtual-truth tar path; corrected to `artifacts/virtual_truth`. No input artifacts were changed.
- Two agent patch attempts used duplicate delete/add targets and were rejected before modification; corrected to update operations.
- Initial local analysis tests imported an older editable checkout; corrected `PYTHONPATH`. Windows temporary-directory ACLs blocked five setups and symlink creation; local elevated rerun passed supported cases, and authoritative coverage moved to Ubuntu. Native Windows research portability was not changed.
- Local UI environment lacked Streamlit; used the existing Ubuntu application environment. Initial Ubuntu focused tests passed 32 cases; initial app/workspace UI tests passed 12 cases, with two existing NumPy/Streamlit deprecation warnings.
- Review found partial retention incorrectly accepted an increased target, and stage/divergence comparisons treated reason-only changes as behavior changes. Corrected numeric comparisons and unchanged-target semantics, with focused regression tests.
- A follow-up patch expected an earlier terminal-reference UI line and was rejected; reread the current code and applied the corrected context. A UI-agent PowerShell regex command was rejected as an unclosed group; plain source reads recovered the inspection.
- Local SSH-forward health returned connection refused again. Restored a hidden OpenSSH process (PID 41440), verified local health `ok`; no collector restart.
- Actual E1/E2 one-lot baselines had identical behavior. This is expected under integer-lot fills, not a failing fill model. Preserved that result and separately exercised 3-lot partial retention/reset with observed action divergence.
- Browser review exposed raw JSON as the default order timeline and excessively long numeric display. Replaced the default view with compact UTC/event/quantity rows and moved original rows into an audit expander. Execution outputs remain immutable.
- Final display-checkout full suite initially reported 290 passed and 2 failed. One comparison was correctly blocked because the top-level data symlink was untracked (Git directory-only ignore rule); replaced only that verified symlink with an ignored real directory and child links, preserving all targets. The legacy timeline assertion used a fixture with a stale initial book and therefore no orders; explicitly raised that test's staleness limit to exercise actual order rows. No simulator rule was relaxed.
- Final clean-checkout rerun: 292 passed, two existing deprecation warnings. The additional real-sample E1 display-only run matched the original deterministic digest exactly. Final real-history UI checks and guarded deployment passed; collector PID 608/restart count 0 remained unchanged.

### 2026-09-11 workbench validation
- Initial commit attempt failed because this checkout had no Git identity; reused the existing commit identity with command-scoped configuration, then rebuilt the bundle. The initial VM clone was still the prior commit and was fast-forwarded before tests.
- New FIFO tests initially had 12 assertion failures because the expected fee ratio used Decimal precision 28 while the reviewed ratio uses 50. Exact monetary reconciliation already passed. Aligned expected ratio context to 50; no accounting change.
- Read-only inspection attempted three nonexistent test/module filenames; corrected to the actual repository paths. No code or data was changed by those failed reads.

- Real-history UI assertion initially expected four decimal places while the UI correctly rendered six; changed validation to compare Decimal values. Rerun passed for negative and positive outcomes with no job submissions.
- Local SSH health found the prior tunnel absent. Restored a hidden loopback-only forward (PID 32120); VM app was healthy. Collector remained PID 608, NRestarts 0.
- Whole Ubuntu suite passed 312 tests with two existing Streamlit/NumPy warnings. Removed the new chart's deprecated width argument during final visual review.


### 2026-09-11 GLFT research policies
- An inspection command used a Windows wildcard path unsupported by rg; repeated with actual filenames.
- An intermediate comparison-guard patch introduced incorrect indentation. compileall detected it before transfer; corrected it and recompiled successfully.
- Initial GLFT and workbench focused tests: 32 passed. Full Ubuntu regression: 324 passed with two existing Streamlit/NumPy warnings. No unresolved test failures at this stage.

### 2026-09-11 Signal Research Lab foundation
- The first focused Windows test command omitted `PYTHONPATH=src`, so collection stopped before loading project code. Repeated with the repository source path.
- The first label-age fixture expected 0/10/20 ns, while its midpoint series actually produced 10/20/30 ns target ages. Corrected the fixture expectation; the implementation already preserved the measured ages.
- Windows pytest temporary-directory ACLs also blocked the cache fixture and session cleanup even with a workspace-local `--basetemp`. Eighteen non-temporary focused cases passed; the locked tool-created directory was removed with its verified absolute path and cache coverage was moved to authoritative Ubuntu validation.
- The first isolated Ubuntu command referenced a guessed `.venv` inside the GLFT release. The release uses `/home/0woo/lobcap-mvp/.venv`; the corrected command ran 49 focused and related regression tests successfully.
- The first real-sample 100 ms foundation validator exceeded the 60-second target and was interrupted. Every feature point redundantly copied 64 recent source references and every snapshot re-sorted the full book. Reduced provenance to the latest source refs for the relevant depth/trade family and cached top-10 levels between depth deliveries before rerunning.
- Interrupting the SSH sessions did not initially terminate their remote validator children; two exact tool-started PIDs remained and contended for CPU. Sent TERM only to PIDs 496348 and 496659, verified both exited, and confirmed the collector remained active and Streamlit health returned `ok`. Replaced slow high-precision Decimal logarithms on every 100 ms return snapshot with the repository's established float-log/Decimal-output convention.
- A clean single-process full-window rerun still reached the explicit 90-second timeout and produced no evidence file. Added a diagnostic prefix limit and separate feature/truth/label timings so the next optimization is based on measured stage cost rather than another guessed change.
- After the streaming cadence change, the default Windows Store `python.exe` alias printed only `Python` and exited before pytest started. Kept Windows portability out of scope and used the installed Python launcher/authoritative Ubuntu runtime for validation.
- Windows reruns through `py -3.12` passed all non-temporary Signal/engine cases (19, then 32) but the known locked pytest temp root blocked one cache setup and two artifact setups. No product assertion failed; authoritative temporary-file coverage remained on Ubuntu.
- The first complete streaming validation passed but took 126.3 seconds at 43.8 MiB peak RSS. Stage evidence isolated 119.2 seconds in feature/label replay; inspection found full-book sorting for every invalidated top-depth snapshot, so `MarketBook` received indexed best/top-level access before the acceptance rerun.
- The first full-suite run in the isolated Signal checkout reached 338 passes, then reported two failures and five setup errors because its untracked `raw_http_slice.txt` fixture link had never been created. Linked that single path to the existing immutable `/home/0woo/lobcap` fixture and reran; no research artifact content was modified.
- Two direct full-suite reruns lost their SSH connection near the one-minute mark, so their final summaries were unavailable and no pytest child remained. Ran the same suite as a one-shot user transient unit and read its exit status/journal separately: 345 passed with two pre-existing Streamlit/NumPy deprecation warnings.
- The first requested Git push tool invocation had a malformed local output-token argument and never launched Git. Corrected the tool call; no repository or remote state changed in the failed attempt.
- The first strategy-comparison test selection assumed another module's local `sample` fixture was globally available; two new setups failed before product code ran. Imported the established fixture explicitly. The remaining selected setups hit the already documented Windows pytest temp-root ACL and were moved to Ubuntu.
- The post-implementation VM bundle transfer was rejected by automatic approval review because remote validation did not explicitly authorize exporting a private code payload. Did not fetch or transfer by another route; used an elevated local pytest run, which passed 28 selected strategy/research/workbench tests.
- The first three-policy end-to-end test failed after its first run while writing a nested attempt record because the generated Windows pytest path exceeded the legacy path limit. The completed run itself was valid. Shortened only the test output root and reran; production paths and simulator semantics were unchanged.
- The full Windows `tests/backtest` run reached 286 passes and one skip but five existing workspace job tests failed after long nested temp paths caused attempt-record writes to fail; downstream assertions then saw failed/incomplete jobs. The new strategy suite's shortened-path end-to-end test and the 30-test focused regression passed. Per the repository runtime policy, unrelated Windows path portability was not changed.
- Automatic approval review rejected the final Git-bundle upload to the existing GCP VM because this turn did not explicitly authorize exporting potentially private repository contents. Did not retry or route around the rejection; retained authoritative Ubuntu evidence at `f49ec7a` and locally verified the final structural-update refinement and documentation.

### 2026-09-11 strategy comparison review
- Found scorecard provenance/capital guard bypass, summary-versus-ledger mismatch acceptance, missing values silently becoming zero, random-ID tie ranks, unrelated-job grouping, sensitivity routed through strategy ranking, and form-dependent disabled strategy selection. Added focused adversarial regressions and corrected these paths.
- A sandbox read of elevated pytest artifacts hit ACL denial; read-only elevated inspection succeeded. A combined patch with an incorrect FAILURES.md anchor failed before edits; reapplied with actual context.
- First short-path complete backtest regression: 301 passed, one symlink-permission skip, one legacy navigation failure while running with dirty-code provenance. Five previous long-path job failures did not recur. Clean-checkout rerun is recorded separately after committing the reviewed implementation.
- Clean commit e67016b rerun: 38 passed; the legacy navigation test failed in local Streamlit 1.37.1 AppTest with a missing widget SessionState key at the page transition. It remains unresolved; the initial dirty-code explanation did not fully account for it. No full-pass claim is made. A subsequent rg Windows wildcard-path inspection failed; it did not change files.

# 2026-09-11 — Signal research VM bundle preflight

- Command: remote `git bundle verify /tmp/signal-research-v1.bundle` before selecting a repository.
- Failure: `error: need a repository to verify a bundle`.
- Impact: none; the upload completed, but the combined preflight stopped before later read-only checks.
- Resolution: rerun verification from an existing Git checkout and run the remaining checks without fail-fast coupling.

# 2026-09-11 — PowerShell expansion in remote symlink setup

- Command: SSH command containing a remote Bash `for name ... $name` loop.
- Failure: local PowerShell expanded the remote loop variable, producing a Bash syntax error near `done`.
- Impact: none; Bash rejected the command during parsing, before creating links.
- Resolution: use explicit, variable-free `test -e || ln -s` commands for each approved artifact link.

# 2026-09-11 — Existing Streamlit did not exit within five seconds

- Command: send `SIGTERM` to deployed Streamlit PID 486876 and poll for five seconds.
- Failure: the process still answered `kill -0` at the deadline, so the guarded command exited before starting the replacement.
- Impact: a short UI deployment interruption may have begun; the collector was not targeted.
- Resolution: inspect the exact process and port state, then complete the replacement only after confirming port 8501 is free.

# 2026-09-11 — PowerShell expansion in final app cwd check

- Command: remote cwd check using `apppid=$(systemctl ...)` inside a PowerShell SSH command string.
- Failure: local PowerShell attempted to execute `systemctl`; health, unit state, commit, and collector checks still ran, but cwd printed as `/proc/cwd`.
- Impact: deployment health was established, while the cwd evidence from that invocation was invalid.
- Resolution: repeat the read-only cwd check with the explicit observed PID 512390.

# 2026-09-11 — In-app browser CUA initialization failure

- Command: initialize browser control for `http://127.0.0.1:8501/` after deployment.
- Failure: `failed to write kernel assets: path not found (os error 3)`.
- Impact: automated screenshot-level inspection did not start; HTTP health, tunnel connectivity, Ubuntu tests, and Streamlit test coverage remained available.
- Resolution: reset the CUA session and retry once; do not use stale UI handles.
- Retry result: the reset session failed with the same missing kernel-assets path, so visual automation was stopped. The deployed page remains reachable through the verified local tunnel.

# 2026-09-11 — PowerShell parsing of remote Python progress probe

- Command: SSH `python -c` expression containing nested dictionary indexing and quotes.
- Failure: local PowerShell raised `ParserError: Missing argument in parameter list` before SSH execution.
- Impact: none; the running strategy-suite worker was unaffected.
- Resolution: read only anchored top-level JSON fields with a simple remote `grep` command.
- Follow-up: the alternation regex was also parsed locally because of nested quoting. Switched to an unquoted remote `tail` with no expressions.

# 2026-09-11 — Missing local Git author identity

- Command: commit deployment evidence and the bounded strategy-suite validator.
- Failure: Git reported `Author identity unknown` after staging the files.
- Impact: no commit was created and nothing was pushed; the intended index remained staged.
- Resolution: copy the existing repository convention (`Codex <codex@openai.com>`) into repository-local Git config and retry the commit.
# 2026-09-11 — Signal next-steps review tooling

- Initial source reads guessed a nonexistent `research_flow/signal_lab/` package;
  the implementation is directly under `research_flow/`. Correct paths were
  enumerated with `rg --files` before inspection.
- A guessed `docs/current/02_DATA_LAYOUT.md` path did not exist. Runtime layout
  was established from the current GCP deployment document and read-only `/data`
  inventory instead.
- The first VM date search used filename patterns that did not match the actual
  Hive-style directories and returned no records. No absence conclusion was
  drawn; `/data/market=futures/symbol=btcusdt/kind=.../date=...` was then
  inventoried directly.
- A nested PowerShell/SSH `sed` regex intended to count lifecycle event types was
  parsed locally and never executed. File counts and sample lifecycle rows were
  retained; reconnect/error quality remains explicitly unverified.

# 2026-09-11 — Signal Research P0 implementation tooling

- The first batched inspection call contained malformed JavaScript and failed
  during parsing before any nested tool ran. Reissued the reads with valid syntax;
  no files or processes were affected.
- `python -m pytest` resolved to a nonfunctional Windows app alias and returned
  only `Python`. `py -m pytest` then collected without the src layout on
  `PYTHONPATH` and reported five import errors. Re-ran with `PYTHONPATH=src`.
- Windows pytest could not enumerate its user temp root, and a repository-local
  `--basetemp` inherited the same ACL denial. Tests without `tmp_path` passed;
  all temp-backed and full-suite judgments are deferred to authoritative Ubuntu.
- One combined acquisition-segment patch used a whitespace-mismatched manifest
  anchor and was rejected before editing. Reapplied the change in smaller exact
  patches.
- An inspection guessed `configs/backtest/base.yaml`; the repository uses
  `binance_demo.yaml` and `binance_recorded.yaml`. The catalogue builder now
  defaults to the existing recorded-clock config.
- The sandboxed GitHub push could not reach the configured local proxy. The
  approved network retry pushed the same commit successfully.
- The first Ubuntu focused-test command assumed a checkout-local `.venv`; the
  deployed unit uses `/home/0woo/lobcap-mvp/.venv`. The Git fast-forward had
  completed, and rerunning with the inspected interpreter passed 93 tests.
- The first full Ubuntu suite found three synthetic scorecard fixtures without
  the newly mandatory scheduler identity (375 passed, three failed). Production
  comparison correctly failed closed. Added the identity to those fixtures and
  reran the affected tests before repeating the full suite.
- The first catalogue worker constructed `SampleSpec` positionally, placing the
  arrival-normalization string in `schema_version` and leaving normalization at
  v1. Inspection caught this after one PASS window; stopped the dedicated worker,
  quarantined its two generated sample directories and catalogue output, changed
  construction to explicit keywords, and added a field-order regression.
- A catalogue resource probe again let local PowerShell expand a remote Bash
  command substitution. It printed the worker PID but gave `ps` an empty value;
  the immediate retry used that explicit PID and changed no state.
- The first Streamlit health probe ran immediately after `systemctl restart` and
  reached port 8501 before the process was ready. A five-second retry returned
  `ok`; the collector remained active with zero restarts.
- A Windows `rg` command used a Unix-style wildcard in a path argument and
  reported an invalid path syntax error while the independent app read succeeded.
  Subsequent searches named files explicitly.
- A post-catalogue `grep` command over-escaped quotes through PowerShell and
  produced `Trailing backslash`; the preceding systemd journal already provided
  the authoritative 16 PASS / 0 EXCLUDED count and catalogue hash.
- The first DEVELOPMENT study reached about 393 MiB cgroup peak during initial
  cache work. Static review showed the later redundancy phase would retain full
  seven-feature snapshots and risk the 512 MiB limit. Stopped it before that
  phase, quarantined 6.1 MiB of partial output, and replaced the retained objects
  with compact value tuples before rerunning.
- The compact-redundancy rerun still reached 649 MiB at cache 6/8 because the
  cache/cadence phase retained every window's truth series and CPython kept freed
  arenas. Stopped at the measured breach, quarantined 31 MiB of partial output,
  and changed the runner to retain only paths/segment IDs, reload truth per
  feature, collect garbage and call Linux `malloc_trim` between bounded phases.
- A child-process-isolated rerun showed that window 6 still reached about
  529 MiB RSS because cache writing and cadence/truth measurement shared one
  child. Stopped and quarantined it, then split each window into separate
  `cache_write` and `cadence` children with independent recorded RSS gates.
- The first child RSS probe used a stale guessed PID after reading the new parent
  PID, so `ps` returned no process and the SSH command exited 1. Retried with the
  exact reported PID and made no state change.
- Splitting cache and cadence into separate children exposed the remaining peak:
  the sixth cache writer reached 530.6 MiB RSS and failed the explicit 512 MiB
  gate. Quarantined the partial study and changed the cache contract to persist
  only the preregistered signals and required controls, with the exact projection
  included in the cache identity.
- A sandboxed Git push again inherited the unavailable local proxy and failed;
  the approved network retry pushed the identical commit.
- The first projected-cache study invocation guessed `catalogue.json`, while the
  sealed output is `catalogue_manifest.json`. It failed before creating the study
  directory; the corrected invocation uses the inspected path.
- Persisting fewer feature columns did not reduce the sixth cache worker below
  the limit: it still reached 529.7 MiB. Inspection found stale priority-heap
  entries accumulating when deep levels were repeatedly deleted and re-added;
  added bounded heap compaction and a churn regression test.
- Heap compaction did not resolve the cache peak; another dense window reached
  about 529 MiB before completion. The retained objects were queued cross-stream
  deliveries: exchange-time iteration could read a delayed stream far ahead of
  client time. Replaced the production cache path with a lazy two-stream merge
  over independent file iterators, retaining only one lookahead per stream while
  preserving per-stream order and `(delivery time, replay index)` tie-breaking.
- The first bounded-delivery run completed all eight caches under the memory
  limit, then exposed redundant work in seven separate feature children: each
  rebuilt the same eight truth series. Stopped during the first feature, kept the
  partial run quarantined, and combined preregistered feature evaluation and
  redundancy into one bounded child that reads each cache/truth input once.
- The all-feature child reached about 565 MiB RSS, confirming that seven complete
  `FeaturePoint` sets cannot coexist under the 512 MiB worker target. Stopped it
  and split evaluation into preregistered groups of four and three; redundancy
  again uses compact value tuples in its own bounded phase.

# 2026-09-12 — Multi-day prerequisite completion

- Read-only inspection found the 5786966 worker stopped, eight feature caches
  and freshness evidence retained, but no development_report.json. The journal
  records a stop at 15:43 UTC; it does not establish an OOM failure for that run.
- Two multi-file editing attempts were rejected before applying because one
  reused a path in delete/add operations and another used a mismatched document
  anchor. Reissued exact patches; no runtime inputs were affected.
- A broad initial journal query included unrelated telemetry errors. Narrowed
  the query to the signal worker; telemetry configuration was not changed.
