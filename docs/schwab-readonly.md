# One-session Schwab read-only analysis

Read `private/qqq-covered-call-plan/README.md` first for the user's current preferences and unresolved parameters. That private file is local, ignored by Git and absent from a fresh clone. If missing, report the missing context rather than importing old defaults. This runbook includes a repeatable browser-assisted collector; it is not an unattended service, scheduler or approved numerical strategy.

The existing [longarc-development skill](../.codex/skills/longarc-development/SKILL.md) routes user-requested analysis here. It is the entrypoint; this document is its maintained procedure reference, not a separate skill or browser program. Update this file when the procedure changes rather than creating another runbook.

## Multiple-symbol reviews

Use the same procedure for QQQ, IAU or an explicitly requested stock/ETF CALL
underlying. Resolve the exact symbol and its policy path from the private context;
do not copy QQQ thresholds to another asset implicitly. For a combined review,
read the account once, then evaluate each symbol with separate shares, option
obligations, pending-order coverage and policy hashes. Reconcile any proposed
buyback cash across the whole account before discussing combined sizing.

Pass `{symbol: "IAU", ...}` to `collectSchwabChain` on the observed IAU Options
research page. The bridge checks the exact symbol route before interacting; all
canonical inputs, decisions and research scopes retain that ticker. For selected
rows use `manual-schwab-selected-rows-v2` with explicit `symbol`. Do not rename old
unknown-layout evidence into a supported format without checking its field mapping,
preserving source IDs, original times and missing values.

Use `--symbol IAU` for history and estimate reports; their backwards-compatible
default is QQQ. Decision and replay requests identify their own symbol and require
matching `scope.underlying` in the supplied policy. Record confirmed executions
with exact `contract.symbol` and a distinct episode per asset. Performance can
report each asset and account totals without combining stock gains/losses with
option P&L. Realized-only output does not measure open-option risk or expected income.

IAU having no regular distribution is not a hardcoded calendar exemption: verify
current product/event facts before setting `dividend_window_clear`. Report unknown
fees, source times or contract terms as unknown. A newly supported ticker does not
itself authorize a policy change, a trade, or a scheduled review.

## Before reading

1. Confirm the current checkout exposes the required CLI. The calculation implementation was merged in PR #14. An older checkout may still lack it. Run `uv run python -m longarc.cli --help`; `calc` documentation is `docs/calculations.md` in that implementation. Do not recreate missing functions or merge unrelated PRs automatically.
2. Use the installed browser-use tool and its current instructions to locate the existing authorized Schwab tab. Discover tabs and page state afresh; never reuse tab IDs/selectors from an earlier session. Ordinary read-only navigation, expiry selectors and scrolling are sufficient. Do not click bid/ask trade buttons, order builders, preview/submit/cancel controls, account settings, or extract cookies/tokens. If login or MFA requires user action, explain the blocker.
3. Create a unique run identifier and explicit UTC capture start/end times. This is one user-requested run. No autonomous schedule, credentials storage or background monitoring is implied.

## Required collection checklist

Missing data is allowed. Every review must account for the required collection
attempts, not obtain every value. Continue all calculations supported by available
inputs; name the missing inputs for each unavailable conclusion. Collection
completeness, numerical calculability and policy eligibility are separate. Never
turn an unavailable field into zero or a passed policy check. Existing risk evidence
still takes priority over unrelated missing inputs.

Before browsing, create a per-symbol checklist under the existing private run
folder. Use the same run ID as the capture and report:

```bash
uv run python scripts/collection_checklist.py --init QQQ > private/RUN/checklist.json
# Populate observed sources, timestamps, references and reasons during collection.
uv run python scripts/collection_checklist.py --file private/RUN/checklist.json > private/RUN/checklist-audit.json
```

Replace RUN with the actual run directory. The script is an offline process audit,
not a browser collector, ingestion gate or trading approval. It lists untouched
items and undocumented assertions while allowing partial-data analysis. Initialize
before collection rather than inventing successful attempts at report time.

Use these statuses for each item and each quote field:

