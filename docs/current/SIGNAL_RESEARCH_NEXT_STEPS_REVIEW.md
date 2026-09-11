# Signal Research next-steps review — 2026-09-11

Reviewed proposal: `LOBcap_Signal_Research_Next_Steps.md` against
`feat/signal-research-v1` at `3a9f269` and read-only Ubuntu runtime evidence.

## Decision

Accept the direction with corrections. The next release should complete the
predictive research pipeline before adding more strategy names or tuning the
ten-minute PnL. The existing replay, execution, accounting, GLFT, trace and
three-label foundations should remain intact.

Do not implement the proposal literally. Dataset roles must bind immutable
window manifests and hashes, not calendar dates alone. Cache identity is more
incomplete than the proposal states, the current segment plumbing is only a
placeholder, and the present future-suffix test does not exercise the complete
replay pipeline.

## Verified current state

- The VM contains 24 depth files and 24 trade files for each of 2026-09-07,
  2026-09-08, 2026-09-09 and 2026-09-10. Each date also has one snapshot
  container and 288 lifecycle records. This is inventory evidence only; none of
  those dates or proposed windows has passed reconstruction quality gates yet.
- `FeatureDefinition` contains the proposed metadata fields, but
  `CausalFeatureEngine` does not consume a registry. Calculations and metadata
  can therefore disagree.
- Unit inference is name-based. `spread_ticks`,
  `microprice_minus_mid_ticks`, and `vamp_5_minus_mid_ticks` currently fall
  through to `dimensionless` instead of `ticks`.
- `imbalance_5` and `vamp_5` use however many levels are present. A two-level
  fixture is currently accepted as `vamp_5`, confirming the proposal's depth
  semantics finding.
- Rolling windows do not implement per-feature minimum warmup. OFI, signed
  volume, intensity and return features also lack definition-specific stale and
  missing rules. `mid_return` can use an old anchor without recording its age.
- The cache key includes sample hash, schedule, clock mode, one stale threshold
  and a manually maintained feature version. It omits tick, bootstrap receipt,
  acquisition segment, feature-definition hash, feature configuration,
  normalization/schema versions and Git/code fingerprint.
- Acquisition-segment-aware label censorship exists, but cache generation takes
  one constant `acquisition_segment="segment-1"`; normalized events do not yet
  drive real segment transitions.
- Predictive analysis currently reports coverage, status counts, target age,
  Pearson/Spearman IC, sign hit rate and means for one feature. It does not yet
  produce standard deviation/quantiles, buckets, monotonicity, per-window,
  per-date, pooled, non-overlapping, stability or redundancy reports.
- The current prefix-invariance test snapshots state and mutates a different
  engine afterward. Since the original snapshot is immutable, this is weaker
  than replaying two streams with an identical prefix and different suffixes
  and comparing all prefix artifacts.
- Scheduler selection still depends on strategy identity for `flow_guard` and
  `expression` under exchange-clock configurations. Recorded-clock suites all
  use the arrival scheduler, but scheduler implementation identity is not an
  explicit comparison dimension.
- The frozen ten-minute scorecard supports the proposal's PnL interpretation:
  GLFT-microprice had spread capture `+0.089300` USDT and inventory MTM
  `-0.395200` USDT. This is descriptive development evidence, not proof that
  GLFT failed or succeeded.

## Required corrections to the proposal

1. A dataset role belongs to a sealed manifest containing exact window bounds,
   source and normalized checksums, normalization version, segment IDs and
   quality status. A date label alone is insufficient.
2. Existing 2026-09-09/10 files may be reserved for pilot validation/OOS only
   if outcomes remain unopened until the policy and promotion rules are frozen.
   `FINAL_UNTOUCHED_RESERVED` must use data collected or sealed after the final
   research protocol is frozen; it should not be assigned to already explored
   data retroactively.
3. Window selection must be fixed before examining returns, volatility or signal
   performance. Operational completeness may exclude a window, with its reason
   retained; it may not choose a more attractive replacement window.
4. Feature cache identity must include tick, bootstrap availability and real
   segment identity in addition to every item proposed in the document.
5. Strict depth requires both at least N live levels on each side and evidence
   that those levels lie within known reconstructed coverage. The missing status
   should be `INSUFFICIENT_DEPTH`, not generic zero or `UNAVAILABLE`.
6. DEVELOPMENT bucket boundaries need honest evaluation. Fit on one development
   partition and evaluate on another, or use date-level cross-fitting. Reporting
   bucket performance on the same rows that selected the cutoffs overstates
   evidence.
7. The primary analysis should be preregistered. Seven features × three label
   families × four horizons already creates 84 descriptive comparisons. Start
   with microprice and named primary label/horizon pairs; label the rest
   secondary diagnostics and claim no significance.
8. `POST_LATENCY_RETURN` must record the latency scenario used. Fixed latency is
   a stress assumption until an empirical distribution is frozen, and recorded
   client/exchange clocks still rely on the documented clock-alignment proxy.
9. Scheduler identity must enter manifests and comparison guards before any
   exchange-clock cross-strategy conclusion. Add parity tests first; refactor
   routing only when exact output parity is demonstrated.

## Revised implementation order

1. **Integrity guard:** add scheduler identity to manifests/comparison guards and
   replace the weak suffix test with complete two-replay prefix invariance.
2. **Catalogue and sealing:** inventory fixed windows, build real acquisition
   segments, run continuity/snapshot/coverage gates, and emit `PASS` or
   `EXCLUDED` without replacement selection.
3. **Feature registry:** make immutable definitions the calculation source of
   truth; fix units, strict depth, warmup, source-specific ages, stale and
   missing statuses. Freeze the small core feature set.
4. **Cache v2:** hash canonical definitions/configuration plus tick, bootstrap,
   segment, source schema, normalization, clock, schedule and code provenance.
   Reject v1 cache reuse.
5. **Development cadence:** use only sealed DEVELOPMENT windows to publish raw
   anchor/target age distributions, then freeze horizon-specific freshness.
6. **Predictive report:** start with microprice; report each label family and
   horizon separately, then per-window, per-date and pooled IC, sign, bucket and
   coverage diagnostics. Add non-overlapping views and no iid p-values.
7. **Stability and redundancy:** add date-direction consistency, cross-fitted
   bucket monotonicity, feature correlations and coverage overlap.
8. **Human gate:** inspect results before opening validation/OOS or integrating a
   signal into quote generation. Keep strategy PnL and paper eligibility out of
   this gate.

## Acceptance boundary

The Signal Research Lab is not complete until the sealed multi-day catalogue,
real segments, registry-driven features, cache v2, frozen DEVELOPMENT freshness,
predictive/stability reports and full Ubuntu regressions pass. Quote-path
survival follows only for signals promoted at the human gate. Paper-trading
candidate selection remains out of scope at this stage.
