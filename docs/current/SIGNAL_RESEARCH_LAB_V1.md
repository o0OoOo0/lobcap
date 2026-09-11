# Signal Research Lab v1 foundation

This branch adds the causal data contracts used before a signal is allowed to
drive a market-making policy. It does not claim that a feature is predictive or
that a strategy is ready for paper trading.

## Clocks

The feature engine updates on delivered market events. A 100 ms analysis grid
only records research observations. It does not set the strategy decision
clock. Strategy timers such as 250 ms, event-driven decisions, and hybrid clocks
remain separate experiments.

Every feature point records its analysis time, client availability time,
exchange source time, recorded receive time, status, version, unit, and bounded
source references. A feature whose source is newer than the client observation
is rejected.

## Return labels

Three labels answer different questions:

| Label | Definition | Question |
|---|---|---|
| `MARKET_TRUTH_RETURN` | `M_exch(t+h) - M_exch(t)` | Did the feature align with the market move itself? |
| `CLIENT_FORWARD_RETURN` | `M_exch(t+h) - M_obs(t)` | What move remained from the price visible to the client? |
| `POST_LATENCY_RETURN` | `M_exch(t+L+h) - M_exch(t+L)` | Did the edge remain after entry latency `L`? |

The implementation supports ticks or basis-point returns. It records requested
and actual anchor/target times plus `anchor_age_ns` and `target_age_ns`.
Unfiltered observations have status `OBSERVED`; they are never presented as
`VALID` until an explicit, versioned `FreshnessPolicy` is applied.

There is no built-in 250 ms tolerance. The policy requires separate anchor and
target maximum ages for every horizon. Acquisition-boundary crossings and
sample-end targets are censored.

## Initial causal features

The event-driven engine currently provides BBO midpoint and spread, microprice,
depth imbalance at 1/2/5/10 levels, VAMP at five levels, signed trade volume,
trade-volume imbalance, trade intensity, BBO OFI, trailing midpoint return, and
book age. Trade/OFI/return windows are 100 ms, 500 ms, 1 s, and 5 s. These are
candidate measurements, not approved strategy rules.

## Bounded evidence

On the authoritative Ubuntu 3.12.3 runtime, the frozen ten-minute BTCUSDT sample
produced 6,000 research snapshots.

- Exchange-clock validation: 22.5 seconds, peak RSS 78.3 MiB.
- Recorded-clock validation: 30.1 seconds, peak RSS 77.5 MiB.
- At the 100 ms horizon, exchange-truth target age had median 51 ms, p95 96 ms,
  and maximum 186 ms.
- Under recorded delivery, the client anchor age had median 58 ms, p95 103 ms,
  and maximum 890 ms. This is separate from target age and demonstrates why the
  client-forward label must not be substituted for the market-truth label.
- No freshness threshold was selected from this single bounded sample.

Run the evidence builder with either `configs/backtest/binance_demo.yaml` and the
exchange sample, or `configs/backtest/binance_recorded.yaml` and the arrival
sample:

```bash
PYTHONPATH=src python scripts/validate_signal_lab_bounded.py \
  --config configs/backtest/binance_recorded.yaml \
  --sample artifacts/backtest/samples/binance_btcusdt_20260906_1410_1420_arrival \
  --output /tmp/signal-lab-recorded-evidence.json
```

The next research stage must freeze freshness policies on development data,
evaluate predictive stability by label family and horizon, and keep holdout data
sealed. Only signals that survive latency, cost, and execution sensitivity can
become paper-trading candidates.
