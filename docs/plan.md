LongArc — Execution Plan & Design Doc (Cursor-ready)

Goal: ship a minimal end-to-end system (historical backtest + paper trading), then iterate toward a multi-strategy, multi-asset, observable, operable system. Default: non-HFT (minute/hour/day frequency). Priority: stability, reproducibility, auditability. Risk note: no performance guarantees. Start with paper trading/testnet.

0. Scope & Assumptions

0.1 Initial scope (MVP)

Support one market first: prefer US equities (paper trading) or crypto (testnet).

Support one timeframe: 1m / 1h / 1d (recommend 1d or 1h first).

Support one starter strategy: e.g., SMA crossover or N-day momentum.

Support one execution mode:

Backtest

Paper trading / Testnet

Minimal risk controls: max position, max order size, max daily loss / max drawdown kill switch.

0.2 Non-goals (Phase 1)

No HFT (microsecond latency, order book microstructure alpha, colocation).

No full-blown feature store / research platform (keep extension points).

No multi-broker routing (abstract the interface; implement one adapter first).

1. High-level Architecture (Iterative)

1.1 Layered design

Data layer: historical/real-time bars ingestion, caching, persistence

Research & Backtest: reproducible backtesting engine

Strategy: market data → signal → target position / intents

Portfolio & Risk: signal → tradable orders, risk constraints

Execution: broker/exchange adapter for paper/testnet

Ops & Monitoring: logs, metrics, alerts, safety controls

1.2 Runtime shape (recommended for non-HFT)

Scheduled / batch-driven (cron / APScheduler):

Each cycle: fetch latest data → compute signal → generate orders → execute → persist

Replayable:

Same strategy code used for backtest and paper trading (via provider/adapter abstraction)

1.3 ASCII diagram

+-------------------+       +------------------+
|  Scheduler / CLI  | ----> |  Trading Engine  |
+-------------------+       +------------------+
                                     |
                                     v
      +-----------+     +-------------------+     +--------------+
      | Data      | --> | Strategy          | --> | Portfolio    |
      | Provider  |     | (signals)         |     | + Risk       |
      +-----------+     +-------------------+     +--------------+
                                     |
                                     v
                              +--------------+
                              | Broker       |
                              | Adapter      |
                              +--------------+
                                     |
                                     v
                           +--------------------+
                           | Storage + Logs     |
                           | Metrics + Alerts   |
                           +--------------------+

2. Technology Choices (Defaults)

2.1 Language & tooling (Hybrid: Python + Rust)

We adopt Option B (recommended):

Python: research, indicators, and backtesting (fast iteration; strong ecosystem)

Rust: trading engine service (risk, portfolio/order generation, broker adapters, persistence, monitoring)

TypeScript: web dashboard (optional; later milestone)

Rationale:

This is a non-HFT system. The key constraints are correctness, safety, auditability, and operability, not microsecond latency.

Python accelerates strategy iteration and backtest development.

Rust reduces runtime risk in long-lived services (state machines, idempotency, concurrency, reliability).

Tooling (defaults):

Python 3.11+

Dependency management: poetry or uv (pick one)

Quality: ruff + mypy + pytest + pre-commit

Rust stable (edition 2021)

Workspace: Cargo workspace in repo

Quality: clippy + rustfmt + cargo test

Cross-language boundary:

Start with file-based JSON (strategy outputs) or HTTP/gRPC once stable.

Keep a stable schema for Bar, TargetPositions, OrderIntent, Order, Fill.

2.2 Storage

MVP: local sqlite (orders/fills/positions/runs) + parquet (historical bars)

Later: Postgres + S3/MinIO (optional)

2.3 Observability

Logs: structured JSON

Metrics: Prometheus (Rust exporter) / prometheus_client (Python) (later Grafana)

Alerts: simple webhook interface (Telegram/Slack/email via user integration)

2.4 Repository layout (monorepo, hybrid)

Use a single repo with two packages:

py/ for Python research & backtest

rs/ for Rust trading service

Example:

