# Phase 0.5 — L3 Ground Truth Specification

Updated: 2026-09-06

## 1. Do not implement against memory

`legacy_code/l3_queue.py` is a prototype only.

Before writing a production L3 parser:
1. runtime-verify Tardis free access;
2. save actual raw sample messages;
3. document exact snapshot/lifecycle schemas;
4. document sequence semantics;
5. only then implement reconstruction.

## 2. Required L3 reconstruction behavior

The production implementation must support, as provided by the verified source:
- initial full snapshot / reconstruction start;
- order add/open;
- order match/partial match;
- cancel/done;
- size change;
- sequence validation;
- reconnect/gap handling.

Unknown or source-specific event types must be logged, not silently ignored.

## 3. L3-derived shadow-fill label

Do not call a virtual fill an absolute fill ground truth. The precise term is:

> **L3-derived shadow-fill label** — Historical L3 order flow is held fixed
> and a small passive virtual order is inserted without affecting subsequent
> observed order flow.

The actual lifecycle of orders ahead of the virtual order (matches,
cancellations, and modifications) is observed L3 truth. A fill assigned to the
virtual order is a no-market-impact counterfactual. Had an order really been
inserted, it could have changed later taker quantity and the subsequent match
path.

Every virtual order is its own counterfactual branch. Virtual orders do not
enter the reconstructed actual book, do not coexist with one another, and can
never appear in another virtual order's ahead set.

### Placement and queue state

Placement is defined by `after_sequence = S`: sequence `S` is completely
applied, then the virtual order enters before `S+1`. Wall-clock time alone is
not an ordering key. The first validation scope is passive BBO placement only,
at the reconstructed best bid or best ask after `S`.

All actual same-side, same-price orders resting after `S` are ahead. Orders
opened or reinserted at that level later are behind. Snapshot-array order is
never interpreted as FIFO: snapshot-internal rank remains unknown, while the
whole resting set is known to precede a newly inserted virtual order.

A virtual FIFO order needs state:

```text
side
price
original_size
remaining_size
placement_sequence
placement_exchange_time
expiry_sequence/time
ahead_order_ids
ahead_volume_initial
ahead_volume_remaining
ahead_trade_depletion
ahead_cancel_depletion
ahead_modify_decrease
ahead_priority_loss
own_filled_volume
first_fill_time
full_fill_time
outcome
```

Requirements:
- side must match the maker side;
- price must match the virtual order price;
- partial fills must be tracked;
- arriving same-price orders after placement are behind the virtual order;
- cancellations of known ahead IDs reduce ahead volume;
- matches of ahead IDs reduce ahead volume by the actual matched size;
- a same-price match against an actual behind maker can fill the virtual order
  only after ahead volume is zero (`SAME_PRICE_BEHIND_MATCH`);
- once ahead volume is zero, a buy virtual order at `P` may use an actual maker
  buy match below `P`, and a sell virtual order may use an actual maker sell
  match above `P`, as separate `TRADE_THROUGH` evidence;
- the same actual match is allocated at most once within one virtual branch;
- fill time must distinguish first partial fill vs full fill.

For an ahead order, actual match size is trade depletion. A canceled order's
current remainder is cancel depletion. A same-price size decrease keeps
priority and is modify-decrease depletion. A size increase or price change
removes the old remainder from ahead as priority loss and any return to the
virtual level is behind.

The engine supports positive arbitrary `Decimal` sizes. `0.001` and `0.01`
BTC are diagnostic validation sizes, not research conclusions. Supported
initial horizons are 1, 5, 10, and 30 seconds. A fully observed TTL produces
`FULL_FILL`, `PARTIAL_ONLY`, or `NO_FILL`; a sample ending before TTL produces
`CENSORED` (unless the order was already fully filled).

Queue-evolution rows retain sequence, event classification, ahead volume
before/after, each depletion class, virtual fill, and fill evidence. They do
not copy raw messages. This ledger is the future L2 model-comparison target.

## 4. L3 -> L2 downsampling

The same L3 stream must produce an L2 view:
- aggregate quantity by price level and side;
- output event-time level changes;
- separately aggregate actual trades by level/side;
- keep timestamps aligned.

The L2 model must never use individual order IDs.

## 5. L2 fill-model definitions

Let:
- `Q_t` = estimated queue volume ahead before an event interval;
- `V_trade` = actual traded maker volume at the quoted price that would consume queue ahead;
- `V_cancel` = non-trade level depletion at that price;
- `alpha in [0,1]` = assumed fraction of cancellation volume that occurred ahead of our virtual order.

### Pessimistic

```text
Q_{t+1} = max(0, Q_t - V_trade)
```

Cancellations never help.

### Calibrated neutral

