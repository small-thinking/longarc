# Repeatable read-only analysis

Three commands turn saved observations into JSON and Markdown reports. They do
not write observations, change the schema, infer live account state or place trades.
Reports are generated on demand; no new schedule is installed.

## Data inventory and quality

```bash
uv run python -m longarc.cli options data-audit \
  --db private/longarc.sqlite3 \
  --json-output private/reports/audit.json \
  --markdown-output private/reports/audit.md
```

The whole-database inventory counts physical records, superseded/current records,
record kinds, modes and quality labels, plus provisional holding-table rows. Actual
executions are counted separately from those holding tables. Quote rows are nested
inside observations and must not be added to business-record counts.

Quote analysis separates underlying, mode and source. It reports batch counts,
contract identities, expiries, capture dates, known source-quote dates, missing-field
counts/fractions, partial windows, malformed quotes and quote-level replay blockers.
It preserves ambiguous repeats instead of pretending they are independent samples.
Contract identities include multiplier and option symbol: unknown metadata is not
silently merged with verified metadata.

Optional `--symbol QQQ --start YYYY-MM-DD --end YYYY-MM-DD` filters quote and event
analysis; dates are inclusive **New York collection dates**, not inferred trading
sessions. The explicitly labelled whole-database inventory remains unfiltered.
`--expected-dates FILE.json` accepts an explicit array of session dates for missing
capture checks; without it, missing sessions remain unknown. An explicit symbol
with zero captures still reports all supplied expected dates as missing.

`--max-age-seconds 300` is a disclosed source-age diagnostic at capture, not a new
trading policy. Passing quote prerequisites does not establish replay eligibility:
market/calendar/account/contract checks and the declared replay assumptions remain
necessary. Unknown multipliers are conservatively flagged even though replay may
use a separately declared multiplier assumption. Replay counts are stored revisions,
not independent trials; use `options estimate` for latest episode statistics.

`--limit` defaults to 10,000 total observations and may be raised to 100,000. The
audit fails on overflow rather than silently producing partial totals. SQLite
integrity, schema and foreign-key checks are included.

## Compare a saved candidate window

```bash
uv run python -m longarc.cli options compare-candidates \
  --db private/longarc.sqlite3 --observation-id CANONICAL_CHAIN_RECORD_ID \
  --policy private/policy.json --fees config/options-costs.json \
  --contracts 1 --multiplier 100 \
  --json-output private/reports/candidates.json \
  --markdown-output private/reports/candidates.md
```

Obtain a batch ID from the audit's `batch_evidence`. The command compares exactly
that canonical `option-chain-v1` batch. It rejects superseded evidence and
symbol/policy mismatches. Partial coverage and the capture window remain visible;
one batch does not imply simultaneous quotes or a complete listed chain.

Each row includes bid/ask, Delta, DTE, dollar/percentage distance to strike, spread,
provider Touch/OTM estimates and source timing. Existing entry delta/DTE/OTM/spread/OI
checks are reused. A quote-threshold pass is **not** entry approval: account coverage,
pending orders, source freshness, dividend/calendar and other checks remain unknown.
Candidates retain source order; the report does not choose a winner or blend dates.

The fee scenario explicitly assumes selling at observed bid and later buying back
at exactly half that premium. It reports gross P&L, a known-cost subtotal and complete
net only when costs are known. Quantity/multiplier are scenario assumptions, not
verified holdings or deliverables. This is neither expected income nor an annualized
return; no occurrence probability or time to profit is inferred.

`--as-of` is an optional timezone-qualified evaluation timestamp, defaulting to now.
Candidate analysis can inspect evidence imported later than the original capture;
it is not a historical knowledge-cutoff backtest. Missing/inconsistent capture clocks
prevent quote qualification/scenarios; missing source timestamps retain descriptive
values and explicit uncertainty.

## Track recorded open lots

```bash
uv run python -m longarc.cli options position-report \
  --db private/longarc.sqlite3 --account LOCAL_ACCOUNT_ALIAS \
  --fees config/options-costs.json --symbol QQQ \
  --json-output private/reports/positions.json \
  --markdown-output private/reports/positions.md
```

