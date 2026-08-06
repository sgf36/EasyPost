-- easypost-mobile-proxy D1 schema.
--
-- Key privacy: no plaintext EasyPost key and no KEK are ever stored past the
-- moment of pairing. `devices` holds only ciphertext; the KEK that decrypts it
-- lives on the paired phone. `pending_pairs` briefly holds the KEK between the
-- desktop registering and the phone claiming, then that row is deleted.

-- Short-lived rendezvous between a desktop that has registered a key and the
-- phone that is about to claim it. Deleted on claim or when it expires.
CREATE TABLE IF NOT EXISTS pending_pairs (
  pairing_token   TEXT PRIMARY KEY,   -- one-time token shown in the desktop QR
  ciphertext      TEXT NOT NULL,      -- base64url AES-GCM ciphertext of the key
  iv              TEXT NOT NULL,      -- base64url 12-byte GCM IV
  kek             TEXT NOT NULL,      -- base64url KEK, handed to the phone then deleted
  license_order   TEXT NOT NULL,      -- licence order id this pairing is bound to
  license_tier    TEXT NOT NULL,
  created_at      INTEGER NOT NULL    -- unix seconds; TTL enforced in code
);

-- A paired phone. Holds the key ciphertext but NOT the KEK, so a dump of this
-- table cannot recover any EasyPost key.
CREATE TABLE IF NOT EXISTS devices (
  device_token    TEXT PRIMARY KEY,   -- long-lived bearer token held by the phone
  ciphertext      TEXT NOT NULL,      -- base64url AES-GCM ciphertext of the key
  iv              TEXT NOT NULL,
  license_order   TEXT NOT NULL,
  license_tier    TEXT NOT NULL,
  platform        TEXT,               -- 'ios' | 'android'
  push_token      TEXT,               -- APNs/FCM registration token (push phase)
  created_at      INTEGER NOT NULL,
  last_seen       INTEGER,
  revoked         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_devices_order ON devices(license_order);
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_pairs(created_at);