```text
Q_{t+1} = max(0, Q_t - V_trade - alpha * V_cancel)
```

`alpha` is estimated from L3 training samples.
For an uncalibrated diagnostic baseline, `alpha = 0.5` may be reported separately.

Do not hard-code “neutral = all level decrease counts.”

### Queue-optimistic diagnostic

```text
alpha = 1
```

All observed cancellation depletion is assumed to have occurred ahead.

### Touch-fill upper bound

A separate extreme upper bound:
- price touches / trades at quote -> fill

Do not conflate touch-fill with `alpha = 1`.

## 6. Ground-truth evaluation

For each virtual order:
- truth fill/no-fill;
- first/full time-to-fill;
- each model prediction;
- false positive = backtest-only fill;
- false negative = missed real fill.

Report:
- confusion matrix
- false-fill rate
- missed-fill rate
- calibration by spread/volatility/time-of-day/queue size
- uncertainty / venue-transfer limitations.

## 7. Transfer to Binance

Tardis/Coinbase (or another L3 venue) is not Binance.

Any calibrated `alpha` or model transferred to Binance must be labeled:
- cross-venue transfer assumption;
- tested under sensitivity ranges;
- not “Binance queue truth”.

## 8. Runtime-verified Coinbase reconstruction

Implemented on 2026-09-06 under `src/lobcap/l3/` against the actual
`2026-09-01T00:00:00Z` Tardis Coinbase sample. The legacy prototype was not
used as a parser specification and was not modified.

### Raw framing and supported messages

The parser preserves two distinct values:

- Tardis local receive timestamp: the prefix before the first space;
- Coinbase exchange-native JSON payload: preserved without adding fields.

Supported runtime-observed message types are `full_snapshot`, `received`,
`open`, `match`, `done`, and `change`. Unsupported types invalidate the
segment with explicit evidence rather than being silently ignored.

### Generated snapshot overlap

Lifecycle messages are sequence-checked and buffered until `full_snapshot`.
After loading snapshot sequence `S`, buffered events with `sequence <= S` are
discarded and events with `sequence > S` are replayed in sequence order. The
snapshot is an out-of-band reconstruction anchor, not a lifecycle increment.

For the verified minute:

- snapshot sequence: `135356475328`;
- buffered lifecycle events replayed: 1,529;
- buffered events discarded at or before the snapshot: 2,224;
- first applied sequence: `135356475329`;
- last applied sequence: `135356516799`;
- gaps, duplicates, reversals: zero.

Any lifecycle gap, duplicate, or reversal makes the segment invalid and is
included in the reconstruction report.

### Order and change semantics

Prices and sizes use `Decimal`; float is not used for book truth. `received`
does not add an order. `open` adds a resting order. `match` decrements only the
maker order by the actual match size. `done` retains the distinction between
`filled` and `canceled` before removing an active order.

All 1,838 applied runtime `change(reason=modify_order)` messages had
`old_price` and `old_size` equal to the reconstructed state. The implementation
uses `new_price` and `new_size` as the new state. Coinbase's documented
priority rules are applied:

- same-price size decrease retains the existing queue position;
- size increase or price change loses priority and reinserts at the level tail.

Coinbase's documented STP change form was not observed in this runtime sample.
It is therefore audited as unsupported/uncertain rather than being guessed.

### FIFO limitation

Snapshot orders store `source=snapshot`, with unknown `open_sequence` and
`open_time`. Their array order is retained only for deterministic storage and
is not interpreted as FIFO. Orders arriving through `open` store their actual
sequence/time and have known relative arrival order. A virtual order placed
later can observe every currently resting order at the same side/price as
ahead, without claiming an absolute FIFO order among the snapshot-origin
orders themselves.

### Verified one-minute result

The full 43,696-message sample reconstructed deterministically with:

- active orders: 109,986;
- bids: 63,959 orders, aggregate size `115359.87827145`;
- asks: 46,027 orders, aggregate size `3939.77794020`;
- best bid/ask: `78537.72` / `78537.73`;
- negative remaining size: 0;
- unknown maker match: 0;
- duplicate open: 0;
- crossed book: false;
- order-index membership errors: 0;
- aggregate-index errors: 0.

There were 213 `done` events for IDs not in the resting book. All 213 belonged
to orders that had a `received` event but never an `open` event, matching the
official Full-channel rule that immediate taker/terminal orders can receive a
`done` without ever resting. Unclassified unknown done events were zero.

Reports are in `artifacts/l3_reconstruction/`.

## 9. Runtime-verified virtual-order experiment

`src/lobcap/l3/virtual.py` implements independent BBO shadow branches as
read-only observers around the verified reconstruction engine. It does not
reuse or modify `legacy_code/l3_queue.py` and never inserts virtual state into
the actual `L3Book`.

