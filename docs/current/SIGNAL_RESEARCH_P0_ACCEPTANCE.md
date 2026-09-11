# Signal Research multi-day prerequisite acceptance

**PASS**: all 19 measured phases completed and the standalone validator verified
cache checksums and report consistency. This closes the P0-A pipeline completion
gate, not the signal promotion or strategy research gates.

## Implementation boundary

The architecture audit used GitHub branch `feat/signal-research-v1` at
`5786966fddc1b87f68253c3f9064fb64207ea8c8`. This implementation extends its
existing signal cache, labels, predictive analysis and runner. Replay, execution,
accounting, queue/fill models, GLFT and strategy comparison are unchanged.
No main changes, collector changes, app deployment or paper promotion occurred.

Runtime code: `4f309dfdb3b95d76c4d4400297d9ef5dd3436b93`.
The new validation checkout is `/home/0woo/lobcap-signal-p0-acceptance` on the
existing data host, with read-only use of existing prepared samples. The user
explicitly approved this transfer and DEVELOPMENT validation.

## Regression and provenance

- Ubuntu full suite: **410 passed**, 106.01 seconds. Two existing Streamlit/NumPy
  deprecation warnings; no failing tests.
- Local focused regression: **60 passed**. Coverage censoring, cache provenance,
  delayed delivery/prefix causality, runner acceptance and exact optimized-vs-
  reference statistics are covered.
- Catalogue SHA256:
  `cc676289c752418edb37ad9adc650dd4e6c611d19bd1bf4170f15fde6a765aae`.
- Opened roles: DEVELOPMENT only, September 7 and 8, eight fixed 20-minute
  windows with five-minute warmup. Validation and pilot OOS remain locked.
- Analysis grid: 100 ms; horizons: 100, 500, 1000, 5000 ms; three separate label
  families; POST_LATENCY uses the fixed 10 ms scenario, not measured fill latency.
- Freshness policy: `development-freshness-9511afafe9451d24`; maximum anchor age
  199 ms and target age 97 ms at every horizon, frozen from worst-window p95.

## Coverage finding and compatibility

The partial `723529e` run exposed available microprice values outside known
reconstructed depth, while imbalance rejected those rows. Labels also used
unknown midpoint coverage. That run was stopped during the second feature group
and its `development-final` directory retained as diagnostic evidence only.

Feature definition v3 enforces registered depth requirements, persists client
midpoint coverage and excludes unknown anchor/target coverage before freshness.
At 1 s MARKET_TRUTH_RETURN, raw coverage exclusions are 1,049 in September 7
00:10, 2,545 in September 8 06:10, and 9,202 in September 8 18:10 (12,000 rows
per window). The last window has only 2,788 raw observed labels before freshness.
The catalogue's reconstruction PASS does not guarantee complete BBO coverage
throughout a prepared window. No excluded observations are silently replaced.
For imbalance_1 at 1 s MARKET_TRUTH_RETURN, the final paired population of that
window is 2,666/12,000 (22.22%) after feature availability and label freshness.

The v2/v3 populations differ: changes in their IC are not evidence of improved
prediction. Raw midpoint/age controls remain observations with explicit midpoint
coverage metadata. Rolling OFI/return history quality requires separate review
before strategy promotion; endpoint label censoring is not a proof of full
lookback or forward-path coverage.

## Runtime evidence and descriptive results

Output on the data host:
`/home/0woo/lobcap-signal-p0-acceptance/artifacts/backtest/research/development-coverage-v3`.
The report SHA256 is
`e68f5148ceea431b782fb75af81402e319a8c9d4673023daa58d0b0b86bd65ff`.
The adjacent acceptance, progress and freshness artifacts preserve the full
checks and measured phases; `SIGNAL_RESEARCH_P0_VALIDATION.json` records the
compact verification evidence in this repository.

Total elapsed time: 38 min 29.66 s, exit 0, no swap. The first feature group
completed in 729.03 s at 459.16 MiB RSS; the second in 532.82 s at 393.55 MiB.
The largest worker was **459.16 MiB**, below 512 MiB. The systemd unit's 524.3 MiB
peak includes parent and worker processes and is a different measurement.
Execution used MemoryMax=768M, CPUQuota=100%, Nice=10, RuntimeMaxSec=7200.
Collector PID 517216 remained active, NRestarts=0 before and after the run.

The following 1 s MARKET_TRUTH_RETURN rows illustrate day-level differences;
they are descriptive DEVELOPMENT observations, not a newly selected endpoint.

| Feature | Sep 7 coverage | Sep 7 Pearson IC | Sep 8 coverage | Sep 8 Pearson IC |
|---|---:|---:|---:|---:|
| microprice_minus_mid_ticks | 0.93219 | 0.19282 | 0.72006 | 0.22915 |
| imbalance_1 | 0.93219 | 0.21499 | 0.72006 | 0.23437 |
| imbalance_5 | 0.93219 | 0.21488 | 0.71788 | 0.23459 |
| vamp_5_minus_mid_ticks | 0.93219 | 0.16869 | 0.71788 | 0.18825 |
| bbo_ofi_1000ms | 0.93219 | 0.12065 | 0.72010 | 0.13153 |
| mid_return_1000ms | 0.93219 | 0.05815 | 0.72010 | 0.08849 |
| trade_volume_imbalance_1000ms | 0.67923 | 0.07436 | 0.59823 | 0.09179 |

Microprice and imbalance_1 have pairwise Spearman 0.999962 (83,322 jointly
available rows). Their definitions satisfy microprice-minus-mid = half-spread
times imbalance_1, so they are not independent evidence. Imbalance_1 and
imbalance_5 are also highly redundant (Spearman 0.990548). Bucket monotonicity
for the 1 s trade-imbalance and mid-return examples is not consistent even
though their Pearson ICs are positive. No statistical significance or paper
eligibility is claimed. Two development dates do not resolve generalization.

## Next research gates

Pipeline acceptance establishes artifact integrity and report consistency; it
does not independently recompute all statistics, prove profitability or promote
any signal. Coverage quality and day/label stability must be reviewed first.
Study 1 can reuse fixed_spread and inventory_skew for MM-0/MM-1, followed by
paired multi-day orchestration and causal runtime parity before BP integration.
VPIN still needs its own feature/toxicity evidence. Study 2 movement/excursion
research can reuse the offline foundation; ladders require the audited existing
execution changes for order identity, shared volume budgets, priority and risk
reservation, with single-level regression. Neither study's strategies are
implemented by this prerequisite change.