longarc/
  py/
    pyproject.toml
    src/longarc_py/
    tests/
  rs/
    Cargo.toml
    crates/
      engine/
      broker/
      storage/
      monitoring/
  shared/
    schemas/   # JSON schema / protobuf definitions (optional)
  config/
  scripts/

2.5 GitHub Repo Bootstrap Checklist (Day 0)

Naming convention

GitHub repository name: longarc (recommended)

Product / project display name: LongArc

Avoid hyphens in the repo name to keep Python import paths and tooling simpler (long-arc is fine for Rust crate names later if desired).

When creating a new GitHub repository:

When creating a new GitHub repository:

Repository basics

Initialize: README.md, LICENSE (MIT/Apache-2.0), .gitignore (Python + Rust + Node)

Default branch: main

Protect main: PR required, status checks required

CI (GitHub Actions)

Python: ruff, mypy, pytest

Rust: cargo fmt --check, cargo clippy, cargo test

Optional: cache deps for speed

Pre-commit (optional but recommended)

Python: ruff format/lint

Rust: rustfmt

Keep hooks fast; run full checks in CI

Secrets & configuration

Store API keys in GitHub Actions secrets (only when needed)

Keep .env.example and config.example.yaml committed; never commit real .env

Conventions

Commit messages: Conventional Commits (optional)

Release tags: v0.x.y when the first stable API boundary is defined

2.6 Evolution Path: From Classic System to Agent

This project starts as a classic systematic trading system. Later it may evolve into an agentic trading system.

2.6.1 What “agentic” means here (scope)

The agent can propose changes (strategy params, universe, risk thresholds) and run evaluations.

The agent does not auto-deploy to live/paper without explicit approval gates.

2.6.2 Architectural choices to enable iteration

Keep the core engine deterministic and auditable. Treat “agent outputs” as inputs/config proposals.

Define stable contracts:

StrategyOutput (e.g., target positions)

ConfigPatch (a structured diff) + justification metadata

EvaluationReport (backtest/paper results) with run_id links

Add extension points (plugins):

strategy plugins

risk rule plugins

broker/data provider adapters

Add an "experimentation lane":

Agent runs backtests in an isolated workspace

Produces reports + patches

Human approves to promote patches into production configs

2.6.3 Safety gates (required for any agent step)

Read-only by default; write actions must be gated

Kill switch always enforced by the engine (agent cannot bypass)

All agent actions are logged with inputs/outputs and run_id references

3. Repository Layout (Cursor should generate accordingly)

trade_system/
  README.md
  pyproject.toml
  .env.example
  config/
    config.example.yaml
  scripts/
    run_backtest.sh
    run_paper.sh
  src/longarc/
    __init__.py

    core/
      types.py              # Bar, Order, Fill, Position...
      time.py               # timezone / calendar basics (can be minimal in MVP)
      logging.py            # logger init
      config.py             # config load & validation (pydantic)
      errors.py             # unified exception types

    data/
      base.py               # DataProvider interface
      providers/
        alpaca.py           # example: Alpaca market data (optional)
        binance.py          # example: Binance market data (optional)
      store.py              # local cache & persistence (parquet/sqlite)

    broker/
      base.py               # Broker interface
      adapters/
        alpaca_paper.py     # Alpaca paper adapter (optional)
        binance_testnet.py  # Binance testnet adapter (optional)
      paper_sim.py          # pure local simulator (no external API)

    strategy/
      base.py               # Strategy interface
      sma_cross.py          # starter strategy
      momentum.py           # optional

    risk/
      rules.py              # risk rules
      sizing.py             # position sizing / order sizing

    engine/
      trading_engine.py     # fetch->signal->risk->execute->record
      backtest_engine.py    # backtest engine (start simple)

    storage/
      db.py                 # sqlite init
      models.py             # ORM models or simple DAO
      repo.py               # repositories for orders/fills/positions/runs

    monitoring/
      metrics.py            # metrics definitions
      alerts.py             # alert interface + default console implementation

    cli.py                  # Typer/argparse: backtest/paper/run/report
  tests/
    test_strategy_sma.py
    test_risk_rules.py
    test_broker_paper_sim.py
    test_backtest_engine.py

