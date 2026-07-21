/**
 * Seat activation for tiered licences.
 *
 * A tier is just a number of computers, so something has to count them. This is
 * that counter. Three decisions shape the whole file:
 *
 * 1. **Licences are verified, not looked up.** Every request carries the full
 *    licence key, and its Ed25519 signature is checked against the public half
 *    derived from our own signing key. Nothing is consulted about whether the
 *    order "exists". That keeps minting and activation completely decoupled: a
 *    complimentary key minted by hand activates exactly like a purchased one,
 *    with no record to create first and nothing to keep in sync.
 *
 * 2. **The device is a hash we cannot reverse.** The app sends
 *    HMAC-SHA256(licence_key, machine_id). We never see the machine. Two
 *    licences on one computer produce unrelated hashes, so nothing here can
 *    correlate customers.
 *
 * 3. **Possession of the key is proved.** Each request carries an HMAC over its
 *    own fields, keyed by the licence. Knowing an order id is not enough to
 *    burn someone else's seats, and a captured request goes stale quickly.
 *
 * Revocation is the one thing that does require a lookup, because a refund or
 * an abused freebie has to be killable after the fact.
 */

import { createHmac, createPrivateKey, createPublicKey, sign as nodeSign, verify as nodeVerify, timingSafeEqual } from "node:crypto";

const LICENSE_TAG = "EPD1";
const RECEIPT_TAG = "EPDR1";

// A receipt is verified offline, so its life is the only thing that forces a
// customer back to the network. Long enough to be invisible in normal use.
const RECEIPT_DAYS = 400;

// A seat held by a computer we have not heard from in this long is returned to
// the pool. Covers the dead laptop nobody thought to release.
const RECLAIM_DAYS = 180;

// A signed request older than this is refused, so a captured one cannot be
// replayed indefinitely.
const REQUEST_TOLERANCE_SECONDS = 900;

// Cheap brake on someone hammering a key. Counts *failures* only: an IT team
// rolling out 50 machines in an afternoon is exactly the behaviour we sold them,
// and an earlier version of this counted every attempt and would have locked
// the organisation tier out at seat 31.
const MAX_FAILURES_PER_HOUR = 20;

const TIER_SEATS = {
  personal: 3,
  business: 25,
  organisation: 50,
  enterprise: 0, // uncapped
};

function b64url(buf) {
  return Buffer.from(buf).toString("base64url");
}

