# LOBcap — Market-Making Research Workbench

LOBcap is a reproducible research environment for testing market-making ideas
against captured Binance USDⓈ-M BTCUSDT order-book and aggregate-trade data. It
connects an explicit hypothesis to observable signals, quote decisions, simulated
order lifecycles, fills, portfolio accounting, and the next experiment.

The repository is a research simulator. It does not place live orders and its
bounded results are not evidence of production profitability.

## What is implemented

- Deterministic L2 replay with snapshot bootstrap, Binance Futures sequence checks,
  exchange-time and recorded-arrival clocks, and source-linked event evidence.
- Maker-order entry, cancellation, serial replacement, partial-fill, queue-position,
  latency, fee/rebate, inventory-limit, and stale-book models.
- Exact Decimal accounting with spread capture, inventory mark-to-market, fees,
  drawdown, fill statistics, markouts, and reconciliation invariants.
- A side-by-side Streamlit workbench: strategy settings on the left; comparable
  metrics, time-series charts, order evidence, and experiment history on the right.
- Causal signal research with event-driven features, a separate analysis grid, and
  market-truth, client-forward, and post-latency return labels.
- Durable serial experiment jobs, restricted signal formulas, parameter grids,
  frozen development protocols, and comparison guards.

## Strategy families

| Policy | Quote behavior | Research purpose |
|---|---|---|
| Fixed spread | Places symmetric passive quotes around the observable midpoint. | Baseline for distance, latency, fee, and queue sensitivity. |
| Inventory skew | Shifts the quote center against current inventory. | Tests inventory control separately from predictive alpha. |
| Microprice / imbalance | Shifts the center using observable BBO pressure. | Tests a small, causal short-horizon price signal. |
| Flow withdrawal | Suppresses exposure under adverse observable trade flow. | Tests whether selective participation improves outcomes. |
| GLFT | Estimates past trade-arrival intensity and volatility, then calculates inventory-aware widths. | Tests a practical stochastic-control quotation model. |
| GLFT + microprice | Adds a capped observable price signal to GLFT inventory control. | Separates quotation control from directional signal value. |
| No trade | Never submits an order. | Detects ranking and accounting mistakes around zero activity. |

Every strategy uses the same public `MarketView` boundary. It cannot access future
events, hidden queue truth, replay iterators, or private fill-model state.

## System outline

```text
captured depth + trades + snapshot
              │
              ▼
bounded validation and normalized replay
              │
              ▼
observable features ──► strategy ──► risk and quote policy
              │                         │
              │                         ▼
              └────────────── order lifecycle + fill model
                                        │
                                        ▼
                       accounting, metrics, evidence artifacts
                                        │
                                        ▼
                          Streamlit comparison workbench
```

Active implementation lives in `src/lobcap/backtest/`. The UI entry point is
`apps/backtest_lab.py`. Configuration examples are in `configs/backtest/`.

## Install and test

Python 3.11 or newer is required. Ubuntu is the authoritative runtime because a
legacy research module uses Python's Unix-only `resource` package.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[web]'
python -m pytest -q
```

Native Windows collection of the complete legacy suite is not supported. Focused
backtest modules can still be developed on Windows; final acceptance runs on Linux.

## Run the workbench

The repository intentionally excludes raw market data and generated run payloads.
Prepare or copy a normalized sample under `artifacts/backtest/samples/` and ensure
the selected YAML configuration points to its data root. Then run:

```bash
python -m streamlit run apps/backtest_lab.py \
  --server.address 127.0.0.1 \
  --server.port 8501
```

The default recorded-arrival configuration is
`configs/backtest/binance_recorded.yaml`. The CLI entry points are:

```bash
lobcap-backtest --help
lobcap-workspace --help
```

For a remote machine, keep Streamlit bound to loopback and use an SSH tunnel.
No public firewall rule or authentication service is required.

## Bounded evidence

The checked-in acceptance evidence uses one frozen ten-minute BTCUSDT development
sample. Under identical zero-fee diagnostic assumptions, the tested BBO, fixed
width, inventory, microprice, flow, and GLFT configurations were mostly negative.
Wider quotes reduced fills and losses; one very wide fixed-spread run finished
positive only while holding inventory at the limit. GLFT reduced the loss in that
window but did not produce a paper-trading candidate.

That result is useful: the accounting does not force a loss, but adverse selection,
inventory mark-to-market, and modeled execution dominate the simple policies on
this sample. The UI exposes those components instead of presenting a single PnL.

Detailed evidence and interpretation:

- [Simulation workbench](docs/current/SIMULATION_WORKBENCH.md)
- [GLFT research model](docs/current/GLFT_RESEARCH.md)
- [Signal Research Lab](docs/current/SIGNAL_RESEARCH_LAB_V1.md)
- [Backtest semantics and reproduction](docs/current/BACKTEST_MVP.md)
- [Execution realism and limitations](docs/current/BACKTEST_REALISM.md)
- [Multi-day research protocol](docs/current/SIGNAL_RESEARCH_P0.md)

## Important limitations

- Market-by-price data cannot reveal actual individual queue position. Queue
  advancement is modeled and sensitivity-tested.
- Historical replay cannot measure endogenous market impact. The impact setting is
  a hypothetical cost stress, not a measured response of the market to our orders.
- Binance depth and aggregate trades have separate sequence spaces; their alignment
  is an explicit normalization convention rather than matching-engine chronology.
- The current development evidence is too short for Sharpe, annualization, strategy
  selection, or claims of out-of-sample robustness.
- Funding, liquidation, hidden liquidity, account-specific fees, hedging execution,
  and automatic terminal liquidation are outside the current simulator.

Any paper-trading promotion should require untouched dates, verified fees and
latencies, stable signal behavior, queue/impact sensitivity, and explicit risk gates.

## Repository map

```text
apps/                 Streamlit application
configs/backtest/     sample and strategy configurations
src/lobcap/backtest/  replay, execution, strategies, accounting, research jobs
tests/backtest/       deterministic and focused acceptance tests
scripts/              bounded validators and audit tools
docs/current/         current design, evidence, and research protocols
docs/internal/        development workflow, prompts, and failure history
docs/legacy/          frozen historical references
```

Raw captures, credentials, generated backtest runs, and large compressed outputs
are excluded from Git. Run `python scripts/audit_public_repo.py` before publishing
new commits to check tracked files for credential patterns, private runtime
identifiers, and oversized payloads.

## Project status

The simulator and research workbench are implemented and reproducible on the
authoritative Linux environment. Strategy evidence remains development-only and no
configuration is approved for paper or live trading.
