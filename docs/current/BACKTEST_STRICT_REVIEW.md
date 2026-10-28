# Backtest lab strict review — 2026-09-09

The review covered experiment selection, UI state, configuration loading,
comparison interpretation, run discovery, manifest writes, diagnostic claims,
and the existing execution/accounting regression suite. The application now
uses guided Korean pages, with only the selected page rendered.

[Measured completion evidence](BACKTEST_STRICT_REVIEW_VALIDATION.json): final
Ubuntu suite **132 passed in 43.14 seconds** on `aef9b92`.

## Findings and resolutions

| Severity | Finding | Resolution and evidence |
|---|---|---|
| P1 | Sensitivity silently inherited the last successful form submission; visible unsent edits were not its actual base. | Explicit default/saved-run base selection, full active settings table and exact typed restoration. Test injects conflicting old session settings and verifies the selected base wins. |
| P1 | Switching catalogue could retain another dataset's experiment context. | Dataset-scoped state reset; restored configuration checks sample and instrument; saved experiments bind sample ID/checksum. |
| P1 | An impact-cost chart used stressed PnL while the displayed range still used unstressed PnL. | Table, chart, label and range now use the same chosen metric. Actual browser test at 0/1 bps displayed −0.519900/−1.300928 USDT. |
| P2 | A truncated manifest or incomplete run could break every tab, even unrelated setup. | Resilient discovery reports incomplete/corrupt runs separately. Only the requested page reads its analysis payloads. JSON writes atomically replace complete files; simulated replacement failure preserves the previous file. |
| P2 | Ordinary diagnostics claimed source consistency verification without running an independent source audit. | Scope now explicitly distinguishes engine invariants, available source-linked evidence, and a separate independent audit. Historical accepted artifacts are preserved. |
| P2 | Hidden Streamlit widget cleanup discarded the selected run after visiting a page without that selector. | Durable selected-run state separate from widget state; a navigation regression checks older-run selection survives a sensitivity visit. |
| P2 | Saved sensitivity results were only discoverable through current session state. | New UI experiments persist `lab_experiment.json` and have a history selector. File-based restoration checks completion and sample identity. |
| P2 | Irrelevant strategy settings, inactive taker fees, unexplained units and mixed-language internal codes obscured operation. | Strategy-specific controls; basic/advanced separation; Korean explanations, unit examples, order states/events, decision reasons and a glossary. Inactive taker execution is stated rather than presented as an editable cost driver. |
| P2 | Comparison labels omitted influential differences such as participation, and arbitrary settings could be mistaken for a one-variable experiment. | Full configuration-difference table, identical-sample gate and multi-variable interpretation notice. No sign-change interpolation or optimum claim. |
| P2 | Sweep defaults stayed appropriate for alpha when another variable was chosen; inactive, duplicate and excessively many variants were accepted by the UI. | Per-variable examples; strategy applicability; prevalidated 2–24 values; numeric duplicate rejection including `.5` vs `0.50`. Historical configuration hashes remain unchanged. |
| P2 | Extremely fine decision cadence could create an unexpectedly large UI job; sub-lot settings could silently yield no quotes. | UI preflight caps decisions at 60,000/run and rejects order size/inventory limit below one lot. CLI semantics remain available independently. |

No execution formula was tuned to change the sample's profitability. The
review did not establish calibrated own-order fill or endogenous market-impact
accuracy. These remain explicit modeling limits.

## Current user workflow

1. **시작·설정**: choose the catalogue, optionally load a saved configuration,
   choose strategy/fill assumption, enter size/spread/inventory/fee settings,
   then submit. Advanced controls have units and interpretation help. Strategy
   and configuration selection should precede numeric edits.
2. **실행 결과**: the newly submitted run opens automatically. Inspect PnL
   components, fill count/quantity, inventory, drawdown and markout coverage.
   The actual full active configuration is expandable and downloadable.
3. **실행 비교**: explicitly choose 2–12 completed runs. Inspect all setting
   differences before interpreting the PnL difference.
