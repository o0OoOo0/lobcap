# GLFT research policies

This adds `glft` and `glft_microprice` to the existing simulator. They implement
the GLFT approximation with an explicitly documented aggregate-trade intensity
estimator. They are research implementations, not claims about a particular LP's
production strategy or demonstrated profitability.

## Formula and units

Prices and distances are in instrument ticks. Inventory `q` is known BTC inventory
divided by the configured order size, so an order has `Delta=1`. This version ties
`xi=gamma` and uses:

```
lambda(delta) = A * exp(-k * delta)
c1 = log(1 + gamma/k) / gamma
c2 = sqrt(gamma/(2*A*k) * (1 + gamma/k)^(k/gamma + 1))
half = c1 + sigma*c2/2
inventory_shift = (inventory / size) * sigma*c2
center = observable_mid - inventory_shift*tick + optional_signal*tick
```

`A`: arrivals per second per direction; `k`: inverse ticks; `sigma`: ticks per
square-root second from the standard deviation of past one-second arithmetic
midpoint changes. Gamma is a research risk coefficient in this normalized tick /
order-size coordinate system; changing order size changes the inventory units.
It is not the queue-depletion alpha. The implementation uses Decimal logarithm,
exponential and square root at precision 50 for the quotation coefficients.

The effective half width is the maximum of the computed width, the configured
minimum half width and the positive maker fee expressed in ticks. Rebates do not
reduce this floor. This is an explicit cost floor layered on the model, not part
of its original derivation. The existing tick/lot rounding, BBO clamp, reservations,
serial replacement, entry/cancel/report delays and maximum age remain in force.
An effective width above the configured maximum suppresses quotes; it is not
silently clamped. GLFT does not execute terminal liquidation or hedges.

The signal variant adds `strength * (microprice-mid)/tick`, capped by
`max_signal_ticks`. Strength zero should reproduce the basic GLFT order behavior;
the record retains distinct strategy identities. Predictive usefulness is unproven.

## Causal estimation contract

- The estimator consumes only public delivered trade price, maker side, normal
  quantity, timestamps and the latest observable midpoint. It never sees queues,
  own fill outcomes, future packets, or the hidden exchange book in recorded mode.
- An aggressive buy crosses distance `(trade_price-observable_mid)/tick`; an
  aggressive sell crosses `(observable_mid-trade_price)/tick`. Count normal-volume
  aggregate packets reaching each of 1, 2, 4, 8, 16, 32, 64 and 128 ticks.
- One packet can cross multiple thresholds. It counts once at each crossed
  threshold; quantity is not multiplied into the arrival count. Directions are
  pooled and divided by twice valid observation seconds. This assumes symmetric
  arrival intensity. Binance aggregation and batching affect this proxy.
- Ignore trades more than one second old on delivery and trades against a stale
  observed book; retain exclusion counts. The first partial second is discarded.
  Only intervals with contiguous one-second observations and fresh endpoints
  contribute. This endpoint test does not prove freshness throughout an interval.
- Roll at most 120 one-second intervals by default; require 60 valid intervals,
  at least 80% valid coverage, 30 one-tick crossings, and three distinct positive
  rate observations. Regress log(rate) on actual tick distances, excluding zero
  rates. Require negative slope and R2 >= 0.5. These defaults are diagnostic
  assumptions, not optimized coefficients or statistical significance thresholds.
- The fit and volatility update only after closing a second. Decisions between
  updates use the last estimate, at most one second old. Invalid current fits
  replace the prior fit with an explicit no-quote state; there is no fallback
  guessed coefficient or indefinitely reused last good fit.
- This is an observable crossing-rate proxy, not calibrated own-order fill
  intensity. It omits queue priority, volume available ahead, and burst dependence.
  Pooled directions, zero-rate exclusion, a single exponential curve and extension
  beyond the observed distance range are material modeling assumptions.

## UI and comparisons

Choose **GLFT** or **GLFT + 잔량 가격 신호** in the left strategy selector. The
half-width input is a minimum floor for these policies. Gamma, estimation window,
minimum observations/crossings, fit threshold and maximum width are explicit.
The order-behavior tab shows estimation status counts, calculated widths, inventory
and signal shifts, valid observations, and last A/k/sigma/R2. Full decision traces
retain distance counts, rejected-trade counts and the inputs to every decision.
The preview's artificial A/k/sigma values are labeled examples and are never used
by a replay.

`quote_start_seconds` provides a common initial no-quote interval across policies.
Estimation continues during this interval. Direct comparison rejects mismatched
start delays. Use zero initial inventory for a comparison with a flat warmup;
nonzero initial inventory still earns MTM during the no-quote interval.

The bounded validator fixes 120 seconds of initial observation for every policy,
then trades the remaining eight minutes of the existing ten-minute sample. The
reported full-window PnL is unchanged by the flat initial interval, while occupancy
and rate summaries still use their documented full-window denominators. A separate
control without this delay checks the previous BBO deterministic digest.

```sh
PYTHONPATH=src /home/0woo/lobcap-mvp/.venv/bin/python -m pytest -q
PYTHONPATH=src /home/0woo/lobcap-mvp/.venv/bin/python scripts/validate_glft_bounded.py --output artifacts/backtest/glft-validation
```

## References and limits

[HftBacktest GLFT tutorial](https://hftbacktest.readthedocs.io/en/latest/tutorials/GLFT%20Market%20Making%20Model%20and%20Grid%20Trading.html)
provides the quotation approximation and demonstrates estimating intensity and
volatility. Its intensity demonstration uses interval maxima, unlike this packet
count proxy; numerical calibration parity is not claimed. We use actual tick
distances rather than copying its documented half-tick measurement inconsistency.
[Order-book imbalance tutorial](https://hftbacktest.readthedocs.io/en/latest/tutorials/Market%20Making%20with%20Alpha%20-%20Order%20Book%20Imbalance.html)
motivates separating a price signal from inventory and quotation controls.

No fitting uses future observations or the final sample outcome. A ten-minute
development replay cannot establish out-of-sample quality or choose a production
candidate. Market impact, account fee verification and actual queue calibration
remain unresolved; adding this model does not remove those limits.
