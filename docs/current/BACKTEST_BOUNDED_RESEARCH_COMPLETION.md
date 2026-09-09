# Bounded research delivery — 2026-09-10

Implemented on `feat/backtest-mvp-v1`, implementation commit `5ea375a`. This is the 10-minute development workflow described in [BACKTEST_BOUNDED_RESEARCH.md](BACKTEST_BOUNDED_RESEARCH.md), not completion of the multi-week paper selection roadmap.

## Measured validation

- Ubuntu full suite: **144 passed in 52.22 seconds**. Existing Unix-only research code was unchanged.
- Frozen 2026-09-06 14:10–14:20 UTC arrival-v2 sample: **24 scenarios repeated = 48 runs**, identical deterministic digest `0af1747f524a65105a0c789f85ffecf36ef033a3505a9af54da86f4265787b6d`.
- First experiment source audit: **2,883 maker fills and 640 taker level fills**. Checked source references/price/side/time, maker lifecycle and volume cap, hedge reduce-only and non-reuse of published depth, ending inventory.
- Largest measured run wall time **33.951 seconds**, maximum process RSS recorded in run manifests **43.664 MiB**. Total run output **29,854,266 bytes**. Preparation was already complete; these are replay measurements.
- Real saved-result Streamlit AppTest: gates, exploratory ranking, full results and four downloads rendered. Form uses the same plan builder as CLI and does not run on navigation.
- Legacy exchange and recorded golden digests both unchanged; see [validation JSON](BACKTEST_BOUNDED_RESEARCH_VALIDATION.json).
- Localhost health responded `ok`; collector active before and after. Only the identified Streamlit process was restarted.

Experiments are stored on Ubuntu at:

```text
/home/0woo/lobcap-mvp/artifacts/backtest/research/485e447f02c7-d68a8194
/home/0woo/lobcap-mvp/artifacts/backtest/research/485e447f02c7-d7f311bb
```

| Baseline strategy | Net PnL USDT | Maker fills | Hedge executions |
|---|---:|---:|---:|
| fixed_spread | -5.04298374 | 114 | 33 |
| inventory_skew | -5.36291526 | 119 | 35 |
| microprice | -5.19186760 | 113 | 34 |
| flow_guard | -0.04989788 | 1 | 0 |

These are **research assumptions with 2/5 bps maker/taker costs**, not measured account execution. The flow guard's lower loss comes with only one maker fill and is not evidence it is a superior strategy. Some TOUCH_FILL runs hit the 10 USDT sample-loss kill; cancel latency and hedging costs leave loss above the trigger, correctly preserved. All exploratory strategies fail the declared criteria; paper candidates are empty independently because OOS/calibration/mechanics evidence is missing.

## Provenance and limits

The runtime validation used the Ubuntu working tree based on `3b5c7c2`. Auto-review rejected a `git restore` command during optional Git synchronization, citing possible loss of uncommitted work. The rejected command did not execute. No override/workaround overwrite was attempted. A subsequent read-only comparison of **34 source/test files** against fetched commit `5ea375a` found no content differences after CRLF/LF normalization. The Ubuntu HEAD remains `3b5c7c2` with implementation edits preserved. Local source is committed.

Browser automation could not initialize its kernel assets (OS error 3). UI evidence is Streamlit AppTest and HTTP health, not a manual browser walkthrough. Public hosting was not performed.

Funding/mark price/margin tiers/minimum-notional history, real liquidity impact, request-rate limits, multi-day evaluation, parameter optimization, L3 calibration and live paper are not implemented by this bounded delivery. `handoff.json` explicitly prohibits paper promotion. The validation report must not be used as a paper-readiness certificate.

## Task completion report

- Files changed: research orchestrator, research control/hedges, UI adapter, role-based accounting, opt-in lot enforcement, volatility strategy, focused tests, validation scripts, docs and README/FAILURES.
- Commands run: source inspection; targeted and full Ubuntu pytest; `validate_backtest_research.py`; `verify_backtest_research_ui.py`; source-content comparison; HTTP health and collector status; feature-branch commit.
- Official docs checked: none newly required during this implementation; no new exchange API or order endpoint was called.
- Tests passed: final 144 Linux tests; 48 real runs; source audit; UI and legacy goldens.
- Tests failed: intermediate Windows temp/cache ACL errors and a synthetic guard fixture were resolved as documented in FAILURES.md. Final Linux checks passed.
- Runtime observations: loopback Streamlit healthy, collector active, risk kill and conditional hedge exercised on the original sample.
- Data written: new research/run artifacts, local validation records, implementation files. No raw data or accepted research artifacts overwritten.
- Open risks: fixed-path simulation assumptions; missing real futures/long-period evidence; Ubuntu Git metadata not advanced; native browser automation unavailable.
- Recommended next action: inspect the new research page and download the HTML report for independent review. Keep paper promotion blocked until the missing evidence and broader roadmap gates are implemented and validated.
