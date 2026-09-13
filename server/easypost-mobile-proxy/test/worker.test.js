/**
 * Behavioural tests for the Worker: the allow-list, revocation and expiry.
 *
 * These run the real `worker.js` against the real `schema.sql`, in an
 * in-memory SQLite database behind a minimal D1-shaped adapter, with EasyPost
 * replaced by a stub `fetch`. No wrangler, no network, no Cloudflare account.
 *
 * Behavioural rather than reading the source (as proxy-contract.test.js has to
 * for the query string) because each thing guarded here failed silently: the
 * allow-list still let a phone buy insurance long after the app stopped
 * offering it, and no route to revoke a phone existed at all while the store
 * listing said unpairing revoked it. A test that reads the text would have
 * passed both.
 *
 * Needs Node 22.13+ for `node:sqlite`.
 */
import { test, describe, beforeEach, afterEach, mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { DatabaseSync } from "node:sqlite";

import worker from "../src/worker.js";
import { b64urlEncode } from "../src/crypto.js";

const SCHEMA = readFileSync(fileURLToPath(new URL("../schema.sql", import.meta.url)), "utf8");
const BASE = "https://proxy.test";
const EASYPOST = "https://easypost.test/v2";

// Invented placeholders shaped like EasyPost keys. Never real credentials.
const KEY_A = "EZAKfake" + "a".repeat(32);
const KEY_B = "EZAKfake" + "b".repeat(32);

// ---- a D1-shaped adapter over node:sqlite ---------------------------------

// Only what worker.js calls: prepare/bind/first/run and batch. Results carry
// `meta.changes`, as D1's do, because revoke-all reports a count from it.
function d1(db) {
  const bound = (sql, args) => ({
    sql,
    args,
    bind: (...next) => bound(sql, next),
    async first() {
      return db.prepare(sql).get(...args) ?? null;
    },
    async run() {
      const r = db.prepare(sql).run(...args);
      return { success: true, meta: { changes: Number(r.changes) } };
    },
    runSync() {
      const r = db.prepare(sql).run(...args);
      return { success: true, meta: { changes: Number(r.changes) } };
    },
  });
  return {
    prepare: (sql) => bound(sql, []),
    // D1 runs a batch as one transaction; so does this.
    async batch(statements) {
      db.exec("BEGIN");
      try {
        const out = statements.map((s) => s.runSync());
        db.exec("COMMIT");
        return out;
      } catch (err) {
        db.exec("ROLLBACK");
        throw err;
      }
    },
  };
}

// ---- fixtures -------------------------------------------------------------

let db;
let env;
let upstream; // every request the Worker forwarded to "EasyPost"
let clock; // unix seconds the Worker sees

async function makeLicenceEnv() {
  const kp = await crypto.subtle.generateKey({ name: "Ed25519" }, true, ["sign", "verify"]);
  const rawPub = await crypto.subtle.exportKey("raw", kp.publicKey);
  return { kp, pub: b64urlEncode(rawPub) };
}
const licenceKeys = await makeLicenceEnv();

async function mintLicence(order = "ord_1") {
  const payload = { v: 1, product: "easypost-desktop", order, tier: "business", email: "x@example.invalid" };
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, licenceKeys.kp.privateKey, bytes);
  return "EPD1." + b64urlEncode(bytes) + "." + b64urlEncode(sig);
}

