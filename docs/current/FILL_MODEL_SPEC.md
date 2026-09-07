# Fill Model Specification

Status: current implementation  
Methodology lineage: Prompt 5C–5E, through `prompt5e-v13`  
Primary code: `src/lobcap/l3/virtual.py`, `src/lobcap/l2/fill_models.py`,
`src/lobcap/l2/multidate.py`

## 1. Scope and terminology

The label produced from Coinbase L3 is an **L3-derived shadow-fill label**.
Historical exchange order flow is held fixed while one passive virtual order
is inserted into an independent counterfactual branch. Actual lifecycle events
for exchange orders are observations; the virtual order's fill is inferred.
It is not evidence that a submitted real order would have received the same
fill.

The engine assumes **no market impact**. A real order could change taker size,
subsequent matching, cancellations, and other participants' behavior. Virtual
orders never enter the reconstructed exchange book and never coexist with each
other. One virtual order cannot become another virtual order's queue ahead.

All quantities and prices use `Decimal`. Sequence is the ordering authority;
timestamps determine scheduled placement and TTL boundaries but never reorder
exchange lifecycle messages.

## 2. Placement timing and price

An intent may specify an exact `after_sequence=S` or an exchange-time trigger.
For an exact sequence, lifecycle sequence S is fully applied and the virtual
order is inserted before S+1. For a time trigger, the first lifecycle message
whose exchange timestamp is at or after the trigger is fully applied, and the
virtual order is placed immediately after that message's sequence.

The current experiments place passively at the reconstructed BBO:

- buy at best bid;
- sell at best ask.

The persisted placement records include the actual placement sequence and
exchange timestamp, so the time-trigger conversion is auditable.

## 3. Ahead and behind sets

At placement, every actual order resting on the same side and price is copied
into `ahead_order_ids`, with its current remaining quantity. Their sum is
`ahead_volume_initial`.

Snapshot array order is not treated as FIFO. Internal rank among orders already
in a generated snapshot is unknown, but every one of those orders predates the
new virtual order and is therefore ahead from the virtual order's perspective.

Actual orders opened at that price after placement are behind. An order that
loses priority through a price move or size increase is removed from ahead; if
it rests at the virtual price afterward, it is reinserted behind.

## 4. Actual ahead-order lifecycle

- Maker match: subtract the actual match size from that maker's tracked
  remainder and from aggregate ahead volume. Record it as
  `ahead_trade_depletion`. The same match is not also allocated to the virtual
  order.
- Done/canceled: remove the order's current tracked remainder and record
  `ahead_cancel_depletion`.
- Same-price size decrease: retain priority, reduce the tracked remainder, and
  record the reduction as `ahead_modify_decrease`.
- Same-price size increase: the old quantity loses FIFO priority. Remove its
  tracked remainder from ahead and record `ahead_priority_loss`; the modified
  actual order is behind.
- Price change: remove the old-price tracked remainder from ahead as priority
  loss. Any new-price presence is a tail reinsert.
- A resting order ID observed as a taker is held in explicit pending
  reconciliation state. A following `remainder_after_modification` or
  authoritative `done` reconciles its old level. Unresolved reconciliation is
  a blocking invariant failure.

## 5. Shadow-fill allocation

### Same-price behind match

When ahead volume is zero and an actual maker that is known to be behind the
virtual order receives a match at exactly the virtual price, the fixed-flow
counterfactual allocates:

`virtual_fill = min(actual match size, virtual remaining size)`.

The evidence type is `SAME_PRICE_BEHIND_MATCH`. A behind maker receiving a
match while ahead volume is positive is an invariant violation; the engine
does not silently repair it.

### Trade-through

After ahead volume reaches zero:

- a virtual buy at P can use a maker-side-buy match below P;
- a virtual sell at P can use a maker-side-sell match above P.

Such a worse-price execution proves that the incoming taker crossed the
virtual quote. Same-price behind evidence takes precedence. The evidence type
is `TRADE_THROUGH`.

### Partial and terminal outcomes

Each eligible match contributes at most the virtual order's remaining
quantity. Multiple matches can create a partial fill. The first positive
allocation records first-fill sequence/time; remaining quantity reaching zero
records full-fill sequence/time.

At TTL:

- zero remaining: `FULL_FILL`;
- positive fill with remaining quantity: `PARTIAL_ONLY`;
- no fill: `NO_FILL`.

If the bounded sample ends before TTL, the unresolved branch is `CENSORED`.
Censored rows are excluded where a terminal label is required.

## 6. Blinded L2 model family

L2 models receive aggregate level updates/trades plus sanitized placement
fields. They do not receive order IDs, ahead membership, L3 depletion
components, or shadow outcomes.

For placement price P, let queue Q start at aggregate L2 quantity at P.
For each post-placement, pre-expiry frame:

1. compute aggregate level depletion;
2. subtract same-price maker trade volume to obtain non-trade depletion C;
3. for an alpha model, reduce Q by `min(Q, alpha * C)`;
4. for an executable trade, consume Q first and allocate only residual trade
   quantity to the virtual order.

Executable means maker side equals the virtual side and price is at least as
aggressive as the virtual quote: buy-side maker trade price <= virtual buy
price, or sell-side maker trade price >= virtual sell price.

Implemented models:

- `alpha=0.00`: pessimistic queue model; non-trade depletion never advances
  the virtual queue. Trades must consume the entire modeled ahead queue.
- `alpha=0.25`, `alpha=0.50`, `alpha=0.75`: fixed diagnostic assumptions.
- `alpha=1.00`: every unit of observed non-trade level depletion advances the
  ahead queue, bounded by remaining Q.
- `STRUCTURAL_ALPHA`: same queue algorithm with alpha frozen from calibration
  dates before final test.
- `TOUCH_FILL`: ignores queue position and allocates each executable trade
  directly to virtual remaining quantity.

The model's alpha is a fraction of **L2-observed non-trade depletion assumed to
occur ahead**, not a fill probability.

## 7. Visible depletion and hidden priority loss

For structural calibration:

- `C` is aggregate L2 non-trade depletion at the placement price.
- `A_visible` includes actual ahead cancellation, same-price size decrease,
  and old-level depletion caused by price change. Each is visible as a
  decrease at the L2 old level.
- `H_hidden` includes same-price size-increase priority reset and other L3-only
  priority effects that reduce virtual ahead quantity without aggregate old
  level depletion.

The primary estimator is:

`alpha_visible = sum(A_visible) / sum(C)`.

`H_hidden` never enters the L2 model input or alpha numerator. Its diagnostic
share is:

`sum(H_hidden) / sum(total_observed_L3_ahead_reduction)`,

where the denominator also includes trade depletion. The implementation uses
semantic event classification, not `min(A,C)` clipping. Exact Decimal checks
require `0 <= A_visible <= C` per event and virtual-order window.

## 8. Known limits

- Snapshot-internal same-price FIFO rank is unavailable.
- No-market-impact labels may diverge from real-order fills.
- The current diagnostic sizes and horizons are experiment settings, not
  claims of optimal market-making parameters.
- Full 12-date calibration/OOS evaluation has not run; the bounded one-date
  alpha is not a frozen production/OOS parameter.
