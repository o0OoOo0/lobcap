# Research flow v1 — P0–P3

This release implements the first research loop in `MM_RESEARCH_PLATFORM_REDESIGN.md`: hypothesis → observable policy → order behavior → accounting contribution → one next experiment. It preserves capture files, prepared samples, existing run artifacts, the synthetic futures ledger, and research fill arithmetic.

## Using the four pages

1. **전략**: choose a behavior, name the hypothesis, edit its active inputs and save a revision. Saving does not run a simulation. The synthetic preview explains a single fresh policy state, not a performance forecast.
2. **실험**: choose a single run, E0–E4 baseline suite, one strategy change, or one execution/cost assumption. Review the frozen settings and explicitly submit. The existing serial worker, immutable experiment spec, cancellation and resume paths are reused.
3. **결과**: open legacy and workspace runs in one history. Read spread-at-fill, inventory MTM and signed fees, then inspect a linked order and its decision. Record `no_candidate`, `revise`, or `needs_more_data`; clone a follow-up with a new question.
4. **비교**: select a baseline before a changed run. Compare settings, the first desired/action divergence, and the exact PnL contribution difference. Different execution assumptions require the explicit sensitivity mode and frozen strategy settings.

The saved draft revision, route, run, baseline and job are restored from workspace context. This loopback application has one shared workspace; simultaneous independent browser users do not have separate accounts or contexts. Unsaved form text is not a saved revision.

## Policy and execution contracts

`binance-own-orders-v2` remains the execution contract. `quote-policy-v1` versions the new client-observable retention behavior; `decision-trace-v1` versions the new evidence. Old runs without these fields retain their original evidence and are labeled missing, not retroactively interpreted as new-policy runs.

- S0 `no_trade`: no new orders. Initial inventory can still have MTM.
- S1 `join_bbo`: quote the delivered best bid/ask using the configured quantity.
- S2 `inventory_size`: at 60% of the known inventory limit, reduce the inventory-increasing side by one lot. A one-lot target becomes zero on that side.
- S3 `flow_withdraw`: use delivered trades in the preceding second. Enter withdrawal at signed flow ±0.6 and release inside the respective ±0.3 boundary; require configured minimum volume. State belongs to each simulation.
- `keep_remaining` retains a partially filled order only when its original target quantity is unchanged. Increasing or shrinking the target requests cancellation.
- `reset_target` requests cancellation/replacement when desired target and remaining quantity differ. It is the legacy default; the new UI defaults to `keep_remaining` explicitly.
- `reprice_threshold_ticks` suppresses ordinary price changes smaller than the threshold. `min_hold_ms` delays ordinary price changes, measured from activation when known and submission while pending. Neither overrides withdrawal, target-size changes or hard risk. Existing maximum-order-age and cancellation latency remain effective.
- Cancel-pending orders remain exposed. Serial replacement remains one order per side. Recorded clock policies use acknowledged order/portfolio state, not hidden exchange state.

DecisionTrace links a deterministic decision ID to observable input, raw target, tick-rounded target, policy-limited target, risk-limited target and planned actions. Numeric stage comparisons ignore explanation-text changes. Order/fill records carry the originating decision ID. Queue fields in execution evidence are post-run diagnostics, not strategy inputs. A custom strategy callback whose internal stages are unavailable is labeled unavailable.

## Frozen baseline question

Use the existing `binance_btcusdt_20260906_1410_1420_arrival` sample, 2026-09-06 14:10–14:20 UTC. E0 is no-trade, E1 S1 with partial retention, E2 only changes partial retention to reset, E3 changes S1 to inventory sizing, and E4 changes S1 to flow withdrawal. Same sample, initial state, risk limits, cost and execution assumptions are held fixed. The suite rejects an attached hedge/research policy. Each row records its label, question, effective settings and frozen experiment hash.

Zero fees are explicitly a diagnostic profile; user-entered costs are assumptions, not verified account fees. The participation parameter is a per-order cumulative eligible-public-volume budget, not measured impact. No scenario is automatically altered to make PnL positive.

## Interpretation and migration

Net PnL remains spread contribution at the fill's midpoint + inventory MTM − signed fees. This is an accounting identity, not identification of a causal mechanism. Markout is not added again. Representative orders have explicit selection reasons and are not a statistical sample.

The terminal inventory calculation is a separate immediate visible-depth reference: sell longs into bids, buy shorts into asks, apply lots, known coverage, stale-book checks and assumed taker fees. Insufficient depth leaves an explicit residual. It does not mutate fills or the ledger, reproduce cancel races, or simulate the market reacting to liquidation.

The unified catalog reads existing directories (including deployment symlinks) and workspace jobs. Failed, canceled, incomplete or conflicting records cannot rank as successful runs. Original files are preserved. Legacy runs remain readable; missing decision stages remain **미기록**. Cross-contract/code comparisons require explicit compatibility and otherwise are blocked. A narrow exception checks actual Git objects and records all changed paths: only the UI, catalog, tests and documentation may differ. Execution, configuration, dependencies and analysis changes fail this check; missing objects or dirty versions also fail closed. The UI displays this compatibility evidence. Cloning an old run is a new-policy experiment, not a claim to reproduce its original environment.

## Reproduction

On Ubuntu with the existing prepared sample and Python environment:

```bash
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/validate_research_flow.py \
  --workspace /tmp/research-flow-fresh-workspace \
  --output /tmp/research-flow-fresh-report.json
PYTHONPATH=src python -m streamlit run apps/backtest_lab.py \
  --server.address 127.0.0.1 --server.port 8501
```

Validation uses fresh output paths, two serial E0–E4 suites (second in reversed variant order), original-source volume/ledger audit, trace links, contribution reconciliation, and next-experiment cloning. The authoritative regression environment is Ubuntu. No changes to the Unix-only research `resource` import are required.

## Deliberately outside this release

P4 multiple-period evaluation, P5 live paper operation and P6 advanced strategies are not implemented by this redesign. Ten minutes cannot approve a future-profitability candidate. Public aggregate capture does not establish actual queue rank, private order latency, market response, funding, margin liquidation, or exchange-wallet accounting. Existing HftBacktest comparison accounting discrepancies remain unresolved; this release does not claim accounting parity with it.

Runtime measurements and deployment evidence are recorded separately after validation.