- `observed`: source inspected and value/evidence saved, including a verified empty list.
- `not_provided`: relevant source actually inspected but the field is absent; retain
  the source reference and evidence. This means absent from that source, not unavailable everywhere.
- `blocked`: a specific access/tool failure prevented inspection; retain failure evidence.
- `not_applicable`: explain why the item does not apply to this run or contract.
- `not_attempted`: skipped, with reason and next step. Never relabel this as source missing.

For observed/not_provided/blocked items save `checked_at`, `source_ref` (URL or
page identity), and `evidence_ref` (private file/record plus field or section).
All other statuses need a reason too. Keep raw values in canonical captures or
account evidence, not a second quote ledger. Capture/source clocks remain distinct.

| Checklist item | Required inspection and retained evidence |
| --- | --- |
| session_calendar | Official session date, open/close, timezone, actual run start and any missed target |
| positions | Selected account alias, shares, all option legs, signed quantities, filters and pagination |
| orders | All relevant open/partial/pending orders and visible filled confirmations; coverage reservations |
| buyback_funds | Cash and relevant available-to-trade funds/restrictions; cash is not assumed buying power |
| execution_reconciliation | Prior recorded fills versus visible history; opening price/time precision/fees, unmatched events and history coverage |
| expiry_inventory | Visible listed expiry dates, DTE, target/range from current policy and selection rationale |
| held_and_watch_contracts | All held contracts and estimate watch_contracts; explicit empty sets or blocked coverage |
| four_week_candidates | Inspect policy-eligible expiries nearest the target, including adjacent listed expiries needed to compare; save windows/rejections |
| two_week_candidates | Same process for the research target/range; keep research separate from live recommendations |
| underlying_quote | Symbol, price/basis, source time and capture time for each slice; disclose asynchronous quotes |
| quote_times / greek_times / delay_disclosure | Chain, then relevant read-only quote detail/help/disclosure if absent; independently record each outcome |
| contract_terms | Multiplier, deliverable, adjusted status and expiry from read-only contract details; inferred multiplier alone is not verified terms |
| dividend_events | Current official issuer distribution/corporate-action schedule and coverage through expiry, ex-date/amount when published; no declared future event is not proof none can occur |
| fees | Actual opening total fees and dated closing assumptions, waiver, extra fees and explicit slippage; unknown fees remain unknown |

For each captured contract append `contracts: [{contract_id, fields: {...}}]`.
Use exact symbol/expiry/type/strike for contract_id. Each field has the same status
object as a checklist item. Account for bid, ask, last, Delta, Theta, Gamma, Vega,
IV (preserve displayed units), volume, OI, bid/ask size, Probability of Touch and
Probability of OTM. Enable available research columns using permitted controls;
if unavailable, record the attempt instead of deriving probabilities from Delta.
The script lists these field names in `QUOTE_FIELDS`. `observed` field references
should identify the exact canonical slice/row/cell or normalized quote.

Freeze requested coverage before observing outcomes. Held contracts are always
tracked even when their tenor no longer ranks first. Do not simply reuse yesterday's
candidate expiry. For example, with a 28-day target and eligible listed expiries
24 and 31 days away, inspect the 31-day expiry too; retaining the held 24-day
contract alone cannot establish the best new-entry tenor. Broaden strike windows
only as needed to find candidates in the policy Delta band. If bounded collection
stops early, list uninspected expiries/windows; do not claim full-chain optimality.

Use the existing bridge and canonical ingest. If the bridge fails to associate a
table with its expiry, record that failure; a manual fallback must preserve exact
headers/cells and explicit expiry, and use the same checklist. Do not silently
reuse ad-hoc scripts, old timestamps or old account evidence as fresh evidence.
Do not repair browser code inside scheduled checks.

Before finalizing, run the checklist audit, inspect every not_attempted item and
complete safe relevant follow-ups where possible. A blocked run can finish with
missing data; its report must say what was attempted and what remains unknown.
Save the checklist/audit with the existing report observation (inputs/results or
private evidence references), ingest available quotes and read back saved records.
No new tables or historical backfill are required. Old reports without a checklist
remain legacy coverage unknown, never retrospectively complete.