beforeEach(() => {
  db = new DatabaseSync(":memory:");
  db.exec(SCHEMA);
  env = {
    PAIRING: d1(db),
    EASYPOST_API_BASE: EASYPOST,
    LICENSE_PUBLIC_KEY_B64: licenceKeys.pub,
    LICENSE_PRODUCT_ID: "easypost-desktop",
    LICENSE_FORMAT_TAG: "EPD1",
    PAIR_TTL_SECONDS: "600",
  };
  upstream = [];
  mock.method(globalThis, "fetch", async (url, init = {}) => {
    upstream.push({ url: String(url), method: init.method || "GET", auth: init.headers?.authorization });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  clock = 1_800_000_000;
  mock.method(Date, "now", () => clock * 1000);
});

afterEach(() => {
  mock.restoreAll();
  db.close();
});

function call(method, path, { body, headers = {} } = {}) {
  const init = { method, headers: { ...headers } };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers["content-type"] = "application/json";
  }
  return worker.fetch(new Request(BASE + path, init), env);
}

async function register(key = KEY_A, token = "tok-" + crypto.randomUUID(), order = "ord_1") {
  const res = await call("POST", "/pair/register", {
    body: { pairing_token: token, easypost_key: key, license: await mintLicence(order) },
  });
  return { res, token };
}

/** Register and claim: a paired phone's headers. */
async function pairPhone(key = KEY_A, order = "ord_1") {
  const { res, token } = await register(key, undefined, order);
  assert.equal(res.status, 200);
  const claim = await call("POST", "/pair/claim", { body: { pairing_token: token, platform: "ios" } });
  assert.equal(claim.status, 200);
  const c = await claim.json();
  return { authorization: "Bearer " + c.device_token, "x-ep-kek": c.kek, token: c.device_token };
}

const phoneHeaders = (p) => ({ authorization: p.authorization, "x-ep-kek": p.kek ?? p["x-ep-kek"] });

// ---- allow-list -----------------------------------------------------------

describe("allow-list", () => {
  // Every call Easy-Post Mobile Companion 1.3.0 makes through the proxy, from
  // lib/services/proxy_client.dart in sgf36/Easy-Post-Mobile-Companion:
  // five paged collection GETs and cancelling a pickup. If the app gains a
  // call, it has to be added here and to ALLOW in the same change.
  const APP_CALLS = [
    ["GET", "/trackers?page_size=100"],
    ["GET", "/shipments?page_size=100&before_id=shp_1"],
    ["GET", "/insurances?page_size=100"],
    ["GET", "/claims?page_size=100"],
    ["GET", "/pickups?page_size=100"],
    ["POST", "/pickups/pickup_123/cancel"],
  ];

  // What a copied credential could previously do to the customer's account,
  // plus the purchases that were always refused, as controls.
  const REFUSED = [
    ["POST", "/insurances"], // buy insurance
    ["POST", "/pickups"], // schedule a pickup
    ["POST", "/pickups/pickup_123/buy"], // buy a pickup
    ["POST", "/claims"], // file a claim
    ["POST", "/shipments"], // control: always refused
    ["POST", "/shipments/shp_1/buy"], // control: always refused
    ["POST", "/shipments/shp_1/refund"], // the app never requests refunds
    ["POST", "/refunds"],
    ["GET", "/trackers/trk_1"], // the app never fetches a single object
    ["GET", "/api_keys"],
    ["DELETE", "/pickups/pickup_123"],
  ];

  for (const [method, path] of APP_CALLS) {
    test(`forwards the app's ${method} ${path}`, async () => {
      const phone = await pairPhone();
      const res = await call(method, "/ep" + path, {
        headers: phoneHeaders(phone),
        body: method === "POST" ? {} : undefined,
      });
      assert.equal(res.status, 200);
      assert.equal(upstream.length, 1);
      assert.equal(upstream[0].url, EASYPOST + path, "path and query must reach EasyPost unchanged");
      assert.equal(upstream[0].auth, "Basic " + btoa(KEY_A + ":"));
    });
  }

  for (const [method, path] of REFUSED) {
    test(`refuses ${method} ${path} without calling EasyPost`, async () => {
      const phone = await pairPhone();
      const res = await call(method, "/ep" + path, {
        headers: phoneHeaders(phone),
        body: method === "POST" ? {} : undefined,
      });
      assert.equal(res.status, 403);
      assert.deepEqual(await res.json(), { error: "operation_not_permitted" });
      assert.equal(upstream.length, 0, "a refused call must never reach EasyPost");
    });
  }
});

// ---- phone revocation -----------------------------------------------------

describe("POST /pair/revoke (phone, on unpair)", () => {
  test("a revoked phone gets 401 on its next request, reads and cancels alike", async () => {
    const phone = await pairPhone();
    // Positive control from the same method in the same run: the credential
    // works before it is revoked.
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(phone) })).status, 200);

    const res = await call("POST", "/pair/revoke", { headers: { authorization: phone.authorization } });
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, revoked: 1 });

    upstream = [];
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(phone) })).status, 401);
    const cancel = await call("POST", "/ep/pickups/pickup_1/cancel", { headers: phoneHeaders(phone), body: {} });
    assert.equal(cancel.status, 401);
    assert.equal(upstream.length, 0);
  });

  test("revoking blanks the stored ciphertext, so the phone's KEK decrypts nothing", async () => {
    const phone = await pairPhone();
    await call("POST", "/pair/revoke", { headers: { authorization: phone.authorization } });
    const row = db.prepare("SELECT ciphertext, iv, revoked, revoked_at FROM devices WHERE device_token = ?").get(phone.token);
    assert.equal(row.revoked, 1);
    assert.equal(row.ciphertext, "");
    assert.equal(row.iv, "");
    assert.equal(row.revoked_at, clock);
  });

  test("repeating a revoke succeeds, so the phone can retry a lost response", async () => {
    const phone = await pairPhone();
    await call("POST", "/pair/revoke", { headers: { authorization: phone.authorization } });
    const again = await call("POST", "/pair/revoke", { headers: { authorization: phone.authorization } });
    assert.equal(again.status, 200);
  });

  test("an unknown or missing token is refused and revokes nothing", async () => {
    const phone = await pairPhone();
    assert.equal((await call("POST", "/pair/revoke")).status, 401);
    assert.equal(
      (await call("POST", "/pair/revoke", { headers: { authorization: "Bearer not-a-device" } })).status,
      401,
    );
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(phone) })).status, 200);
  });

  test("revoking one phone leaves the account's other phones working", async () => {
    const one = await pairPhone();
    const two = await pairPhone();
    await call("POST", "/pair/revoke", { headers: { authorization: one.authorization } });
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(one) })).status, 401);
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(two) })).status, 200);
  });
});

