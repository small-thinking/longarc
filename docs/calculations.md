# Calculation and audit loop

`longarc calc --db PATH --file REQUEST.json` runs pure short-CALL calculations and appends one observation. It accepts either explicit quote inputs or immutable snapshot IDs. Multiple holdings retain separate histories; no trade is needed to record a new check. This is a calculation dry run, not a policy engine or reconciled trading ledger.

## Run a synthetic example

```bash
uv run python -m longarc.cli db init --db private/calculation-demo.sqlite3
uv run python -m longarc.cli calc --db private/calculation-demo.sqlite3 --file examples/calculation.json
# Repeat: same record_id and write_status=existing, no duplicate event.
uv run python -m longarc.cli calc --db private/calculation-demo.sqlite3 --file examples/calculation.json
uv run python -m longarc.cli db get --db private/calculation-demo.sqlite3 --id RETURNED_RECORD_ID
```

The fixture's midpoint is $5.10, spread $0.20, and explicit one-contract close scenario is $520.65 including closing fees. Net option P&L is $78.70 after both supplied fees. These are synthetic arithmetic examples.

## Request and units

Required top-level fields: `idempotency_key`, `scope`, `mode`, `source`, `quality`, `as_of`, `evidence_ids`, `inputs`. Optional `policy_hash` is a reference only; no policy is evaluated. All timestamps require timezones. `as_of` is explicit and fixed for reproducible retries. Real browser reads use `mode=observe`, `quality=unverified`; fixtures use `shadow`/`synthetic`. The calculator cannot certify inputs as verified.

Inline inputs contain `contract` and `snapshot`; see the example. Contract: `symbol`, `option_type=CALL`, `expiry_date`, `strike_u`, and `multiplier` (null is allowed for quote-only calculations). Money inputs use integer millionths of a dollar per underlying unit (`1 dollar = 1,000,000 u`). Money outputs use decimal strings, preserving half-micro-unit midpoints. Fractions are unitless; `0.10` means 10%. Greeks retain source units, and are not position Greeks or calculated assignment probabilities.

Snapshot fields used: `bid_u`, `ask_u`, `underlying_price_u`, `delta`, `theta`, `quote_at`, `greeks_at`, `underlying_at`, `captured_at`. `last_u` is validated but never substituted for a bid/ask. Extra source evidence such as last price, volume and displayed probabilities can remain in the snapshot and audit record without becoming decision signals.

For a stored holding snapshot, replace inline `contract`/`snapshot` with `snapshot_id`; set `scope` to `holding:<holding_id>` and match its source/quality. Optional `previous_snapshot_id` must belong to the same holding. The adapter reads the exact IDs, not whichever record happens to be latest. It never uses opening quantity as current quantity. Inline history instead uses `previous: {contract, snapshot}` with matching identity and non-decreasing timestamps. Stored holdings must use IDs for history.

## Implemented metrics

| Group | Definition |
| --- | --- |
| Quote | midpoint `(bid+ask)/2`, spread `ask-bid`, spread/midpoint |
| Time | expiry minus New York calendar date (not trading days or precise expiry hours); separate quote, Greeks, underlying and capture ages |
| Moneyness | signed strike minus underlying; distance/underlying; CALL intrinsic `max(underlying-strike,0)`; midpoint minus intrinsic |
| Observed Greeks | source delta and theta, with their own timestamp; no fitted IV or new Greeks |
| History | changes in midpoint, underlying, delta and theta between explicit snapshots |
| Close scenario | ask × contracts × multiplier; add closing fees; subtract close cost and opening fees from supplied opening credit |
| Premium captured | `(opening premium - ask)/opening premium`, gross and excluding fees |
| Coverage scenario | supplied covered shares / (contracts × multiplier), plus uncovered units; not portfolio reconciliation |
| Roll scenario | replacement bid minus old ask; explicit-quantity net cashflow after both new fees; added calendar days and strike change |

`scenario` accepts only `contracts`, `opening_premium_u` (per unit), `opening_fees_u`, `closing_fees_u` (both total fees for the supplied quantity), and `covered_shares`. Missing quantity, multiplier or fees remain unknown. To inspect a roll, provide `replacement: {contract, snapshot, opening_fees_u}` with the same symbol/type/multiplier. Positive roll cashflow is not profit: the old option's scenario P&L remains separate, and the replacement introduces a new obligation. None of these numbers includes stock P&L, taxes, actual fills or reconciled cumulative episode cashflow.