Every recommendation includes contract/quantity, bid/ask, Delta, premium per
contract, strike-minus-spot dollars and percent with their timestamps, and provider
Touch/OTM probabilities or explicit missing status. Reports summarize the checklist:
collected, source missing, blocked, skipped, and which analyses each gap affects.
Missing optional Greeks should not suppress available cost calculations; missing
policy-critical evidence may still prevent a complete HOLD/entry decision. This
process changes collection discipline, not numerical policy thresholds.

## Read account facts first

Use visible Positions and Orders views for the intended account. Record an account alias locally, not account numbers in reports or commits. Read the selected underlying shares and all of its option legs, contract identities and signed quantities, and relevant open/partial/pending orders. Check visible timestamps, filters and pagination; a filtered or collapsed list is not proof that no calls/orders exist. Read cash relevant to potential buybacks; buying power is not automatically cash or an approved spending budget.

Classify only after the view is complete:

- Shares with no short call: evaluate opening candidates and doing nothing.
- Shares with short calls: compare continuing to hold, closing, and possible replacement calls.
- Missing shares, unexpected long/adjusted positions, pending fills/assignment or disagreement with the prior log: report the observed discrepancy. Do not force it into the expected two states.

Use current visible state for this run; use prior records to detect changes, not overwrite current quantities. Reserve coverage for relevant pending sell-to-open orders. Do not release obligations for an unfilled buy-to-close order; identify linked replacement orders to avoid claiming a reconciled aggregate from ambiguous legs. Unknown quantities or orders remain unknown. Opening-lot history alone cannot establish current remaining positions.

## Read quotes and candidate coverage

Read the underlying quote and displayed market timestamp, then the held contracts and a bounded set of candidate strikes/expiries justified by the local context. Record which expiries/strikes were actually inspected and any filtering/truncation; never call a candidate optimal across an unread chain.

Capture exact symbol, option type, expiry, strike, multiplier/deliverable when displayed; bid, ask, underlying price, delta, theta, quote/Greek times, and capture times. Keep volume/open interest, probabilities and other visible fields as optional source data. Missing stays null. Page-refresh time is not market time; last trade is not an executable quote. If contract multiplier is not established, quote-only arithmetic may proceed but size-dependent calculations remain unknown.

Read source freshness/delay notices and market-session state. Do not label weekend/old values live. When relevant to a held/candidate contract, verify dividend/corporate-action and expiration details using visible official information; if unavailable, record that gap rather than manufacture a value. This workflow invents no quote-age tolerance or assignment-probability model.

## Calculate, record and explain

Start contract selection with per-unit quotes, delta, liquidity and expiry comparisons; do not require a preset contract count for this analysis. Actual coverage, total cash requirements and position exposure still require observed quantities. Use recorded snapshot changes with explicit observation intervals; disclose missing history rather than inferring a trend.

Use the existing CLI/functions for money and scenario arithmetic. Use explicit quantities and total fees, preserving missing inputs. Apply the current offline rules from the private context/policy consistently; never loosen an exit threshold during a run to accommodate a losing position. Compare candidates under labeled assumptions only for unresolved parameters; this is not a policy pass. Persist the policy version with each analysis. Include the no-action alternative. Confirm live state again before presenting a time-sensitive conclusion if collection was interrupted or took long enough to make inputs questionable.

For the future dry run, the existing `db save` observation envelope can retain the following in `inputs`/`results`; these are payload conventions, not new tables or a new implemented API:

| Record | Minimum contents |
| --- | --- |
| Account/collection | run ID, capture interval, account alias, shares/legs/orders/cash as observed, view completeness, source times, missing values and source references |
| Calculation | exact inputs and source record IDs; use `calc` to obtain immutable result ID/hash/version; do not claim execution from hand arithmetic |
| Analysis | source and calculation IDs, context version/hash, assumptions/unapproved parameters, candidates inspected, comparison/rejection reasons, tentative next action or no-action, conditions requiring re-read/recalculation, unresolved facts |