4. Key Interfaces (Implement interfaces first, then details)

4.1 Core Types (required)

Bar: ts, open/high/low/close, volume

Order: client_order_id, symbol, side, qty, type, limit_price, ts, status

Fill: order_id, filled_qty, filled_price, fee, ts

Position: symbol, qty, avg_price, unrealized_pnl, realized_pnl

Requirement: reuse these types across backtest/paper/live.

4.2 DataProvider interface

get_historical_bars(symbol, timeframe, start, end) -> list[Bar]

get_latest_bar(symbol, timeframe) -> Bar | None

get_clock() -> datetime (single time source; local time in MVP)

4.3 Broker interface (paper/testnet)

place_order(order: Order) -> OrderAck

cancel_order(order_id) -> CancelAck

get_open_orders()

get_positions()

get_account() (optional)

Idempotency: client_order_id must be supported; repeated requests must not duplicate orders.

4.4 Strategy interface

on_bars(symbol_to_bars: dict[str, list[Bar]], state) -> StrategyOutput

Two output modes (choose one; recommend target positions):

A) target_positions: dict[symbol, target_qty]

B) intents: list[OrderIntent]

4.5 Portfolio & Risk

Inputs: strategy output + current positions + account + prices

Output: final executable orders

Must implement:

max position (qty or notional)

max order notional

max daily loss / max drawdown triggers KILL_SWITCH (halt or close-only)

5. Data & Audit (Do this early)

5.1 Minimal tables (sqlite)

runs: run_id, mode(backtest/paper), start_ts, end_ts, status, git_sha(optional)

orders: client_order_id, broker_order_id, symbol, side, qty, type, price, status, ts

fills: broker_order_id, fill_ts, qty, price, fee

positions_snapshots: ts, symbol, qty, avg_price, pnl

5.2 Reproducibility

Backtest inputs pinned (persisted data + optional hash)

Config pinned (store config snapshot/hash per run)

Parameter changes must be traceable via runs

6. Milestones (Execution Plan)

Each milestone must deliver: runnable command + minimal tests + artifacts (logs/report). Cursor should implement M0→M1→M2… sequentially; tests must pass before moving on.

M0: Scaffold & engineering base (1–2 days)

Deliverables

repo structure + lint/test passing

config.example.yaml + .env.example

structured logging

Acceptance

python -m longarc.cli --help works

pytest green

M1: Data layer MVP (historical bars + local cache) (1–3 days)

Scope

Implement one DataProvider:

Option A: local CSV/Parquet provider first (most stable)

Option B: connect external market data API (later)

Commands

longarc data download --symbols AAPL --timeframe 1d --start 2020-01-01 --end 2024-01-01

trade_system data show-latest --symbol AAPL --timeframe 1d

Acceptance

idempotent downloads (no duplicate writes)

tests: missing/duplicate/out-of-order data

M2: Backtest engine + starter strategy (SMA cross) (2–5 days)

Scope

Implement backtest_engine: historical bars → orders/fills/PNL

Minimal cost model: fee bps + slippage bps

Command

trade_system backtest --config config/config.example.yaml --start 2020-01-01 --end 2024-01-01

Outputs

report: total return, annualized, max drawdown, win rate, trade count, trade ledger (csv)

Acceptance

reproducible results given same data+config

tests for edge cases: insufficient window, gaps

M3: Local Paper Sim Broker (no external API) (2–4 days)

Purpose

Validate the online trading loop end-to-end before integrating any broker API.

Scope

broker/paper_sim.py:

market orders (limit later)

fill at latest bar close (MVP), optional slippage

engine/trading_engine.py:

one-cycle loop: latest bar → signal → risk → orders → persist

Command

trade_system paper-sim run --config ... --steps 2000 (drive “online” using historical bars)

Acceptance

resumable after crash (load state from sqlite)

idempotent ordering via client_order_id

M4: Integrate real Paper/Testnet (pick one) (2–6 days)

Recommended

US equities: Alpaca paper

Crypto: Binance/Bybit testnet

Scope

Implement one BrokerAdapter:

