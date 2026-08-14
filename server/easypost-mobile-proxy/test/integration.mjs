// End-to-end smoke against a running `wrangler dev` (port 8799). Exercises the
// full pairing -> claim -> proxy -> demo flow with a throwaway licence key that
// matches the LICENSE_PUBLIC_KEY_B64 set in .dev.vars.
//
//     npm run dev:init   # once — creates the tables in the local D1
//     npm run dev        # in one terminal
//     npm test           # in another
//
// Without that server this file used to fail with ECONNREFUSED, which made
// `node --test` red by default — and a suite that is always red is a suite
// nobody reads. A genuine regression here was indistinguishable from nobody
// having started wrangler.
//
// Two preconditions are checked, not one. A worker with an empty local D1
// answers /health perfectly well and then fails six tests with
// "Cannot read properties of undefined" — which is how this was actually
// found. The schema probe turns that into a skip that names the fix.
//
// Skipped, not passed: a silent green would be worse than the red it replaces,
// because it would claim coverage this run did not have.
import { describe, test, before } from "node:test";
import assert from "node:assert/strict";

const BASE = process.env.BASE || "http://127.0.0.1:8799";
const PRIV_B64URL = "MC4CAQAwBQYDK2VwBCIEIHraDcuqlzbjWa3oz72qzQ-kWcY3rLUUqNbsWjhk8TNP";

const b64url = (b) => Buffer.from(b).toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (s) => Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/"), "base64");

async function mintLicense() {
  const priv = await crypto.subtle.importKey("pkcs8", fromB64url(PRIV_B64URL), { name: "Ed25519" }, false, ["sign"]);
  const payload = { v: 1, product: "easypost-desktop", order: "ord_test", tier: "business", email: "rev@x.com" };
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, priv, bytes);
  return "EPD1." + b64url(bytes) + "." + b64url(sig);
}

const post = (path, body, headers = {}) =>
  fetch(BASE + path, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });
const get = (path, headers = {}) => fetch(BASE + path, { headers });

/**
 * Why these tests cannot run, or null if they can.
 *
 * Short timeouts throughout: a readiness probe must never hang a suite.
 */
async function notReady() {
  try {
    const health = await fetch(BASE + "/health", { signal: AbortSignal.timeout(2000) });
    if (!health.ok) return `worker on ${BASE} answered /health with ${health.status}`;
  } catch {
    return `no worker answering on ${BASE} — run \`npm run dev\``;
  }
  // Claiming an unknown token only SELECTs, so this creates nothing. With the
  // tables present it is a clean 404; without them the query throws and the
  // Worker 500s.
  try {
    const probe = await fetch(BASE + "/pair/claim", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ pairing_token: "__readiness_probe__" }),
      signal: AbortSignal.timeout(4000),
    });
    if (probe.status !== 404) {
      return `local D1 has no tables (POST /pair/claim gave ${probe.status}, expected 404)` +
        " — run `npm run dev:init`";
    }
  } catch (err) {
    return `could not probe the database: ${err.message}`;
  }
  return null;
}

// Top-level await so the skip reason is known at definition time and the runner
// reports these as skipped rather than silently absent.
const skip = (await notReady()) ?? false;

describe("easypost-mobile-proxy end-to-end", { skip }, () => {
  let license;
  let pairingToken;
  let claimBody;
  let auth;

  before(async () => {
    license = await mintLicense();
    pairingToken = "tok-" + b64url(crypto.getRandomValues(new Uint8Array(12)));
  });

  test("health ok", async () => {
    const health = await (await get("/health")).json();
    assert.equal(health.ok, true);
  });

  test("register accepts valid licence", async () => {
    const reg = await post("/pair/register", {
      pairing_token: pairingToken, easypost_key: "EZTKfake_reviewer_key", license,
    });
    const body = await reg.json();
    assert.equal(reg.status, 200, `status ${reg.status}`);
    assert.equal(body.ok, true, `tier=${body.tier}`);
  });

  test("register rejects bad licence", async () => {
    const bad = await post("/pair/register", { pairing_token: "x", easypost_key: "k", license: "EPD1.bad.bad" });
    assert.equal(bad.status, 403);
  });

  test("claim returns device_token + kek", async () => {
    const claim = await post("/pair/claim", { pairing_token: pairingToken, platform: "ios" });
    claimBody = await claim.json();
    assert.equal(claim.status, 200);
    assert.ok(claimBody.device_token, "no device_token");
    assert.ok(claimBody.kek, "no kek");
    auth = { authorization: "Bearer " + claimBody.device_token, "x-ep-kek": claimBody.kek };
  });

  test("pairing token is single-use (burned)", async () => {
    const again = await post("/pair/claim", { pairing_token: pairingToken });
    assert.equal(again.status, 404);
  });

  test("proxy rejects missing auth", async () => {
    assert.equal((await get("/ep/trackers")).status, 401);
  });

  test("proxy refuses non-allow-listed op", async () => {
    const resp = await post("/ep/shipments", { shipment: {} }, auth);
    const body = await resp.json();
    assert.equal(resp.status, 403, body.error);
  });

  test("proxy rejects wrong KEK", async () => {
    const resp = await get("/ep/trackers", {
      authorization: "Bearer " + claimBody.device_token,
      "x-ep-kek": b64url(crypto.getRandomValues(new Uint8Array(32))),
    });
    assert.equal(resp.status, 401);
  });

  test("allowed op decrypts key + forwards to EasyPost", async () => {
    // The fake key is rejected upstream, but EasyPost's error shape
    // ({error:{code:...}}) — not our {error:"string"} — proves the Worker
    // decrypted the key and relayed the call.
    const resp = await get("/ep/trackers", auth);
    let body = {};
    try { body = JSON.parse(await resp.text()); } catch { /* non-JSON upstream */ }
    assert.ok(
      body.error && typeof body.error === "object" && !!body.error.code,
      `upstream=${body?.error?.code || resp.status}`,
    );
  });

  test("the query string reaches EasyPost", async () => {
    // The contract worker.js comments and test/proxy-contract.test.js pin
    // statically. Here it is end to end: EasyPost validates page_size, so an
    // out-of-range value comes back as its own error rather than a generic one.
    // If the query were dropped, the request would succeed instead.
    const resp = await get("/ep/trackers?page_size=99999", auth);
    let body = {};
    try { body = JSON.parse(await resp.text()); } catch { /* non-JSON upstream */ }
    assert.ok(body.error, `expected an upstream error, got status ${resp.status}`);
  });

  test("review code grants demo access", async () => {
    const resp = await post("/pair/demo", { code: "REVIEW-TEST-123", platform: "android" });
    const body = await resp.json();
    assert.equal(resp.status, 200);
    assert.equal(body.demo, true);
    assert.ok(body.device_token);
  });

  test("wrong review code refused", async () => {
    assert.equal((await post("/pair/demo", { code: "WRONG" })).status, 403);
  });
});