The report reuses the execution ledger, including partial-close fee allocation.
It separates recorded realized P&L from remaining-lot ask-based close scenarios.
Each lot has its opening time/price, remaining quantity/fees, source-specific latest
quote, Delta/Theta, strike distance and post-opening observation history in JSON.
Premium capture excludes fees and is not realized profit or a strategy return.

`--as-of` defaults to now. Unlike candidate evaluation, positions use both record
time and event/capture time as a historical knowledge cutoff. Later imports and
later fills cannot leak into an earlier report. A later missing/invalid quote does
not silently fall back to a favorable older quote. Conflicting provider streams are
valued separately; no combined mark is invented. `--limit` caps selected account
and quote records at 10,000 by default (maximum 100,000); overflow fails rather than
returning partial financial totals. `--mode manual` joins observed
quotes; `--mode shadow` joins synthetic quotes, keeping modes separate.

Unknown quote multipliers permit only an explicitly conditional scenario using the
recorded execution multiplier and assuming the same deliverable. A known conflict
or ambiguous identity blocks the scenario. `--max-age-seconds` labels source/capture
freshness (default 300); stale/unknown saved quotes remain historical scenarios,
not executable current valuations. Closed-session evidence is labelled explicitly.

No broker reconciliation, stock P&L, tax accounting or trading action is inferred.
An empty ledger is not evidence of zero account income.

## Costs, exports and reproducibility

The dated fee JSON follows the existing `options costs` schedule. Optional
`opening_extra_fees_u` / `closing_extra_fees_u` are **total extra fees for the
scenario leg**, in integer microdollars. Missing extras stay unknown; do not insert
zero unless it is an explicit assumption. Position reports use actual recorded
opening fees and estimated closing fees; candidate reports estimate both legs.
All `_u` fields are millionths of a dollar, with exact decimal strings where needed.

JSON preserves evidence IDs, filters, source clocks, assumptions and warnings.
Markdown presents a readable summary. With no output arguments, JSON goes to stdout.
Output paths cannot overwrite the input database, WAL/SHM, policy, fees or session
list, and JSON/Markdown paths must differ. Keep account reports under ignored
`private/`; only synthetic fixtures belong in Git.

These commands complement `options history`, `performance`, `replay` and `estimate`.
Automated research-path maintenance and calendar-period return reports are future
work. More formulas cannot reconstruct missing source timestamps or market paths.

## Saved IV context and explicit scenarios

```bash
uv run python -m longarc.cli options iv-history \
  --db private/longarc.sqlite3 --symbol QQQ \
  --target-dte 28 --target-delta 0.075 \
  --json-output private/reports/iv-history.json \
  --markdown-output private/reports/iv-history.md
```

This read-only report isolates one source (default `schwab_visible_browser`) and
observed data, excluding explicitly synthetic quality even if labelled observe.
Missing evidence returns `insufficient_comparable_iv`, not zero or
a policy pass. Output paths cannot overwrite the database, WAL/SHM, session input,
or each other. No schema or policy change, scheduling, collection or cache is added.

The default target is 28 **calendar** DTE and 0.075 call Delta, with tolerances of
7 days and 0.025 Delta. Select nearest DTE, then Delta, then expiry/strike in each
batch **before examining IV**. Retain the latest target-bearing batch per New York
date; holding-only captures without a target match do not replace that stream.
A held call inside target tolerances can replace the broader day's sample; consistent
daily neighborhoods are required, and changing coverage remains a comparison limit.
Missing/invalid IV
or source clocks in that sample cannot cause fallback to older favorable values.
This is a bounded nearest-match changing-contract proxy, not an interpolated
constant-tenor surface. Selected contracts and deviations remain in JSON.

Only positive `iv` with `iv_unit=fraction`, valid bid/ask and independent quote/Greek
source times within the disclosed age tolerance (default 300 seconds at capture)
enter statistics. Raw `provider_display` is counted and excluded without guessing.
Fractions assume a consistent source annualization convention; the source IV model
and bid/mid/ask calculation basis remain unverified. Closed-session captures and
weekends are excluded. `--expected-dates` can supply actual exchange sessions;
otherwise holiday and missing-session completeness remain unknown.

