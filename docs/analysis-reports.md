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