4. **민감도 실험**: explicitly choose a base run or catalogue default, then one
   applicable variable. New experiments persist beyond a browser session.
   Old comparisons without UI metadata remain in their original CSV/artifacts;
   they are not silently relabeled with guessed experiment intent.
5. **주문 추적**: inspect filled orders first, translated lifecycle states,
   exchange versus client timing, then expandable raw queue/source evidence.
6. **근거·용어**: check definitions, limitations, sample validation, provenance
   and bounded file previews/downloads. Technical field names remain in raw
   evidence to preserve reproducibility.

Forms submit values only on their own submit action; this behavior motivated
the explicit experiment-base design. Widget state can be removed when a widget
is no longer rendered, motivating a separate durable selection.
[Streamlit forms](https://docs.streamlit.io/develop/concepts/architecture/forms),
[widget lifecycle](https://docs.streamlit.io/develop/concepts/architecture/widget-behavior).

## Validation and reproduction

The full Ubuntu suite passed after the first revision (129 tests), then after
history/workload guards (131 tests), and finally **132 tests** including the
selected-run navigation regression. The focused tests exercise form parity, no execution during
navigation/strategy selection, explicit sensitivity base, dataset reset,
corrupt run isolation, atomic-write failure, restored history and numeric
duplicate handling.

Real browser validation covered setup layout, actual default submission,
automatic result navigation, translated filled-order timeline, a two-value
impact-cost experiment and persisted history. Saved-history rendering was also verified after restarting the app
and opening a fresh browser session. The default run was rerun through
the CLI; both recorded-arrival and original v1 digests are unchanged:

```text
recorded: fe87ef329ae21dca8ea8d2efff655db5a07877b28e7d333aa7b3cf556389ef2a
v1:       f723f938e9ba732896202fba7d1948d9b9da9f9f02aa94c235c6dc52fd21396d
```

The new browser run took 10.132 seconds with 175.199 MiB process peak RSS.
The recorded CLI rerun took 9.655 seconds / 38.746 MiB; the v1 rerun took
7.656 seconds / 38.746 MiB. These checks also reconciled ledger identities,
client-reported inventory and reservations. The collector remained active.
The earlier 41-run source audit was not represented as newly rerun; its
accepted artifacts and provenance are retained separately.

```bash
cd /home/<vm-user>/lobcap-mvp
.venv/bin/python -m pytest -q
.venv/bin/python -m lobcap.backtest run --config configs/backtest/binance_recorded.yaml
.venv/bin/python -m lobcap.backtest run --config configs/backtest/binance_demo.yaml
```

## Task completion record

- **Files changed:** `apps/backtest_lab.py`, new `backtest/presentation.py`,
  atomic `backtest/replay.py` JSON helper, diagnostic scope text, UI/presentation
  tests, failure log and review documentation.
- **Commands run:** compile checks, Ubuntu full pytest, real browser submissions,
  exported-config/legacy CLI reruns, Git synchronization and app/collector health.
- **Official docs checked:** Streamlit forms and widget lifecycle, linked above.
- **Tests passed:** full suite and bounded parity checks described above;
  exact final counts are in the adjacent compact evidence.
- **Tests failed:** no unresolved failures; development duplicate-value,
  selection-state and connection findings are retained in `docs/internal/FAILURES.md`.
- **Runtime observations:** existing Ubuntu worktree and loopback Streamlit app;
  only the lab process was restarted. Local SSH helper had exited and was
  restarted. It must remain running; it is not a newly installed service.
- **Data written:** new immutable validation runs, the two-value comparison,
  `artifacts/backtest/validation/review-001/report.json`, and compact committed
  review evidence. New UI history metadata resides beside its comparison CSV.
- **Open risks:** L2 queue/own-fill realism and impact remain uncalibrated;
  funding/hedging/liquidation absent; one fixed ten-minute interval cannot
  establish strategy robustness. No research portability patch was made.
- **Recommended next action:** use the updated guided workflow and inspect
  active settings before comparing results. Independent periods and private
  own-order observations would be separate future validation work.