Use the existing explicit `--db` path, unique idempotency keys per record/check, correct mode/quality (`observe`/`unverified` for unverified visible data; `shadow`/`synthetic` for fixtures), and timezone-aware observed times. Real account data belongs only under ignored `private/`. Use `db get` to verify writes. Do not fabricate fills/holding lots from an account snapshot or alter old facts in place. A generic analysis JSON row does not implement reconciliation or a validated recommendation engine.

If source inputs or calculations fail, save the available evidence and missing/error reason where the existing journal accepts it. If storage fails, explicitly report that the run was not persisted. Log no-action outcomes as well as tentative recommendations. Fresh runs reread the account; they never depend on yesterday's chat memory.

## Evidence for comparing exit thresholds

The existing holding snapshots already preserve delta, bid/ask, underlying price and market/capture times. Reuse them; do not add a table for each threshold or store only a triggered/not-triggered summary. The existing observations envelope can retain the study metadata below alongside source and calculation IDs. This is a collection/replay contract; automatic event detection and comparison reports are not implemented by this document.

- **Stable comparison unit:** study/episode ID, exact contract identity, opening reference and actual credit if known, starting observation time, live-position versus quote-only research role, active policy version/hash and compared thresholds. Fix the comparison window and other exit rules before interpreting results. A contract identity is not a substitute for a particular opening episode.
- **Observability:** run ID, quote/Greek/capture times, actual observation intervals, session state, source availability, duplicate market observations and collection failures/gaps. Repeated page reads of the same timestamp/value do not establish fresh independent observations. Missing Greek timestamps prevent claims about exact crossing time.
- **Every check:** retain below-threshold, above-threshold and missing-data checks, including no action. Preserve bid/ask, underlying and observed Greeks, DTE and references to the source snapshot. Store advice separately from user action, order status and confirmed fills; never treat a recommendation as execution.
- **Observed crossings:** derive from ordered, usable observations of the same contract/episode. A below-to-at/above transition identifies an observed crossing interval, not the exact instant. Several consecutive above-threshold snapshots form one observed excursion. A first observation already above threshold is left-censored; after a data gap the intervening path is unknown. Use first observed crossing per episode as the primary frequency measure, and report recrossings separately rather than counting each snapshot as a trigger.
- **Fair threshold comparison:** compare all configured thresholds on the same underlying contract path and observation window. Keep profit/time/dividend exits and fees consistent. Report both threshold crossings on the research path and actual policy exits; a later hypothetical crossing after a different rule already exits is not an executable policy outcome. Do not imply the full strategies share the same future path after they choose different replacements.
- **After an actual close:** at subsequent user-requested checks, continue the original contract as quote-only research through its expiry when available. Put these quotes in generic observations with `scope=research:<study_id>:<contract>`, `mode=observe`, `quality=unverified`, and a research-role marker; real quotes are not synthetic shadow data. Do not add them as an active holding, fabricate fills, or create a recurring task implicitly. If collection stops, mark the end of coverage and the unresolved counterfactual.
- **Replay outcome:** per episode, compare first observed crossing interval, ask-based estimated close cost/loss per unit, later observed maximum delta/ask and whether the alternate threshold was subsequently seen. Actual filled costs remain separate from quote estimates. Record whether a policy would already have exited for profit/time/dividend reasons. Use the same fee assumptions; unknown costs remain unknown. Counterfactual assignment cannot be reconstructed merely from prices.

A later summary should show observed episode counts, their sampling cadence/completeness, first-crossing counts for each threshold, additional quoted close cost from waiting, and post-exit observed paths. Do not label a subsequent price reversal a proven unnecessary exit. Missing intervals are not evidence that no trigger occurred; manual checks cannot establish intraday hit frequency. Many snapshots from one market move are not many independent strategy trials. Full replacement/roll strategy evaluation needs its own reconstructed trajectories and fill assumptions.

## Completion criteria

The report identifies the observed account state and as-of times, the limited candidate universe, explicit assumptions, computed scenarios, missing facts and stored record IDs. Distinguish premium cash received, scenario option P&L and total portfolio return; do not invent expected returns. No order is executed. If a smaller model is used later, judge it by this same evidence/readback checklist; this document does not establish its reliability in advance.


