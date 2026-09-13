-- Revocation and pairing expiry, for a database created from the schema.sql
-- that predates them. Fresh databases get the same shape from schema.sql.
--
-- Apply BEFORE deploying the Worker that needs it. The new Worker writes
-- owner_hash on every pairing, so running it against the old tables makes
-- /pair/register fail. The old Worker never names these columns, so it keeps
-- working against the new tables and the order is safe that way round.
--
-- Run it once. SQLite's ADD COLUMN has no IF NOT EXISTS, so a second run fails
-- at the first ALTER with "duplicate column name".

ALTER TABLE pending_pairs ADD COLUMN owner_hash TEXT;
ALTER TABLE devices ADD COLUMN owner_hash TEXT;
ALTER TABLE devices ADD COLUMN revoked_at INTEGER;

CREATE INDEX IF NOT EXISTS idx_devices_owner ON devices(owner_hash);
CREATE INDEX IF NOT EXISTS idx_pending_owner ON pending_pairs(owner_hash);

-- Every pairing that was never claimed and has passed the 600-second window.
-- Until now nothing removed these, and each holds a KEK beside its ciphertext:
-- a production EasyPost key readable from the database alone.
DELETE FROM pending_pairs WHERE created_at < CAST(strftime('%s', 'now') AS INTEGER) - 600;