// ---- desktop revocation ---------------------------------------------------

describe("POST /pair/revoke-all (desktop)", () => {
  test("revokes every phone paired to that EasyPost key and no other", async () => {
    const a1 = await pairPhone(KEY_A);
    const a2 = await pairPhone(KEY_A);
    const b1 = await pairPhone(KEY_B);

    const res = await call("POST", "/pair/revoke-all", { body: { easypost_key: KEY_A } });
    assert.equal(res.status, 200);
    assert.deepEqual(await res.json(), { ok: true, revoked: 2 });

    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(a1) })).status, 401);
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(a2) })).status, 401);
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(b1) })).status, 200);
  });

  test("also cancels a QR still waiting to be scanned", async () => {
    const { token } = await register(KEY_A);
    await call("POST", "/pair/revoke-all", { body: { easypost_key: KEY_A } });
    const claim = await call("POST", "/pair/claim", { body: { pairing_token: token } });
    assert.equal(claim.status, 404);
  });

  test("a wrong key revokes nothing", async () => {
    const phone = await pairPhone(KEY_A);
    const res = await call("POST", "/pair/revoke-all", { body: { easypost_key: KEY_B } });
    assert.deepEqual(await res.json(), { ok: true, revoked: 0 });
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(phone) })).status, 200);
  });

  test("no key is refused; a forged licence is refused", async () => {
    assert.equal((await call("POST", "/pair/revoke-all", { body: {} })).status, 400);
    const forged = await call("POST", "/pair/revoke-all", {
      body: { easypost_key: KEY_A, license: "EPD1.bad.bad" },
    });
    assert.equal(forged.status, 403);
  });

  // Phones paired before this change have no owner_hash. Simulated by nulling
  // it, which is exactly the state migration 0001 leaves an existing row in.
  function makeLegacy(phone) {
    db.prepare("UPDATE devices SET owner_hash = NULL WHERE device_token = ?").run(phone.token);
  }

  test("a pre-existing phone is revoked through its licence order", async () => {
    const legacy = await pairPhone(KEY_A, "ord_legacy");
    makeLegacy(legacy);

    const keyOnly = await call("POST", "/pair/revoke-all", { body: { easypost_key: KEY_A } });
    assert.deepEqual(await keyOnly.json(), { ok: true, revoked: 0 }, "no hash, so the key alone cannot find it");

    const res = await call("POST", "/pair/revoke-all", {
      body: { easypost_key: KEY_A, license: await mintLicence("ord_legacy") },
    });
    assert.deepEqual(await res.json(), { ok: true, revoked: 1 });
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(legacy) })).status, 401);
  });

  test("a pre-existing phone binds to its account on its first request", async () => {
    const legacy = await pairPhone(KEY_A);
    makeLegacy(legacy);
    assert.equal((await call("GET", "/ep/trackers", { headers: phoneHeaders(legacy) })).status, 200);

    const res = await call("POST", "/pair/revoke-all", { body: { easypost_key: KEY_A } });
    assert.deepEqual(await res.json(), { ok: true, revoked: 1 });
  });

  test("never touches reviewer devices, even for a licence claiming order REVIEW", async () => {
    env.REVIEW_CODE = "REVIEW-TEST-CODE";
    env.DEMO_EASYPOST_TEST_KEY = "EZTKfake" + "c".repeat(32);
    const demo = await (await call("POST", "/pair/demo", { body: { code: env.REVIEW_CODE } })).json();
    db.prepare("UPDATE devices SET owner_hash = NULL WHERE device_token = ?").run(demo.device_token);

    await call("POST", "/pair/revoke-all", {
      body: { easypost_key: KEY_A, license: await mintLicence("REVIEW") },
    });
    const row = db.prepare("SELECT revoked FROM devices WHERE device_token = ?").get(demo.device_token);
    assert.equal(row.revoked, 0);
  });
});