## What is logged each time

One append-only `observations` row contains the full resolved inputs, snapshot IDs, explicit `as_of`, source/quality, evidence references, policy reference, code fingerprint, input hash, metrics, warnings and status. Stored snapshot IDs also join `evidence_ids`. Repeating the same key/content returns the existing record; changed content with that key fails. A new observation/check requires a new key. `db get` returns the complete audit payload; `db list --scope ...` lists results for a holding or quote study.

Missing inputs produce null outputs and `partial`; crossed quotes produce `invalid` with quote calculations suppressed. Future data are excluded and warned. Negative extrinsic remains visible with a warning, rather than being silently clamped to zero. Each well-formed request is logged even when no trading action occurs or its calculation fails. Malformed calculation inputs are logged as `kind=error`; an invalid request header, unreadable file, invalid JSON, unavailable DB or conflicting idempotency key cannot create a new record and returns an error with null `record_id`. CLI exits nonzero for invalid/error, zero for ok/partial. Always inspect status and warnings.

`ok` means this arithmetic had no missing/invalid-input warnings; it does not mean data are fresh, a strategy passed, or a trade is approved. Ages are returned without inventing a freshness threshold. Missing quote timestamps stay unknown; page refresh time is not quote time. The code fingerprint covers the calculator and logging adapter source, not an external policy or market feed.

## Storage compatibility and remaining work

**No tables, columns, constraints, indexes or migration receipts change.** Schema stays at version 2. `holding_snapshots.payload_json` additionally accepts optional `underlying_at`, validated as a timestamp. It is omitted when absent so retries of pre-existing snapshot payloads keep their original hashes. Generic calculation/error rows use the existing observations table. Backups and readback work unchanged.

The existing local covered-call-advisor formulas were inspected at commit `ce97a394e47334584295e2fef9f87eb027dbe5a9`. The arithmetic definitions inform this implementation, but its float money, implicit multiplier/cashflow defaults, timezone-free dates and clamped extrinsic are incompatible with this storage contract. The new pure layer uses explicit inputs and independent arithmetic tests, with no import of the old policy defaults.

Strategy thresholds remain unchanged and unapproved. Next: specify source freshness/Greek units and a minimal policy contract, then add manual fill/reconciliation records before treating scenarios as current holdings. Automatic collection, scheduling/alerts, candidate ranking, assignment/dividend modeling and live-trading readiness are still separate work.


## Symbol scope and compatibility

All new requests should carry the exact canonical uppercase ticker; spelling is
not corrected automatically. Interfaces remain standard short CALLs with explicit
share coverage and contract multipliers. Adding a ticker does not validate its
contract deliverable, source availability or trading policy.

| Interface | Symbol and isolation |
| --- | --- |
| Browser collector | `options.symbol`, default QQQ for old callers; must match the exact observed Schwab stock/ETF Options URL before any UI actions |
| Canonical capture | Required top-level `symbol`; scopes become `options:<symbol>`; conflicting slice/requested-contract symbols and PUT declarations fail |
| Selected rows | `manual-schwab-selected-rows-v2` requires top-level `symbol`; v1 and legacy tenor imports remain QQQ and reject conflicting declarations |
| Decision | `facts.symbol` must equal policy `scope.underlying`; replacement must be the same symbol and CALL; logged under `options:<symbol>:decisions` |
| Cost scenario | Optional top-level `symbol`, legacy default QQQ; logs under `options:<symbol>:costs` |
| Execution | `contract.symbol`; account/mode/execution-ID identity stays unchanged; close matches the full opening contract; an episode cannot cross symbols |
| Replay | `contract.symbol`, legacy default QQQ; all source scopes, source symbols, policy and replacement must agree; logs under `options:<symbol>:replays` |
| History / estimates | `--symbol`, legacy default QQQ; replay groups and paired comparisons never combine underlyings |
| Performance | No symbol means account aggregate plus `by_symbol`; `--symbol IAU` filters the ledger; unknown fees affect that asset and aggregate net results |