## Repeatable candidate capture and history

Use `scripts/collect_schwab_chain.js` inside the installed Codex browser runtime with the current documented tab binding. It exports `collectSchwabChain(tab, options, observe)`; it is not a standalone browser driver. Read current page state before invoking it. Pass observed expiry dates, strike centers, optional exact watched contracts and independently observed underlying price/time. The `observe` callback must refresh browser state after each action. `includeGreeks: true` enables the visible IV/Gamma/Vega/size columns through Customize. Only research-page controls are used.

The bridge reads bounded expiry/strike windows, retries empty reads up to three times, and returns successful slices plus errors if later collection fails. Save its returned JSON verbatim under ignored `private/`; verify transfer against the returned rows before ingesting. Do not reconstruct missing cells or use cookies/private network requests. Login, changed dialogs or unsupported page state stop the run. The caller must report a storage failure rather than claiming a completed sample.

Schwab's All setting can still show only a strike window. Add explicit centers for wider coverage and inspect recorded ranges; requested-window coverage is distinct from the entire listed chain. The importer deduplicates overlapping windows, preserves raw slices, excludes placeholder rows and records malformed fields. Unknown multiplier and quote/Greek source times stay null. Capture time is the analysis clock, not a fabricated exchange timestamp. IV without a displayed percent sign retains provider units and cannot be pooled as a normalized IV.

```bash
uv run python -m longarc.cli options ingest --db private/longarc.sqlite3 --file private/capture.json
uv run python -m longarc.cli options history --db private/longarc.sqlite3 --start 2026-09-20 --end 2026-10-23 --json-output private/history.json --markdown-output private/history.md
uv run python -m longarc.cli options costs --db private/longarc.sqlite3 --file private/cost-request.json --fees config/options-costs.json
```

History reads `option-chain-v1` capture batches; earlier generic calculation records remain preserved but are not silently converted. It separates exact-contract/source series and reports observed midpoint and Delta changes with elapsed time. Cross-contract descriptives group intervals by initial DTE, Delta and elapsed-time buckets. These are correlated descriptive samples, not independent trials or causal estimates. Sparse intervals are retained without interpolation, daily forward-filling or claims about intraday paths. Missing source timestamps are permitted and labeled; known invalid/future times suppress affected metrics. Repeated known source observations are collapsed; unknown-time repeats cannot be proven fresh.

Coverage reports missing dates, missing watched contracts/expiries, incomplete batches and query truncation. Default expected dates are weekdays, not an exchange holiday calendar; pass `--expected-dates` with a JSON list of actual sessions for an exact check (an empty list disables expected-day checks). A one-point series has no trend. Collection is one run; a scheduler determines when to invoke it. No daily automation is activated by installing this bridge.

Costs use explicit contract count/multiplier and microdollar money inputs. See `tests/test_cost_journal.py` for a complete synthetic request envelope. Every calculation stores the dated schedule, inputs, evidence IDs and code fingerprint. `config/options-costs.json` records standard online commission assumptions; actual total fees override them. Ask-based buybacks already include spread relative to midpoint, so spread is not deducted twice. Additional slippage is explicit. Unknown exchange/other fees leave final net P&L unknown while showing the known-cost subtotal. Rates are dated assumptions, not immutable constants. These are option-leg scenarios, not reconciled portfolio returns.


## Closed-market duplicate reads