// ---- expiry of unclaimed pairings -----------------------------------------

describe("unclaimed pairings expire", () => {
  const pendingCount = () => db.prepare("SELECT COUNT(*) AS n FROM pending_pairs").get().n;

  test("the scheduled sweep deletes a pairing past PAIR_TTL_SECONDS", async () => {
    await register();
    assert.equal(pendingCount(), 1);

    clock += 600;
    await worker.scheduled({}, env, { waitUntil() {} });
    assert.equal(pendingCount(), 1, "still claimable at exactly the TTL, so still kept");

    clock += 1;
    await worker.scheduled({}, env, { waitUntil() {} });
    assert.equal(pendingCount(), 0);
  });

  test("the next registration sweeps abandoned pairings too", async () => {
    const { token: old } = await register();
    clock += 601;
    await register();
    const left = db.prepare("SELECT pairing_token FROM pending_pairs").all().map((r) => r.pairing_token);
    assert.equal(left.length, 1);
    assert.ok(!left.includes(old));
  });

  test("a pairing within the window can still be claimed", async () => {
    const { token } = await register();
    clock += 599;
    await worker.scheduled({}, env, { waitUntil() {} });
    const claim = await call("POST", "/pair/claim", { body: { pairing_token: token } });
    assert.equal(claim.status, 200);
  });
});

// ---- register cannot overwrite --------------------------------------------

test("registering a token already waiting is refused, not overwritten", async () => {
  const { token } = await register(KEY_A, "tok-fixed");
  const again = await register(KEY_B, token);
  assert.equal(again.res.status, 409);

  const claim = await (await call("POST", "/pair/claim", { body: { pairing_token: token } })).json();
  const phone = { authorization: "Bearer " + claim.device_token, "x-ep-kek": claim.kek };
  await call("GET", "/ep/trackers", { headers: phone });
  assert.equal(upstream[0].auth, "Basic " + btoa(KEY_A + ":"), "the first desktop's key must survive");
});