function fromB64url(str) {
  return Buffer.from(String(str), "base64url");
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

function isoPlusDays(days) {
  return new Date(Date.now() + days * 86400000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

function isoMinusDays(days) {
  return new Date(Date.now() - days * 86400000).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Verify a licence key and return its signed contents, or null.
 *
 * The public half is derived from the private signing key we already hold, so
 * there is no second secret to configure and no way for the two to drift apart.
 */
export function verifyLicense(privatePem, key) {
  const parts = String(key || "").trim().split(".");
  if (parts.length !== 3 || parts[0] !== LICENSE_TAG) return null;

  let payloadBytes;
  let signature;
  try {
    payloadBytes = fromB64url(parts[1]);
    signature = fromB64url(parts[2]);
  } catch {
    return null;
  }

  let ok = false;
  try {
    const pub = createPublicKey(createPrivateKey(privatePem));
    ok = nodeVerify(null, payloadBytes, pub, signature);
  } catch {
    return null;
  }
  if (!ok) return null;

  let payload;
  try {
    payload = JSON.parse(payloadBytes.toString("utf8"));
  } catch {
    return null;
  }
  if (payload.v !== 1 && payload.v !== 2) return null;

  // v1 predates tiers; those keys are already sold, so they read as the entry
  // tier rather than being rejected.
  const tier = payload.tier || "personal";
  const seats = Number.isInteger(payload.seats) && payload.seats >= 0
    ? payload.seats
    : (TIER_SEATS[tier] ?? TIER_SEATS.personal);

  return { order: String(payload.order || ""), email: String(payload.email || ""), tier, seats };
}

/** Constant-time compare of the caller's HMAC proof. */
export function checkProof(licenseKey, fields, proof) {
  const expected = createHmac("sha256", String(licenseKey).trim())
    .update(fields.join("|"))
    .digest("hex");
  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(String(proof || ""), "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

function freshEnough(ts) {
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return false;
  return Math.abs(Date.now() - parsed) <= REQUEST_TOLERANCE_SECONDS * 1000;
}

/** The signed statement the app stores and re-checks offline on every launch. */
export function signReceipt(privatePem, { order, device, tier, seats }) {
  const payload = Buffer.from(JSON.stringify({
    v: 1,
    order,
    device,
    tier,
    seats,
    iat: nowIso(),
    exp: isoPlusDays(RECEIPT_DAYS),
  }), "utf8");
  const signature = nodeSign(null, payload, createPrivateKey(privatePem));
  return `${RECEIPT_TAG}.${b64url(payload)}.${b64url(signature)}`;
}

async function logAttempt(db, order, device, action, outcome) {
  try {
    await db.prepare(
      "INSERT INTO activation_log (order_id, device, action, outcome, at) VALUES (?, ?, ?, ?, ?)"
    ).bind(order, device, action, outcome, nowIso()).run();
  } catch {
    // Logging must never be the reason a paying customer cannot activate.
  }
}

async function isRevoked(db, order) {
  const row = await db.prepare("SELECT reason FROM revocations WHERE order_id = ?")
    .bind(order).first();
  return row ? (row.reason || "withdrawn") : null;
}

async function overRateLimit(db, order) {
  const since = new Date(Date.now() - 3600000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const row = await db.prepare(
    "SELECT COUNT(*) AS n FROM activation_log WHERE order_id = ? AND at > ? AND outcome != 'ok'"
  ).bind(order, since).first();
  return (row?.n ?? 0) >= MAX_FAILURES_PER_HOUR;
}

/** Return seats silently held by long-gone computers to the pool. */
async function reclaimStale(db, order) {
  await db.prepare("DELETE FROM devices WHERE order_id = ? AND last_seen < ?")
    .bind(order, isoMinusDays(RECLAIM_DAYS)).run();
}

async function deviceList(db, order) {
  const { results } = await db.prepare(
    "SELECT device, label, first_seen, last_seen FROM devices WHERE order_id = ? ORDER BY first_seen"
  ).bind(order).all();
  return results || [];
}

/**
 * Shared entry checks: verify the licence, the proof, freshness and revocation.
 * Returns { license } or { error, status }.
 */
async function authorise(env, body, extraProofFields = []) {
  const license = verifyLicense(env.LICENSE_PRIVATE_KEY_PEM, body.license);
  if (!license) return { error: "That licence key is not valid.", status: 401 };

  const db = env.LICENSES;
  const device = String(body.device || "");
  const ts = String(body.ts || "");

  // The brake is checked before the expensive paths but after the licence
  // signature, so it can be scoped to a real order rather than to whoever is
  // shouting loudest.
  if (await overRateLimit(db, license.order)) {
    return { error: "Too many failed attempts on this licence. Try again in an hour.", status: 429 };
  }

  if (!/^[0-9a-f]{16,64}$/.test(device)) {
    await logAttempt(db, license.order, "", "auth", "bad_device");
    return { error: "Malformed device identifier.", status: 400 };
  }
  if (!freshEnough(ts)) {
    await logAttempt(db, license.order, device, "auth", "stale_ts");
    return { error: "Request timestamp is too old. Check this computer's clock.", status: 400 };
  }
  if (!checkProof(body.license, [license.order, device, ts, ...extraProofFields], body.proof)) {
    // The interesting failure: a valid key, but the caller cannot prove they
    // hold it. Worth counting, and worth counting per order.
    await logAttempt(db, license.order, device, "auth", "bad_proof");
    return { error: "Request signature did not match.", status: 401 };
  }

  const revoked = await isRevoked(db, license.order);
  if (revoked) {
    await logAttempt(db, license.order, device, "auth", `revoked:${revoked}`);
    return { error: `This licence is no longer valid (${revoked}). Please get in touch.`, status: 403 };
  }
  return { license, device };
}

export async function handleActivate(request, env, json) {
  const body = await request.json().catch(() => ({}));
  const auth = await authorise(env, body);
  if (auth.error) return json({ error: auth.error }, auth.status);

  const { license, device } = auth;
  const db = env.LICENSES;
  await reclaimStale(db, license.order);

  const existing = await db.prepare(
    "SELECT device FROM devices WHERE order_id = ? AND device = ?"
  ).bind(license.order, device).first();

  if (existing) {
    // Re-activating a computer we already know is not a new seat. Refresh the
    // timestamp so it does not get reclaimed out from under an active user.
    await db.prepare("UPDATE devices SET last_seen = ?, label = ? WHERE order_id = ? AND device = ?")
      .bind(nowIso(), String(body.label || "").slice(0, 64), license.order, device).run();
  } else {
    if (license.seats > 0) {
      const row = await db.prepare("SELECT COUNT(*) AS n FROM devices WHERE order_id = ?")
        .bind(license.order).first();
      if ((row?.n ?? 0) >= license.seats) {
        await logAttempt(db, license.order, device, "activate", "seats_exhausted");
        return json({
          error: `This licence covers ${license.seats} computers and is already on ${row.n}. `
               + `Release one to free a seat.`,
          seats: license.seats,
          devices: await deviceList(db, license.order),
        }, 409);
      }
    }
    await db.prepare(
      "INSERT INTO devices (order_id, device, label, tier, seats, first_seen, last_seen) "
      + "VALUES (?, ?, ?, ?, ?, ?, ?)"
    ).bind(
      license.order, device, String(body.label || "").slice(0, 64),
      license.tier, license.seats, nowIso(), nowIso()
    ).run();
  }

  await logAttempt(db, license.order, device, "activate", "ok");
  return json({
    receipt: signReceipt(env.LICENSE_PRIVATE_KEY_PEM, {
      order: license.order, device, tier: license.tier, seats: license.seats,
    }),
    tier: license.tier,
    seats: license.seats,
    used: (await deviceList(db, license.order)).length,
  });
}

export async function handleDevices(request, env, json) {
  const body = await request.json().catch(() => ({}));
  const auth = await authorise(env, body);
  if (auth.error) return json({ error: auth.error }, auth.status);

  const db = env.LICENSES;
  await reclaimStale(db, auth.license.order);
  return json({
    tier: auth.license.tier,
    seats: auth.license.seats,
    devices: await deviceList(db, auth.license.order),
  });
}

export async function handleDeactivate(request, env, json) {
  const body = await request.json().catch(() => ({}));
  const target = String(body.target || "");
  const auth = await authorise(env, body, [target]);
  if (auth.error) return json({ error: auth.error }, auth.status);

  const db = env.LICENSES;
  await db.prepare("DELETE FROM devices WHERE order_id = ? AND device = ?")
    .bind(auth.license.order, target).run();
  await logAttempt(db, auth.license.order, auth.device, "deactivate", `released:${target}`);

  return json({ released: target, devices: await deviceList(db, auth.license.order) });
}

/** Kill a key: refunds, and freebies that turned out to be a mistake. */
export async function revokeOrder(db, order, reason) {
  await db.prepare(
    "INSERT INTO revocations (order_id, reason, revoked_at) VALUES (?, ?, ?) "
    + "ON CONFLICT(order_id) DO UPDATE SET reason = excluded.reason, revoked_at = excluded.revoked_at"
  ).bind(order, reason || "withdrawn", nowIso()).run();
  // Free the seats too, so a re-sold or replacement key starts clean.
  await db.prepare("DELETE FROM devices WHERE order_id = ?").bind(order).run();
}

export { TIER_SEATS, RECEIPT_DAYS, RECLAIM_DAYS };
