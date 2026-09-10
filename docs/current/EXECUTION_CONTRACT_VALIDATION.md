# Independent execution contract validation

The executable probe is `scripts/validate_execution_contracts.py`. It uses explicit fixture expectations, independently of `advance_queue`, and invokes the production execution adapter. Each case is checked on both maker sides. Inputs and a fixture SHA-256 accompany the report.

```sh
python scripts/validate_execution_contracts.py --output artifacts/execution-contracts.json
```

The optional external path runs **HftBacktest 2.4.4 itself**, using its partial-fill exchange and risk-averse queue model. Install this dependency in an isolated validation virtual environment, never the app or collector environment. The version check rejects other releases. Preserve `pip freeze` with the resulting evidence.

```sh
python -m pip install hftbacktest==2.4.4
python scripts/validate_execution_contracts.py --hftbacktest --output artifacts/hft-contracts.json
```

## Scope and intentional differences

The 26 local probes cover queue exhaustion, partial and complete fills, partial-then-full execution, strict price-through, non-executable prices, lot enforcement, cumulative participation capacity, splitting equivalent capacity across messages, and refusing future capacity. The separate pytest module also checks the maker-to-aggressor direction conversion. These are synthetic probes, not the frozen ten-minute market sample. The 16 external comparisons include partial-then-full execution to distinguish delayed position recognition from permanently omitted partial quantities.

External comparison covers same-price and strict-through cases with participation 100% and queue depletion alpha zero. It does not assert equivalence for cancellation/depth attribution, arbitrary alpha, recorded arrival clocks, participation stress, or multi-order execution. Both engines receive the same integer nanosecond timing convention and normal trade quantity. Trade direction is reversed from maker to aggressor; depth direction is preserved. Own orders are active strictly before the trade.

HftBacktest's documented strict-through rule fully executes a resting order. Our adapter clears the inferred ahead queue but bounds the fill by observed normal trade quantity. Thus an order of 0.003 with ahead queue 1 and a through trade of 0.001 intentionally yields 0.003 externally and 0.001 internally. This is visible in each comparison row, not hidden behind a numerical tolerance. [Official fill model](https://hftbacktest.readthedocs.io/en/latest/order_fill.html)

The external probe records **both order quantity minus remaining quantity and reported portfolio position**. These must not be treated as interchangeable without reconciliation. An external accounting discrepancy produces `ACCOUNTING_DISCREPANCY` and a nonzero exit, even if execution-contract quantities match. Version 2.4.4's matching crate source contains a Filled-only branch in local fill accounting; partial-fill behavior therefore needs the explicit runtime check. Do not alter our Decimal ledger to reproduce an external discrepancy. [Published local processor source](https://docs.rs/crate/hftbacktest/0.9.4/source/src/backtest/proc/local.rs)

`NOT_RUN` means no external engine was executed; absence of the optional dependency when requested is a failure, not a skip counted as parity. `COMPLETED` reports the scoped comparisons only. Neither outcome establishes actual exchange queue position, market impact, live profitability, or paper readiness. [Trade and depth side definitions](https://hftbacktest.readthedocs.io/en/latest/reference/constants.html), [pinned package](https://pypi.org/project/hftbacktest/2.4.4/)

## Task evidence

- Files changed: validation module, CLI script, focused pytest module, this document.
- Commands run: stdlib local validation CLI and direct 26-case probe.
- Official docs checked: order fill, constants, initialization, backtester API, PyPI release, published Rust queue/exchange/local processor sources.
- Tests passed: all 26 local quantity probes at implementation time; authoritative pytest and external reports are recorded by the integration run.
- Tests failed: the first external integration reported partial-fill position discrepancies; investigation retained the original expected quantity and added order/position reconciliation.
- Runtime observations: validation is bounded, synthetic and offline; external dependencies stay isolated.
- Data written: local ignored validation JSON; no captured market data changed.
- Open risks: independent implementation agreement is not real-execution calibration; exact parity is intentionally not claimed.
- Recommended next action: inspect persisted external order and position evidence before approving any external accounting comparison.