`--as-of` is a timezone-qualified knowledge cutoff, default now. Both observation
and database-record times must precede it; later supersession cannot hide evidence
available then. The latest saved target date is the reference. **Exclude that whole
date** from the historical distribution, including its earlier intraday captures.
Source age at capture is separate from the saved reference's age at `--as-of`.

The default lookback is 252 prior observed target dates (`--lookback-samples`), not
a guaranteed year. An explicit session list instead bounds the window to the last
252 listed sessions and exposes gaps. Min/median/max describe usable prior dates.
Percentile/rank require `--min-samples` (default 30; a reporting guard, not statistical
validation or a trading threshold):

- Percentile = 100 × count(prior IV strictly below reference IV) / sample count;
  ties are excluded.
- Rank = 100 × (reference IV − low) / (high − low); the range includes prior samples
  and the reference, keeping rank within 0–100. Flat ranges have unknown rank.

Optional arguments are explicit scenario assumptions, all IV/volatility in fractions:

```bash
# Hypothetical inputs, not current market facts or verified holdings.
uv run python -m longarc.cli options iv-history \
  --db private/longarc.sqlite3 --symbol QQQ \
  --forecast-volatility 0.20 --reference-iv 0.20 \
  --vega-per-vol-point 0.09 --contracts 1 --multiplier 100
```

For usable IV 0.30, the forecast spread is 10 volatility percentage points, or
`0.30² − 0.20² = 0.05` annualized decimal-variance units. This is a model-dependent
**single-call variance-spread proxy**, not model-free VRP, a fitted forecast or dollar
profit. The caller's forecast must have been available at the quote time, match the
remaining calendar-expiry horizon and use consistent annualization; those assertions
are not verified. Trailing RV comparisons are descriptive. Future RV is an outcome,
unavailable at entry; empirical work needs real licensed bars and actual sessions
inside that calendar horizon, not 28 trading days substituted for 28 calendar DTE.

IV-only short-call sensitivity = `Vega × 100 × (IV − reference IV) × contracts ×
multiplier`, with Vega explicitly **dollars/share per percentage point**. Thus 30%
to 20%, Vega $0.09 and multiplier 100 gives a hypothetical gross $90. Other inputs
(spot/time/rates/dividends) stay constant; other price effects, fees, spread and
slippage are excluded. Larger moves need model repricing because Vega changes.
Without `--reference-iv`, prior median is a scenario reference only after the
sample guard passes; it is not a forecast. Provider Vega units are never inferred.

### Decision use and present-close calculations

Follow the [entry and holding review procedure](schwab-readonly.md#iv-context-in-entry-and-holding-reviews) when using this report. Percentile can enter an explicitly adopted, versioned heuristic sum; its weight remains uncalibrated and no automatic policy threshold is added. This CLI exports IV evidence only: it does not calculate the multi-factor score, detect distribution shift, learn weights or enforce a scoring policy. Current ask-based BTC cost/P&L needs no forward volatility forecast. Optional `--forecast-volatility` is only an explicit assumption for the future variance-spread scenario; it is not required for percentile/rank or present-close arithmetic. IV-only monetary sensitivities are USD, separate from dimensionless historical position and variance units.

### Collection and logging for IV research

Collect the target call neighborhood and ATM reference at a consistent daily point,
including no-entry days. Recompute context before each requested trade review from
its latest separate quote and prior history. Daily statistical sampling and intraday
holding-risk observation have different purposes. Daily samples cannot prove intraday
trigger paths. The command runs on demand and installs no automation.

Retain raw IV/unit labels and source-unit evidence in existing canonical raw captures,
with normalized IV/unit, contract, Delta, spot, bid/ask, quote/Greek/spot source clocks,
capture time and record time. Record IV model/basis/annualization and Vega unit evidence
when supplied; keep absent fields unknown. Unlabelled browser IV requires verified
unit mapping in a future adapter; this report does not relabel historical data.

Export JSON under ignored `private/` to retain method/version, cutoff, parameters,
daily evidence IDs, exclusions, sample denominator, assumptions and limits. Link that
file from the existing review observation when archiving; no second quote ledger is
needed. Actual fills still need decision/policy links, total fees and response times
for later paired studies. The IV report does not reconstruct those missing facts.
