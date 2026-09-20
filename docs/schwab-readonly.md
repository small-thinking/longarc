# One-session Schwab read-only analysis

Read `private/qqq-covered-call-plan/README.md` first for the user's current preferences and unresolved parameters. That private file is local, ignored by Git and absent from a fresh clone. If missing, report the missing context rather than importing old defaults. This runbook is a manual browser-assisted workflow, not a deployed collector, scheduler or approved numerical strategy.

## Before reading

1. Confirm the current checkout exposes the required CLI. The calculation implementation is PR #14; its merge/checkout state must be checked, not assumed from this document. Run `uv run python -m longarc.cli --help`; `calc` documentation is `docs/calculations.md` in that implementation. Do not recreate missing functions or merge unrelated PRs automatically.
2. Use the installed browser-use tool and its current instructions to locate the existing authorized Schwab tab. Discover tabs and page state afresh; never reuse tab IDs/selectors from an earlier session. Ordinary read-only navigation, expiry selectors and scrolling are sufficient. Do not click bid/ask trade buttons, order builders, preview/submit/cancel controls, account settings, or extract cookies/tokens. If login or MFA requires user action, explain the blocker.
3. Create a unique run identifier and explicit UTC capture start/end times. This is one user-requested run. No autonomous schedule, credentials storage or background monitoring is implied.

## Read account facts first

Use visible Positions and Orders views for the intended account. Record an account alias locally, not account numbers in reports or commits. Read QQQ shares, all QQQ option legs, contract identities and signed quantities, and relevant open/partial/pending orders. Check visible timestamps, filters and pagination; a filtered or collapsed list is not proof that no calls/orders exist. Read cash relevant to potential buybacks; buying power is not automatically cash or an approved spending budget.

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

Use the existing CLI/functions for money and scenario arithmetic. Use explicit quantities and total fees, preserving missing inputs. Compare candidates under labeled assumptions when numerical preferences remain unresolved; this is not a policy pass. Include the no-action alternative. Confirm live state again before presenting a time-sensitive conclusion if collection was interrupted or took long enough to make inputs questionable.

For the future dry run, the existing `db save` observation envelope can retain the following in `inputs`/`results`; these are payload conventions, not new tables or a new implemented API:

| Record | Minimum contents |
| --- | --- |
| Account/collection | run ID, capture interval, account alias, shares/legs/orders/cash as observed, view completeness, source times, missing values and source references |
| Calculation | exact inputs and source record IDs; use `calc` to obtain immutable result ID/hash/version; do not claim execution from hand arithmetic |
| Analysis | source and calculation IDs, context version/hash, assumptions/unapproved parameters, candidates inspected, comparison/rejection reasons, tentative next action or no-action, conditions requiring re-read/recalculation, unresolved facts |

Use the existing explicit `--db` path, unique idempotency keys per record/check, correct mode/quality (`observe`/`unverified` for unverified visible data; `shadow`/`synthetic` for fixtures), and timezone-aware observed times. Real account data belongs only under ignored `private/`. Use `db get` to verify writes. Do not fabricate fills/holding lots from an account snapshot or alter old facts in place. A generic analysis JSON row does not implement reconciliation or a validated recommendation engine.

If source inputs or calculations fail, save the available evidence and missing/error reason where the existing journal accepts it. If storage fails, explicitly report that the run was not persisted. Log no-action outcomes as well as tentative recommendations. Fresh runs reread the account; they never depend on yesterday's chat memory.

## Completion criteria

The report identifies the observed account state and as-of times, the limited candidate universe, explicit assumptions, computed scenarios, missing facts and stored record IDs. Distinguish premium cash received, scenario option P&L and total portfolio return; do not invent expected returns. No order is executed. If a smaller model is used later, judge it by this same evidence/readback checklist; this document does not establish its reliability in advance.
