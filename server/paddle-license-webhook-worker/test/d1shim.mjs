// A local stand-in for the Worker's D1 binding, over node:sqlite (Node 22.5+).
//
// D1 is SQLite, so the same SQL runs here unchanged, including the upsert
// WHERE clauses and julianday() the revocation and subscription rules depend
// on. Only the call shape is imitated: prepare().bind().first()/all()/run().
//
// The tables mirror the columns the Worker reads and writes. There is no schema
// file in the repository to load instead; if one is added, load it here so the
// tests cannot drift from production.
import { DatabaseSync } from "node:sqlite";

export function makeD1() {
  const db = new DatabaseSync(":memory:");
  db.exec(`
    CREATE TABLE devices (order_id TEXT, device TEXT, label TEXT, platform TEXT, tier TEXT, seats INTEGER, first_seen TEXT, last_seen TEXT);
    CREATE TABLE revocations (order_id TEXT PRIMARY KEY, reason TEXT, revoked_at TEXT);
    CREATE TABLE subscriptions (sub_id TEXT PRIMARY KEY, status TEXT, period_end TEXT, tier TEXT, updated_at TEXT);
    CREATE TABLE activation_log (order_id TEXT, device TEXT, action TEXT, outcome TEXT, at TEXT);
    CREATE TABLE promo_redemptions (transaction_id TEXT PRIMARY KEY, discount_id TEXT, at TEXT);
  `);
  const wrap = (sql, args = []) => ({
    bind: (...a) => wrap(sql, a),
    first: async () => db.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: db.prepare(sql).all(...args) }),
    // D1 reports affected rows under meta.changes; match that shape so code
    // reading it is exercised the way production exercises it.
    run: async () => {
      const r = db.prepare(sql).run(...args);
      return { success: true, meta: { changes: Number(r.changes) } };
    },
  });
  return { prepare: (sql) => wrap(sql), raw: db };
}
