-- Holding lots record opening inputs, not current reconciled positions.
CREATE TABLE holding_lots (
    holding_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    account_alias TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('shadow','manual')),
    symbol TEXT NOT NULL,
    option_type TEXT NOT NULL CHECK(option_type='CALL'),
    expiry_date TEXT NOT NULL,
    strike_u INTEGER NOT NULL CHECK(strike_u > 0),
    multiplier INTEGER NOT NULL CHECK(multiplier > 0),
    opening_contracts INTEGER NOT NULL CHECK(opening_contracts > 0),
    opening_premium_u INTEGER NOT NULL CHECK(opening_premium_u >= 0),
    opening_fees_u INTEGER CHECK(opening_fees_u IS NULL OR opening_fees_u >= 0),
    opened_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    source TEXT NOT NULL,
    evidence_ref TEXT,
    external_execution_id TEXT,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    UNIQUE(account_alias, mode, external_execution_id)
) STRICT;
CREATE INDEX holding_account ON holding_lots(account_alias, mode, opened_at);
CREATE TABLE holding_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    holding_id TEXT NOT NULL REFERENCES holding_lots(holding_id),
    captured_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    quote_at TEXT,
    greeks_at TEXT,
    source TEXT NOT NULL,
    quality TEXT NOT NULL CHECK(quality IN ('synthetic','unverified','missing')),
    underlying_price_u INTEGER,
    bid_u INTEGER,
    ask_u INTEGER,
    last_u INTEGER,
    delta REAL,
    theta REAL,
    volume INTEGER,
    content_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json))
) STRICT;
CREATE INDEX holding_snapshot_time ON holding_snapshots(holding_id, captured_at, recorded_at);
CREATE TRIGGER holding_lots_no_update BEFORE UPDATE ON holding_lots
BEGIN SELECT RAISE(ABORT, 'holding lots are append-only'); END;
CREATE TRIGGER holding_lots_no_delete BEFORE DELETE ON holding_lots
BEGIN SELECT RAISE(ABORT, 'holding lots are append-only'); END;
CREATE TRIGGER holding_snapshots_no_update BEFORE UPDATE ON holding_snapshots
BEGIN SELECT RAISE(ABORT, 'holding snapshots are append-only'); END;
CREATE TRIGGER holding_snapshots_no_delete BEFORE DELETE ON holding_snapshots
BEGIN SELECT RAISE(ABORT, 'holding snapshots are append-only'); END;