env-based auth

place/cancel/query

Implement matching DataProvider for latest bars

Robust error handling: retries, rate limits, circuit breaker

Command

trade_system paper run --config ...

Acceptance

orders/positions visible in external paper/testnet account

correct handling of network failures & API errors

M5: Monitoring + alerts + “low ops” (2–5 days)

Scope

Metrics: cycle latency, order success rate, slippage, PnL, drawdown, exposure

Alerts: N consecutive failures, missing data, drawdown breach, position mismatch

Acceptance

unattended run is safe: auto-stop/close-only + alert + full audit trail

M6: Strategy & portfolio extensions (ongoing)

multi-symbol portfolio + rebalancing

multi-strategy (signal aggregation)

stronger risk: vol targeting, VaR/ES (optional)

better execution simulation: limit order, book-based fills (optional)

7. Configuration Design (config.yaml)

7.1 Principles

everything configurable; no hard-coded params

store full config snapshot (or hash) per run

7.2 Example

mode: backtest  # backtest | paper_sim | paper
timezone: America/New_York

universe:
  symbols: ["AAPL"]
  timeframe: "1d"

data:
  provider: "local_parquet"  # local_parquet | alpaca | binance
  path: "./data"

broker:
  adapter: "paper_sim"  # paper_sim | alpaca_paper | binance_testnet

strategy:
  name: "sma_cross"
  params:
    fast_window: 20
    slow_window: 100

portfolio:
  base_currency: "USD"
  initial_cash: 100000

risk:
  max_position_notional: 20000
  max_order_notional: 5000
  max_daily_loss: 1000
  kill_switch: true

cost_model:
  fee_bps: 1.0
  slippage_bps: 2.0

runtime:
  schedule: "0 9 * * 1-5"   # optional cron
  dry_run: false

8. CLI Design (Cursor should implement)

trade_system data download ...

trade_system backtest --config ... --start ... --end ...

trade_system paper-sim run --config ... --steps ...

trade_system paper run --config ... (external paper/testnet)

trade_system report --run-id ... (generate outputs under reports/)

9. Testing (Minimum)

9.1 Unit tests (required)

strategy: insufficient window, signal flips, no data

risk: position/order caps, kill switch triggers

paper_sim broker: idempotency, fills, position accounting

9.2 Integration tests (recommended)

fixed dataset backtest produces stable summary metrics

paper_sim run for 1000 steps: no errors, consistent DB state

10. Ops & Security (Minimum from day 1)

secrets in env only; never commit

never log secrets

kill switch configurable and enabled by default

“close-only mode”: after risk breach or repeated errors, no new positions

11. Cursor Task List (Step-by-step code generation)

Feed these tasks to Cursor in order.

Task 1 (M0)

create repo layout

set up ruff/mypy/pytest

implement core/config.py (pydantic) + core/logging.py

implement CLI skeleton (subcommands as placeholders)

add minimal tests; ensure pytest passes

Task 2 (M1)

implement data/store.py (parquet read/write)

implement data/providers/local_parquet.py

implement trade_system data download/show-latest

tests: time sorting, dedup, idempotent writes

Task 3 (M2)

implement strategy/base.py + strategy/sma_cross.py

implement engine/backtest_engine.py

implement minimal cost model

implement trade_system backtest + csv report

Task 4 (M3)

implement broker/paper_sim.py

implement engine/trading_engine.py (single-cycle run)

implement trade_system paper-sim run

implement sqlite storage models + repos

Task 5 (M4, optional)

pick one: Alpaca paper (US equities) or Binance testnet (crypto)

implement broker adapter + data provider for latest bars

add retries/rate limits/circuit breaker

implement trade_system paper run

Task 6 (M5)

implement metrics + alerts (abstract + default console/webhook)

add alert rules: failures, missing data, drawdown, position mismatch

add health checks

12. Extension Points (Defer; keep interfaces clean)

multi-asset, multi-market calendars & fee models

limit orders & better fill simulation

multi-strategy aggregation

web UI: runs/orders/positions/PnL

research: notebook + cache + parameter sweeps

