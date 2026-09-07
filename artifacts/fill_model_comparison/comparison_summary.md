# Prompt 5D — Blinded L2 fill-assumption comparison

This is a one-minute correctness experiment against frozen Prompt 5C 
**L3-derived shadow-fill labels**, not absolute market-impact ground truth.
The alpha grid is not calibrated; `alpha=0.50` is an uncalibrated midpoint diagnostic.

## Headline

- Placements: 64
- Truth any-fill prevalence: 0.84375
- Truth full-fill prevalence: 0.59375
- Any-fill disagreement across all models: 22 placements
- Full-fill disagreement across all models: 8 placements
- Descriptive in-sample best alpha: alpha=1.00 (not calibrated)

## Model table

| Model | Any TP/FP/FN/TN | Precision | Recall | Quantity MAE | First-fill timing MAE (s) |
|---|---:|---:|---:|---:|---:|
| alpha=0.00 | 40/0/14/10 | 1.0 | 0.740741 | 0.00033265421875 | 1.66082365 |
| alpha=0.25 | 50/0/4/10 | 1.0 | 0.925926 | 0.00015327109375 | 0.97035516 |
| alpha=0.50 | 50/2/4/8 | 0.961538 | 0.925926 | 0.00015327234375 | 1.3757082 |
| alpha=0.75 | 50/2/4/8 | 0.961538 | 0.925926 | 0.00015327234375 | 1.31244036 |
| alpha=1.00 | 54/2/0/8 | 0.964286 | 1.0 | 0.00000000125 | 0.3753268888888888888888888889 |
| TOUCH_FILL | 54/8/0/2 | 0.870968 | 1.0 | 0.0001563403125 | 1.300144037037037037037037037 |

## Research questions

1. **How often do assumptions disagree?** Any-fill predictions differ on 22 of 64 placements; the alpha grid alone differs on 16.
2. **Which assumptions create backtest-only fills?** The highest-FP model is `TOUCH_FILL`. Its FP count is 8. The post-hoc section in `model_metrics.json` reports cancellation/priority regimes, and those L3 fields were not model inputs.
3. **Which assumptions miss L3-derived fills?** The highest-FN model is `alpha=0.00` with 14 misses. For alpha=0, missed-order post-hoc cancel depletion totals 0.79884080 BTC.
4. **Is pessimistic always conservative?** It produced no any-fill false positives in this minute, so it was empirically conservative here. This is not a theorem or an out-of-sample result.
5. **Under which queue/cancellation regimes do errors occur?** The post-hoc aggregates show the regimes for FP/FN sets, but 64 placements in one minute are insufficient for stable conditioning by queue size or market regime.
6. **Does touch-fill materially exaggerate fills?** TOUCH_FILL predicts any fill at rate 0.96875 versus truth 0.84375, with 8 backtest-only fills. Whether that is materially stable across dates is insufficiently sampled here.

## Method boundary

The fill-model module receives only blinded aggregate frames and sanitized placement fields. Actual order IDs, lifecycle reasons, FIFO positions, and L3 ahead decomposition are absent. L3 depletion categories are joined only after predictions for error analysis.
Censored observations are retained as censored and are never replaced by an arbitrary large time-to-fill.