The deterministic one-minute grid uses exchange-time triggers at seconds 5,
20, 35, and 50, both BBO sides, diagnostic sizes `0.001`/`0.01` BTC, and TTLs
1/5/10/30 seconds. For a fixed input, each trigger resolves to an exact
`placement_sequence`; replaying twice produced identical order and ledger
content.

The verified experiment produced 64 placements: 38 full fills, 12
partial-only outcomes, 10 no-fills over a fully observed TTL, and 4 censored
outcomes. Both `SAME_PRICE_BEHIND_MATCH` and `TRADE_THROUGH` occurred in the
actual minute. Negative queue/remaining values, overfills, double allocations,
behind matches while ahead was nonzero, unknown-maker decisions, and invalid
side/price fills were all zero.

Artifacts are in `artifacts/virtual_truth/`:

- `virtual_orders.jsonl` — final branch state and label;
- `queue_ledger.jsonl` — sequence-ordered queue evolution per branch;
- `truth_summary.json` — experiment scope, outcomes, and limitations;
- `invariant_report.json` — invariant counts and first evidence.

The full-minute grid did not happen to place an order at a BBO level whose
ahead member was modified. Actual Tardis payload fixtures separately regress
same-price decrease, same-price increase, and price-change priority behavior.

## 10. Runtime-verified blinded L2 comparison

Prompt 5D freezes the 64 Prompt 5C placements and labels. The truth artifact
SHA-256 is
`593920dc416631c0f4924d9ff420ae6b8801580c85cdc50c9db06337773f28ca`.
No virtual placement, size, price, sequence, TTL, label, or fill time is
regenerated or modified by the comparison.

The L2 boundary is split into three components:

- `lobcap.l2.downsample` is the trusted one-way adapter. It may resolve an
  affected level using order identity internally, but discards that identity
  before constructing an L2 frame.
- `lobcap.l2.schema` permits only sequence/time, aggregate quantity
  before/after, and maker-side trade price/size. It rejects observed order IDs,
  lifecycle identity/reason, individual remaining size/FIFO position, and L3
  ahead-depletion categories.
- `lobcap.l2.fill_models` imports no L3 module and receives only the blinded
  projection, sanitized frozen placement fields, and a model specification.

For a virtual side/price, each level decrease is
`D=max(0,Q_before-Q_after)`. Same-level observed trade volume `T` is retained
separately, and observable non-trade depletion is `C=max(0,D-T)`. Thus the
same observed trade is not counted once as trade and again as cancellation.
The alpha queue model uses `alpha*C`; trade first consumes modeled queue ahead
and only residual trade can fill the virtual order. `TOUCH_FILL` ignores queue
position and remains a separate upper bound rather than an alias for
`alpha=1`.

The verified minute produced 27,953 blinded frames, 29,756 level updates, and
295 trades. Across 64 placements, L3-derived any-fill prevalence was 84.375%
and full-fill prevalence was 59.375%. The six assumptions disagreed on 22
any-fill labels and 8 full-fill labels.

Any-fill TP/FP/FN/TN results were:

- `alpha=0.00`: 40/0/14/10;
- `alpha=0.25`: 50/0/4/10;
- `alpha=0.50`: 50/2/4/8;
- `alpha=0.75`: 50/2/4/8;
- `alpha=1.00`: 54/2/0/8;
- `TOUCH_FILL`: 54/8/0/2.

In this one-minute in-sample description, `alpha=1.00` had the smallest
observed-window fill-quantity MAE (`0.00000000125` BTC). This is explicitly a
**descriptive in-sample optimum**, not a calibrated or out-of-sample model.
`alpha=0.50` is an **uncalibrated midpoint diagnostic**, not a true neutral
model. `TOUCH_FILL` predicted fills for 96.875% of placements and produced the
most backtest-only fills (8).

Post-hoc L3 joining showed that the 14 `alpha=0` false negatives had
`0.79884080` BTC of aggregate ahead-cancel depletion across independent
branches. These L3 categories were joined only after predictions and were not
model inputs. The 64 placements from one minute are insufficient for stable
regime conclusions, calibration, confidence intervals, or venue transfer.
Censored quantities are observed-window lower bounds; censored observations
are never converted to an arbitrary large time-to-fill. Full-fill confusion
excludes the four unresolved censored horizons.

Artifacts are in `artifacts/fill_model_comparison/`. L2/L3 leakage,
trade/non-trade double counting, negative queue/remaining, and overfill
violations were all zero, and two complete runs were byte-deterministic at the
structured-result level.

This phase still does **not** perform multi-date alpha calibration,
calibration/evaluation date splitting, Binance transfer, strategy PnL, or a
full-day Tardis download. Those remain later work.
