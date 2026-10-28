# Research flow release acceptance

Implementation branch: `feat/research-flow-redesign`.
Execution validation code: `656f4f2`; final display release: `292301e` (clean Ubuntu checkouts).

## Scope and acceptance

| Tickets | What was changed | Evidence |
|---|---|---|
| T01/T06 | One history and four pages, durable exact saved draft revision | Catalog symlink/legacy tests, Streamlit save→submit→results→compare→clone and fresh-session tests |
| T02 | Active policy inputs, explicit cost assumptions and cumulative participation-budget meaning | Active-field tests and synthetic policy previews |
| T03/T05 | Shared client-observable lifecycle planner, partial retention/reset, minimum hold and repricing threshold | Both-clock retention tests, size increase/shrink, hard-risk priority |
| T04 | Decision IDs, observable sources, numeric policy stages, order/fill links | Prefix mutation, stage collapse and linked actual sample audit |
| T07 | PnL identity, inventory exposure, representative-order selection and bounded timelines | Independent source/ledger audit and explanation reconciliation |
| T08/T10 | E0–E4 fixed assumptions, one-change follow-ups, explicit no-candidate conclusions, behavioral equivalence | Reversed repeat digests, first divergence reports and stored conclusions |
| T09 | Separate reference-only inventory close evaluation | Long/short, lots, insufficient depth, stale book tests; original ledger preserved |

## Meaningful observation

The default E1/E2 comparison uses a one-lot order (0.001 BTC). An integer-lot engine cannot partially fill a one-lot order, so changing the partial-fill policy can legitimately produce identical desired orders, actions and PnL. The UI now explains this rather than treating a settings difference as a new strategy. A separately labeled 0.003 BTC pair exercises partial fills; its different size is not silently substituted into E0–E4.

The 3-lot pair first diverges at **14:13:09.250 UTC**, order `order-00000091`: `keep_remaining` retains the partially filled sell; `reset_target` requests cancellation for `target_size_changed`. Subsequent desired inventory-limited quantity diverges at 14:13:45.750. Reset minus keep PnL is −0.006600 USDT, decomposed as −0.000200 spread-at-fill and −0.006400 inventory MTM, with zero fee difference. This is the concrete hypothesis→action→accounting example, not a claim that retention is universally better.

| Baseline | Net PnL USDT | Spread at fill | Inventory MTM |
|---|---:|---:|---:|
| E0 no trade | 0.000000 | 0.000000 | 0.000000 |
| E1 join, keep | −1.306050 | −0.028850 | −1.277200 |
| E2 join, reset | −1.306050 | −0.028850 | −1.277200 |
| E3 inventory size | −1.353750 | −0.024550 | −1.329200 |
| E4 flow withdrawal | −0.089150 | −0.001550 | −0.087600 |

All rows use the explicitly labeled **zero-fee diagnostic profile**, same α=0.5 and assumed 10/10/20 ms entry/cancel/report delays on recorded-arrival replay. Funding and endogenous market response remain excluded.

Every tested active baseline can lose money. The acceptance condition is auditable behavior and accounting, not turning the sample profitable. E4's relative result on the same ten minutes is not a future-profitability estimate or paper-candidate approval.

## Commands and regression evidence

- Full Ubuntu suite at `fc60707`: **288 passed**, 2 existing Streamlit/NumPy deprecation warnings, 99.98 s.
- Final result-analysis/UI changes at `656f4f2`: **28 focused tests passed**, 14.97 s, including the two newly added equivalence cases. Earlier full tests were not relabeled as a full 290-test execution.
- Final complete suite at `292301e`: **292 passed**, 2 existing deprecation warnings, 88.64 s. This run includes the readable timeline and verified display-only code-difference guard.
- `git diff --check`: passed.
- `scripts/validate_research_flow.py`: fresh workspace, frozen sample, serial E0–E4 twice (second reversed), separate 3-lot keep/reset pair, independent source/ledger checks and trace links.

Official docs checked in this implementation turn: none newly fetched. The supplied redesign document and local implementation/runtime evidence define this work; no external-model parity claim was added.

## Runtime evidence

Final evidence: [bounded runs](research-flow-runs.json), [real-history UI](research-flow-real-ui.json), [display-only digest proof](research-flow-display-proof.json), [deployment](research-flow-deployment.json). The collector was not reconfigured or restarted. The previous app remains the rollback target. The UI binds only to VM loopback and uses an SSH tunnel; there is no public service or added infrastructure.

The 10 core repetitions passed deterministic digest equality, independent source/ledger auditing and trace checks. Their maximum recorded run wall time was **38.604 s** and process peak RSS **438.786 MiB**, including the process high-water mark after earlier validation work. The separate 3-lot pair also passed source/ledger audit and the observed-action divergence check. Preparation was not rerun: the existing frozen prepared sample was verified and reused.

Initial actual-history AppTest preserved **299 executions**. Final AppTest preserved **300** (269 legacy and 31 workspace), checked all four pages, persisted a separate terminal reference, restored a fresh session and produced **zero navigation-triggered submissions**. The additional same-condition E1 run produced the exact original deterministic digest. Actual Git objects verified the four display/catalog/test-only changed paths; no execution, accounting or analysis implementation changed. A fresh browser also displayed the four menus, E1 decomposition, readable inventory precision and collapsed raw-order evidence.

Final process: PID **445838**, `/home/<vm-user>/lobcap-research-flow-v1-ui`, code `292301e`, loopback port 8501. Collector PID **608**, restart count **0**, active before and after both app switches. Local access health returned `ok` through hidden SSH PID **41440**. The tunnel is a process, not reboot autostart.

## Known limits

- One shared workspace context, not independent authenticated users.
- Ten-minute development sample, assumed fees/private latencies and queue behavior.
- Reference close is not a simulated cancel-and-liquidate path.
- Old artifacts remain readable; missing trace/code compatibility cannot certify a direct comparison.
- Existing HftBacktest accounting discrepancies remain unresolved.
- P4 multi-period evaluation and P5 paper operation remain separate work.

Recommended next action: open an E1/E2 comparison, then the separately labeled 3-lot pair, inspect their first action difference and one linked order, and save the next single-change research question.

## Task completion record

Files changed: policy/config/trace integration, research-flow read model and four-page UI, focused tests, validator, documentation and evidence JSON.

Commands run: Git feature-branch commits/bundle transfer; Ubuntu pytest; frozen-sample validator; real-history Streamlit AppTest; display-only same-digest check; guarded loopback app switch; local HTTP and browser verification.

Official docs checked: no new external documentation fetched during implementation; supplied redesign and repository/runtime evidence used.

Tests passed: final full 292 tests; repeated E0–E4 source/ledger/trace/digest checks; 3-lot policy pair; final display-only digest parity; four-page real-history tests.

Tests failed: zero in the final full suite. Earlier tooling, Windows environment and two staging/test-fixture failures are preserved in `docs/internal/FAILURES.md`; two existing deprecation warnings remain.

Runtime observations: measured core run maxima 38.604 s / 438.786 MiB; collector unchanged; local and VM health verified.

Data written: source and documentation; ten exploratory staging runs; twelve final behavior validations and one display-parity run; append-only research metadata, reference evaluation and small reports. Existing captures, prepared samples and prior execution artifacts preserved.

Open risks: ten-minute sample, uncalibrated execution assumptions, shared workspace context, no live paper implementation or market-response measurement, unresolved external accounting discrepancy.

Recommended next action: review the saved 1-lot and 3-lot policy comparisons and create a single-change follow-up from the corresponding baseline.