A policy's existing `policy_parameters` and `roll` fields are unchanged. Add
`"scope": {"underlying": "IAU"}` to an explicitly chosen IAU policy JSON.
Omitting scope is supported only for legacy QQQ policies; a present but incomplete
scope is invalid. Policy hashes retain the full supplied policy. New IAU numerical
parameters are a separate decision, not inferred from enabling its scripts.

`dividend_window_clear` remains an evidence-backed check for every symbol. A
verified lack of distributions can justify true; the ticker IAU alone does not.
The engine still accepts delta as delta, not a calibrated assignment probability.
Coverage and pending orders must be verified for the same account and symbol;
shared buyback cash cannot be independently budgeted in full for both assets.

```bash
uv run python -m longarc.cli options history --db private/longarc.sqlite3 --symbol IAU --start 2026-09-21 --end 2026-10-23
uv run python -m longarc.cli options decide --db private/longarc.sqlite3 --file private/iau-decision.json --policy private/iau-policy.json
uv run python -m longarc.cli options estimate --db private/longarc.sqlite3 --symbol IAU
uv run python -m longarc.cli options performance --db private/longarc.sqlite3 --account LOCAL_ALIAS --symbol IAU
uv run python -m longarc.cli options performance --db private/longarc.sqlite3 --account LOCAL_ALIAS
```

No tables, columns, types, constraints or indexes change. Existing QQQ records are
not rewritten; their scopes and actual-execution idempotency keys remain valid.
Legacy replay results without a symbol are interpreted as QQQ. Revised calculations
retain their version fingerprints; do not reuse a calculation idempotency key for
changed inputs or code. No historical fills or quote timestamps are invented.

## Deterministic decisions and actual results

`options decide` applies the explicitly supplied private policy's thresholds, with priority: verified position → known risk/time/dividend exit → profit exit → WATCH/eligible roll → HOLD. Flat positions screen one STO candidate; they do not automatically size or rank a portfolio. Source usability, current position, coverage, orders, dividend calendar, sizing and re-entry cooldown are explicit evidence-backed caller checks. `true` is not inferred from a recent capture time. Missing critical checks block HOLD/entry; known risk signals still return a risk exit even with unrelated missing data. An expired contract requires settlement reconciliation. Closed-market output requires rechecking before execution.

The request contains `as_of`, `idempotency_key`, `source`, `mode`, `evidence_ids`, `facts` and an optional `replacement`. Policy JSON contains the existing `policy_parameters` and `roll` sections. `tests/test_decisions.py` supplies complete **synthetic** input examples. Monetary fields are integer microdollars; `allocated_opening_fees_u` applies to the remaining quantity being evaluated, and `estimated_closing_total_fees_u` is the entire proposed close's all-in cost. Roll credit is calculated from old ask/new bid and both legs' fees, with matching quantity/multiplier. Policy values stay in ignored local files. If maintaining YAML, explicitly export its current version to JSON before invoking the script; no default policy is silently loaded.

Every decision saves full facts/policy, policy hash, source references, code fingerprint, per-check states and reasons. No-action and malformed evaluations are journaled; missing journal metadata cannot be fabricated. A changed request needs a new idempotency key. LLM commentary may explain market context or missing evidence but cannot override a triggered rule in the same policy version. Changing thresholds is a separate, versioned policy choice, not automatic learning from recent P&L. Risk/entry outputs are advisory candidates, never broker orders or proofs of readiness.

```bash
uv run python -m longarc.cli options decide --db private/longarc.sqlite3 --file private/decision.json --policy private/policy.json
uv run python -m longarc.cli options execution-add --db private/longarc.sqlite3 --file private/confirmed-execution.json
uv run python -m longarc.cli options performance --db private/longarc.sqlite3 --account LOCAL_ALIAS --mode manual
```

The execution journal accepts only evidence-backed short-call executions/events with explicit symbols, including QQQ and IAU: STO, BTC, confirmed EXPIRE and confirmed ASSIGN. Required fields are `account`, `mode` (manual/shadow), `external_execution_id`, `action`, exact `contract`, `quantity`, `price_u`, `fees_u` (null if unknown), `executed_at`, `source`, `evidence_ref`, `episode_id`. BTC/EXPIRE/ASSIGN also require `opening_execution_id`. Optional `policy_hash` and `decision_record_id` connect results to strategy/decision provenance. See `tests/test_executions.py` for synthetic requests. References are retained, not independently authenticated by the parser.

