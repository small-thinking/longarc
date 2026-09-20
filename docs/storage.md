# Local storage foundation

## Decision and current scope

Use SQLite on this Mac, outside cloud-synced directories. The initial workload is local and has low write concurrency; transactions serialize writers. PostgreSQL becomes relevant if remote clients or multiple independent writers require server-side coordination. SQLite is embedded: there is no database daemon, listening port or service to restart. Each CLI call opens and closes its own connection.

This implements a **W02 foundation slice**, not full W02 or a trading system. Migration 1 stores append-only operational observations, no-action/error records and externally computed results. It deliberately does not install the entire unaudited v0.1 reference schema. Future migrations will add validated policy, contract, quote, proposal, fill and reconciliation domains. This journal is not a second trade ledger.

Local database: `/Users/Yexi/source/longarc/private/longarc.sqlite3`. The private directory is excluded from Git. Tests use temporary databases. Do not use network/cloud-synced storage for the active database.

## Calling from Codex

From the repository root, use `uv run python -m longarc.cli db ...`. All operations require an explicit database path. No arbitrary SQL write command is exposed.

```bash
uv run python -m longarc.cli db init --db private/longarc.sqlite3
uv run python -m longarc.cli db health --db private/longarc.sqlite3
uv run python -m longarc.cli db save --db private/longarc.sqlite3 --file examples/observation.synthetic.json
uv run python -m longarc.cli db list --db private/longarc.sqlite3 --scope setup-smoke
uv run python -m longarc.cli db get --db private/longarc.sqlite3 --id RECORD_ID
uv run python -m longarc.cli db backup --db private/longarc.sqlite3 --to private/backups/NEW_NAME.sqlite3
uv run python -m longarc.cli db restore --db private/backups/NEW_NAME.sqlite3 --to private/recovery/NEW_NAME.sqlite3
```

Commands return JSON with status, timestamp, source, quality, warnings, evidence references, policy/input hash, code version and result. Missing policy is explicitly null; no command grants trading approval. Errors exit nonzero. Python callers can use `initialize`, `health`, `save_observation`, `get_observation`, `list_observations`, and `copy_database` in `longarc.storage.store`.

The example inserts one explicitly synthetic setup record. It is not a live quote, calculation result, portfolio or fill. Retry with the same idempotency key and content returns the original record; reusing the key for changed content fails. A correction uses a new key plus `supersedes_id` in the same scope/mode; prior records remain. List results include history and do not silently represent the latest corrected state.

## Observation contract

Required: idempotency key, scope, mode (`observe/shadow/manual`), kind (`observation/no_action/error/calculation`), timezone-qualified observed time, source, quality, code version, input/result objects and evidence-reference list. Optional: policy hash and superseded record ID.

Synthetic quality and shadow mode must occur together. `verified` requires evidence references, but it remains a caller assertion: the journal does not verify external evidence or license permissions. Evidence references and policy hashes are not yet foreign keys to an implemented evidence/policy domain. Manual observations are not verified trades. A `calculation` event records supplied results; this tool does not yet calculate or independently validate them.

The journal records observation time separately from database receipt time and hashes canonical inputs and content. Unknown values stay null. Use integer microdollars for monetary payload fields (`*_u`, 1 USD = 1,000,000); the generic JSON payload layer does **not** enforce every business unit or formula. Full Money/Contract/Quantity validation and market-vs-provider-received-vs-known-at semantics remain W02 work. NaN, infinity and timezone-less timestamps are rejected.

## Operational checks and recovery

Migration receipts include exact DDL hashes and application access rejects mismatched/unknown migration receipts. Existing unrelated databases are not reset. Foreign keys and a five-second busy timeout are enabled per connection; writes use explicit transactions, WAL and FULL synchronous mode. The migration checksum is a version check, not protection against an administrator directly changing SQLite files/schema. SQL triggers reject observation updates/deletes; local file-owner access is not a security isolation boundary.

After a process failure, rerun health and retry the same idempotency key. SQLite rolls back uncommitted writes; committed records persist. If a lock persists, identify the process holding the transaction rather than deleting journal/WAL files. Machine sleep stops application work; this delivery does not install a scheduler or background collector.

Backup uses SQLite's online backup API, including committed WAL data. Restore validates integrity, foreign keys and supported migration, and writes only to a **new** destination. Never overwrite a live database. Verify restored records by ID before explicitly switching a caller to the recovered path. Backups are local snapshots, not off-machine disaster recovery or automated scheduling.

## Audit against the source plan

- The original SQL is a design reference, not an applied migration. Its run-status events, cross-entity provenance, and historical replay require application validation beyond foreign keys.
- LongArc's existing OHLCV/Parquet code is retained and tested, but does not supply option chains, Greeks, account state or execution.
- Existing local calculator inspection found candidate metric/portfolio/roll functions at commit `ce97a39`; the reuse audit has not validated its monetary precision, inputs or policy compatibility. No calculator code was copied or executed.
- Calculator integration, market-data authorization, full domain tables, approved policy validation, trading proposals/fills, replay, scheduler and alerts remain pending. W00 and W02 must not be marked wholly complete based on this slice.

Sources: [SQLite suitability](https://www.sqlite.org/whentouse.html), [WAL](https://www.sqlite.org/wal.html), [online backup](https://www.sqlite.org/backup.html).
