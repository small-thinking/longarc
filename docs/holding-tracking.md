# Multiple holding lots and repeated observations

## Scope

Migration 2 adds **holding_lots** and **holding_snapshots**. Together with schema_migrations and observations, the database now has four tables. Existing observations are retained unchanged. This is a narrow read/write increment; it does not implement position reconciliation, exits, rolls, assignment, tax lots or trading decisions.

A holding lot is one user-supplied short-CALL opening execution/lot, with its own account alias, contract, opening quantity, premium, fee, time and source. Two openings of the same contract remain two distinct lots. An order with multiple fills can be recorded as separate execution lots. The opening premium is a supplied execution price, never inferred from the option chain. Inputs remain provisional even with a supplied evidence reference. Missing actual execution details block lot creation; missing fees stay null.

A snapshot belongs to exactly one lot and must repeat the matching contract identity, preventing accidental attachment of a different strike or expiration. Every new check appends a snapshot, with or without a transaction. Identical retried submissions reuse their IDs; same-key changed content fails. One market observation may be submitted for several lots with distinct idempotency keys; these copies are not independent market samples.

## Screenshot mapping

The supplied Schwab image shows calls/puts, expiration groups, strike, bid/ask/last, change, delta, theta, session volume, Prob.Touching and Prob.OTM. It does **not** show the underlying symbol/price, quote/Greek timestamp, multiplier or actual opening executions. No holdings or screenshot quotes were imported.

| Fields | Storage and interpretation |
| --- | --- |
| Contract | Explicit symbol, CALL, expiry date, strike and multiplier in holding_lots; repeated identity validated for snapshots. Current slice is short calls, not puts or stock holdings. |
| Opening facts | opening_contracts, opening_premium_u, opening_fees_u, opened_at, external_execution_id, source/evidence. Initial quantity is not current remaining quantity. |
| Core observations | underlying_price_u, bid_u, ask_u, last_u, delta, theta, volume. Unknown stays null; no interpolation or default zero. |
| Times | captured_at (time observed), quote_at and greeks_at (nullable source times), recorded_at (system insertion). Capturing an old quote now does not make the quote current. |
| Optional display fields | prob_touching, prob_otm, change_u, evidence_ref and quality_notes in payload_json; no need for dedicated indexed columns yet. |
| Provenance | source, quality (synthetic/unverified/missing), stable IDs and content hash. Shadow observations cannot be attached to manual lots or vice versa. |

Amounts use integer microdollars: 1 USD = 1,000,000 units. Opening premium and bid/ask/last are quoted per underlying unit; opening fees are the **whole lot's** fees. Multiplier is explicit, never defaulted to 100. The current identity is not sufficient for adjusted deliverables: do not import adjusted/nonstandard contracts until a deliverable model exists. Opening lots are not evidence that enough stock exists to cover them.

Delta/theta retain the provider's **long-option quote convention**, not the signed short-position exposure. Theta's provider units/model and timestamp should be described in quality_notes; do not infer dollar/day portfolio exposure from an unexplained display value. Provider probabilities are fractions (90.42% => 0.9042), retained as provider estimates, not treated as assignment risk or derived from delta. The screenshot's Delta and Prob.OTM columns are distinct inputs.

Crossed quotes can be retained as evidence; data-quality gating and financial calculation remain pending. History returns the latest N snapshots ordered by capture time, with total_snapshots and truncated metadata, preserving quote_at as unknown if absent. This is stored history, not an interpolated trend chart or a verified current-position view. Corrections to opening lots and snapshots are not implemented yet; do not delete/re-enter a mistaken real lot under a different key as if it were another trade.

## Tools

```bash
uv run python -m longarc.cli db holding-add --db private/longarc.sqlite3 --file LOCAL_OPENING.json
uv run python -m longarc.cli db holdings --db private/longarc.sqlite3 --account ACCOUNT_ALIAS --mode manual
uv run python -m longarc.cli db snapshot-add --db private/longarc.sqlite3 --file LOCAL_SNAPSHOT.json
uv run python -m longarc.cli db history --db private/longarc.sqlite3 --id HOLDING_ID --limit 100
```

See [exact migration DDL](../src/longarc/storage/schema_v2.sql). Both tables are STRICT and append-only, with primary/unique keys and time indexes; snapshots reference their holding. External execution ID uniqueness is per account/mode. Migration 1's checksum/content remains unchanged. Back up v1 first, then `db init` applies migration 2; reads/writes require current schema, while health/backup can operate on known v1. Do not run migrations concurrently. Recovery uses the supported new-destination-only backup/restore flow.

## What to log at each check

- Lot ID and exact contract; source/capture time and available quote/Greek times.
- Core observed values, optional provider fields, source reference and missing/stale/error explanations. No-action checks still get snapshots; complete collection failures can use a missing snapshot if contract/lot are known, or an observations error event.
- Once calculation is implemented: snapshot/lot IDs, explicit price basis (bid/ask/mid), input hash, calculator version, policy hash if applicable, outputs and warnings in observations(kind=calculation). Do not compute using initial opening quantity after partial closes until remaining quantity is reconciled.
- Actual BTC/roll/assignment belongs in a future execution-event ledger. A recommended action, no-action conclusion and actual fill are different facts.

## Next small calculation increment

Start with a pure bid/ask midpoint and spread function plus a thin read/calculate/record adapter. Return missing/invalid for absent or crossed quotes; retain raw snapshots. Handle odd microdollar midpoints explicitly rather than silently rounding. Then add DTE with an explicit exchange timezone, OTM distance (requires underlying price), and conservative BTC-cost scenarios (requires current reconciled quantity, ask basis, fees and freshness).

Old calculator review found float money, timezone-naive dates, clipped negative extrinsic value, default multiplier 100, and first-matching holding selection. Do not clone it wholesale. Strategy thresholds do not change merely because more fields can be stored: separate factual metrics from policy decisions. Probabilities, delta thresholds, stop/profit/roll rules and observation frequency remain candidate policy settings requiring independent review.

References: [Schwab delta/probability discussion](https://www.schwab.com/learn/story/options-delta-probability-and-other-risk-analytics), [Schwab Greeks](https://www.schwab.com/options/options-greeks). These support field interpretation, not account/data permission or validation of the screenshot's model.

## Calculation follow-up

[Calculation and audit loop](calculations.md) adds pure metrics and explicit-snapshot history comparisons. Optional `underlying_at` is retained in snapshot JSON, without changing version-2 DDL. Current position quantities remain unreconciled.