Each close explicitly allocates to one opening; split a multi-lot fill into uniquely identified allocation records and split its fees without duplicating them. Import each lot chronologically. Roll is two actual legs sharing an episode, so new credit never erases the old realized loss. Replays of account/mode/execution ID are idempotent; different content conflicts. Transactions prevent concurrent overclosing. The derived remaining quantity reflects only recorded events, not an independently reconciled broker position. Existing `holding_lots` records are not automatically imported, because provisional openings must not become actual fills.

Reports include gross/net realized option P&L, per-close cumulative net, episode totals, remaining lots and realized-only maximum drawdown. Opening fees are allocated proportionally to closed quantities, with the final allocation conserving the total. Unknown fees make affected net totals unknown. Assignment option premium is shown as an option-leg bookkeeping result, not tax accounting; stock sale/cost-basis P&L remains unknown. Opening cash credits and unrealized P&L are not realized gains. No historical executions are invented, and an empty journal is not evidence that no trades ever occurred. Corrections/busted trades are not yet supported: report an error instead of adding a duplicate compensating opening.

### Learning as observations accumulate

Initially use the explicitly versioned rules, current verified facts and clearly labeled market-mechanics reasoning. Do not claim locally estimated probabilities or expected returns from a few snapshots. Collect both acted-on and no-action decisions, exact fills/fees, gaps, and post-exit research observations where available.

As data grow, compare like-for-like DTE/Delta regimes and strategy versions, showing numbers of completed episodes, calendar coverage and missingness. Later empirical calibration needs held-out or walk-forward checks and transaction costs; correlated quotes are not independent trials. Choosing a threshold on the same trades used to advertise its performance overstates evidence. Record any policy change prospectively.

Realized option P&L alone cannot measure strategy risk or value over buy-and-hold. That needs contemporaneous marks for all open options and stock, dividends, cash flows and a matching underlying benchmark. Open losses, sacrificed upside and assignment effects must be included before claiming portfolio drawdown, return, Sharpe ratio or strategy superiority. This increment does not implement that portfolio valuation or empirical policy optimization.

## Policy replay and adaptive descriptive estimates

The replay uses `decisions.evaluate`, the same entry/exit/roll policy function as
`options decide`. It does not maintain another set of trading thresholds. Candidate
selection remains explicit: fix the contract at the first observation, using the
existing candidate-selection procedure. Do not choose a better entry retrospectively.
A research tenor comparison changes only a copy of the policy's entry DTE range;
never rewrite the live policy. Use the same episode ID for a paired start, distinct
policy hashes for the two variants, and identical execution assumptions. Pairing
requires the same New York entry date; elapsed holding periods can differ, so cycle
P&L comparisons alone do not establish which tenor earns more per calendar month.

```bash
uv run python -m longarc.cli options replay --db private/longarc.sqlite3 \
  --file private/replay-request.json --policy private/policy.json --fees private/fees.json
uv run python -m longarc.cli options estimate --db private/longarc.sqlite3 \
  --json-output private/estimates.json --markdown-output private/estimates.md
```

Replay request contract (all numbers here are synthetic interface examples):

```json
{
  "episode_id": "study-start-001",
  "as_of": "2026-09-23T20:00:00Z",
  "contract": {"symbol": "QQQ", "expiry_date": "2026-10-16", "strike_u": 110000000},
  "assumptions": {
    "contracts": 1, "multiplier": 100, "covered_shares": 100,
    "opening_slippage_u": 10000, "closing_slippage_u": 20000,
    "opening_extra_fees_u": 0, "closing_extra_fees_u": 0,
    "max_quote_age_seconds": 60, "max_greeks_age_seconds": 60,
    "max_underlying_age_seconds": 60, "max_gap_hours": 72,
    "allow_roll": false
  },
  "observations": [
    {"record_id": "CANONICAL_CHAIN_ID_AT_ENTRY", "checks": {
      "market_open": true, "quote_usable": true, "greeks_usable": true,
      "underlying_usable": true, "dividend_window_clear": true,
      "standard_contract": true, "reentry_cooldown_clear": true
    }},
    {"record_id": "CANONICAL_CHAIN_ID_AT_NEXT_CHECK", "checks": {
      "market_open": true, "quote_usable": true, "greeks_usable": true,
      "underlying_usable": true, "dividend_window_clear": true,
      "standard_contract": true, "reentry_cooldown_clear": true
    }}
  ]
}
```