For a user-requested read during a verified closed market, pass `--closed-session YYYY-MM-DD` to `options ingest`, using the last closed trading session (not today's calendar date). Verify session state from visible source evidence; do not infer it solely from unchanged prices or weekdays. The same raw coverage and values reuse the first stored record even when collection start/end times differ. Its stored capture time remains the original one, returned as `stored_captured_at`; do not report it as a new market sample or successful coverage on a later day. Source timestamps, values, requested coverage, errors and mode remain part of identity. Changed or improved captures are saved separately. During trading, omit the flag so unchanged prices at different times remain distinct observations.

The general observation journal still rejects conflicting reuse of a key. This capture adapter alone returns the original evidence for an equivalent capture, including retries after a code upgrade. No schema migration or deletion of old duplicates is performed. Repeated checks that reuse a snapshot do not add a database attempt log; their execution receipt reports `existing`.

Suggested request: “Use the LongArc read-only workflow to read the current Schwab QQQ candidates, ingest quotes/Greeks, and generate history and fee-aware comparisons. Verify closed-market state and deduplicate against its last session if applicable. Do not place orders.”


## Rule evaluation and actual executions

After capture/calculation, use `options decide` with the current explicit policy JSON and verified facts; see [decision and actual-result contracts](calculations.md#deterministic-decisions-and-actual-results). Retain the decision ID/hash. The caller records evidence for usability/dividend/position checks and distinguishes unknown from false; the rule script does not verify those claims independently. Historical patterns and LLM commentary cannot bypass an existing policy trigger.

After a user-reported execution, inspect supplied or authorized visible execution confirmation and record actual fills/fees using `options execution-add`; a recommendation or submitted/unfilled order is not a fill. Record partial executions separately with stable IDs and link each close to its opening. Preserve policy/decision IDs, use the same episode through roll legs, and verify writes. Use `options performance` to report recorded cumulative option results with its open-risk/stock-P&L limits. No transaction is executed by these tools.

## Selected-row records and ad-hoc reports

Every new quote review must use `options ingest` before downstream calculations,
and reference its returned record ID. Prefer the browser bridge's canonical
`schwab-browser-chain-v1` envelope even for selected rows: retain headers, explicit
expiry per slice, captured time, available source times, requested coverage and
errors. Do not save quotes only inside a generic narrative observation. Prob. OTM
and Prob. Touching columns are normalized when displayed with percent units.

Existing `manual-schwab-selected-rows-v1` files are accepted through `--file`.
For previously logged supported manual collectors, use:

```bash
uv run python -m longarc.cli options ingest --db private/longarc.sqlite3 --observation-id SOURCE_ID --closed-session YYYY-MM-DD
```

Use the closed-session flag only under the verification rule above. An explicit
`results.closed_session` on source evidence is preserved; a conflicting flag fails.
Intervals with a tagged closed-session endpoint are excluded from changes because
collection elapsed time is not market elapsed time. Older pairs with neither
endpoint tagged remain unverified; the importer cannot reconstruct their session. The importer
supports `manual-evidence-v1` selected tables and `manual-tenor-comparison-v1`
explicit expiry rows. Other layouts fail rather than guessing. It preserves the
original record, links derived evidence, retains original capture time and mode,
and does not inherit missing fields from prose or other observations. Repeating
the same import returns the existing normalized record. Make a database backup
before a historical import and verify counts, readback and source preservation.

Generate history whenever requested; no recurring report is necessary. Repeated
closed-session reads remain audit points but do not produce price/delta change
intervals, including comparisons against older untagged records. Missing active-session paths cannot be recovered by
format conversion. The history report describes observations; it does not simulate
entry, buy-to-close, roll costs, assignment or monthly expected income.


## Continue a research episode

On each user-requested review, read `options estimate`'s `watch_contracts` and include
those existing simulated obligations in the requested capture coverage, alongside
current entry candidates. Follow the established two-/four-week candidate-selection
procedure; fix each candidate before observing its later outcomes. Save canonical
captures first, then append their IDs and contemporaneous source checks to the
existing replay request. `options replay` reruns the shared policy and preserves
revision history; `options estimate` produces the updated ad-hoc report. See the
[replay contract](calculations.md#policy-replay-and-adaptive-descriptive-estimates).

Never set source checks to true merely to obtain a number. If quote/Greek times,
dividend window, market state or a pending contract's price cannot be established,
record the missing evidence and keep the cycle incomplete. The first valid future
entry is a new episode, not a retroactive fill against old closed-market quotes.
Research size, fee/slippage and sampling tolerances are explicit assumptions,
separate from live-policy approval. No scheduled collection is enabled by this flow.