`--fees` uses the existing cost schedule: dated `as_of`, `base_fee_u`,
`per_contract_fee_u`, and `btc_waiver_threshold_u`. Extra fees and slippage are
explicit research assumptions, including an explicit zero; these are not approvals
of live execution settings. Values ending `_u` are integer millionths of dollars.
Contract multiplier and covered shares are simulated sizing, never inferred holdings.
A different assumed size/cost/freshness/roll configuration forms a separate group.

Each frame references a saved canonical chain batch; the engine loads prices,
Greeks, spot and source times from that record. Flags alone cannot make missing,
future or stale source timestamps usable. Flags for market state, contract type,
calendar/dividend window and cooldown still require contemporaneous evidence from
the read-only review. Closed-session records cannot create simulated fills. Missing
entry facts stop that attempt; the engine does not search forward for a favorable
entry. At later checkpoints, known policy risk signals still take priority even
when unrelated fields are missing.

A frame may include `replacement` with `expiry_date` and `strike_u`, plus
`replacement_checks` in the same shape as `checks`. It is used only when
`allow_roll=true`. New-leg prices must be present in that frame's source batch.
The existing policy must permit the roll, and net credit must remain nonnegative
after the assumed slippage and both legs' fees. Entry fills use bid minus opening
slippage; BTC uses ask plus closing slippage. A profit fill additionally requires
positive modeled net after slippage; a risk exit is not blocked because it loses
money. Each closed leg charges its own opening and closing fees once. A roll's
new premium leaves an open obligation: it is not immediately realized income.

Append observations chronologically to the same request and rerun. An episode's
original entry and observation prefix cannot be rewritten; corrections/alternative
entries need a distinct, clearly labeled episode ID. Replay logs are shadow/synthetic,
linked to quote evidence, and versioned by policy, execution assumptions, source
mode and replay code. No actual execution journal or holdings are modified.
Repeated identical requests return the same record. `estimate` selects only the
newest revision of each episode/policy/assumption group and fails on a query limit
rather than silently truncating. Its `watch_contracts` output lists outstanding
simulated obligations to include in the next authorized read.

With one completed eligible cycle, estimates report its mean and observed loss
fraction; standard error remains unknown. More completed cycles update the report
without any hard minimum sample threshold. Same-entry-date episodes share a cohort;
cohort counts are not statistically independent effective sample sizes. Descriptive
standard errors require at least two dates. Wilson intervals describe the fraction
of negative **cohort means**, not individual-trade or monthly loss probability.
All-win samples retain a positive upper loss bound. Overlapping dates, changing
markets, missed paths and selection bias limit interpretation.

Open/no-entry/incomplete episodes are counted separately, never zero-filled into
closed-cycle returns. Oversized collection gaps, unresolved exits and missing
critical evaluations make a cycle incomplete; any observed-endpoint P&L remains a
diagnostic. Even an eligible replay describes only the observed-checkpoint strategy,
not continuous monitoring, the true first intraday threshold crossing or assignment.
Maximum gap is a declared research sampling assumption, not proof of path completeness.

Optional `--cycles-per-month NUMBER` scales the equal-weight cohort mean as an
explicit sequential-cycle/constant-size scenario. It is not an empirical monthly
income forecast: idle time, reentry, unequal durations and open obligations prevent
mechanical annualization. Without completed cycles, it remains unknown. Existing
`options costs` supports quote-based what-if arithmetic before paths exist. The
engine does not manufacture outcome probabilities from OTM or delta.

For a paired report, add `--left-policy HASH --right-policy HASH` to `estimate`.
It compares only same-episode, same-assumption, same-entry-date closed pairs and
reports unmatched/incomplete counts. There is no automated threshold tuning,
unattended collection, report schedule, broker execution, automatic candidate
ranking or calendar-month portfolio backtest. Reports are generated on demand.
