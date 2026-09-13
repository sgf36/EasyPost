// Revocation, subscription-ordering and seat rules of the licence Worker.
//
// Each test drives the real `fetch` handler end to end: a signed Paddle webhook
// in, then a signed /activate call to observe the effect, against an in-memory
// SQLite stand-in for D1. Nothing here reaches Paddle, Resend or Cloudflare:
// the signing key and webhook secret are generated per run, and outbound fetch
// is replaced before anything is imported that could call it.
//
// Every "must not revoke" assertion sits beside a positive control from the
// same method in the same test, because an activation that silently failed for
// some other reason would otherwise read as a pass.
//
// Run: npm test   (or: node --test test/)
import { test } from "node:test";
import assert from "node:assert/strict";
import { createHmac, generateKeyPairSync, randomBytes } from "node:crypto";

import { makeD1 } from "./d1shim.mjs";
import worker, { mintLicense } from "../src/worker.js";

const { privateKey } = generateKeyPairSync("ed25519");
const PEM = privateKey.export({ type: "pkcs8", format: "pem" });
const SECRET = `test_${randomBytes(24).toString("hex")}`;
const PRODUCT = "easypost-desktop";

const sentEmails = [];
globalThis.fetch = async (url, init) => {
  const u = String(url);
  if (u.includes("/customers/")) {
    return new Response(JSON.stringify({ data: { email: "buyer@example.invalid" } }), { status: 200 });
  }
  if (u.startsWith("https://api.resend.com/")) {
    sentEmails.push(JSON.parse(init.body));
    return new Response("{}", { status: 200 });
  }
  throw new Error(`unexpected outbound fetch in test: ${u}`);
};

function makeEnv() {
  return {
    LICENSES: makeD1(),
    LICENSE_PRIVATE_KEY_PEM: PEM,
    PADDLE_WEBHOOK_SECRET: SECRET,
    PADDLE_API_KEY: "test-paddle-key",
    RESEND_API_KEY: "test-resend-key",
    LICENSE_FROM_EMAIL: "licences@example.invalid",
    PRICE_TIERS: JSON.stringify({
      pri_test_personal: "personal",
      pri_test_business: "business",
      pri_test_org: "organisation",
    }),
  };
}

async function webhook(env, event, secret = SECRET) {
  const raw = JSON.stringify(event);
  const ts = Math.floor(Date.now() / 1000);
  const h1 = createHmac("sha256", secret).update(`${ts}:${raw}`).digest("hex");
  const res = await worker.fetch(new Request("https://worker.test/paddle/webhook", {
    method: "POST",
    body: raw,
    headers: { "Paddle-Signature": `ts=${ts};h1=${h1}` },
  }), env);
  return { status: res.status, body: await res.json().catch(() => null) };
}

function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

async function activate(env, key, order, device) {
  const ts = nowIso();
  const proof = createHmac("sha256", key).update([order, device, ts].join("|")).digest("hex");
  const res = await worker.fetch(new Request("https://worker.test/activate", {
    method: "POST",
    body: JSON.stringify({ license: key, device, ts, proof }),
  }), env);
  return { status: res.status, body: await res.json() };
}

const dev = (n) => n.toString(16).padStart(32, "0");

function perpetualKey(txn) {
  return mintLicense(PEM, PRODUCT, "buyer@example.invalid", txn, "2026-09-01T00:00:00Z", "personal");
}

function adjustment(eventType, fields) {
  return {
    event_type: eventType,
    occurred_at: new Date().toISOString(),
    data: {
      id: "adj_test",
      transaction_id: "txn_test",
      subscription_id: null,
      type: "full",
      items: [],
      ...fields,
    },
  };
}

// --- Defect 1: only money actually returned revokes --------------------------

test("an approved full refund revokes (positive control for the cases below)", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);

  const hook = await webhook(env, adjustment("adjustment.created", { action: "refund", status: "approved" }));
  assert.equal(hook.status, 200);
  assert.equal(hook.body.status, "revoked");

  const after = await activate(env, key, "txn_test", dev(1));
  assert.equal(after.status, 403);
  assert.match(after.body.error, /\(refunded\)/);
});

for (const [label, fields] of [
  ["a refund still pending approval", { action: "refund", status: "pending_approval" }],
  ["an approved partial refund", { action: "refund", status: "approved", type: "partial", items: [{ type: "partial", amount: "100" }] }],
  ["an approved credit", { action: "credit", status: "approved" }],
  ["a chargeback warning", { action: "chargeback_warning", status: "approved" }],
  ["a chargeback reversal", { action: "chargeback_reverse", status: "approved" }],
]) {
  test(`${label} does not revoke`, async () => {
    const env = makeEnv();
    const key = perpetualKey("txn_test");
    assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);

    const hook = await webhook(env, adjustment("adjustment.created", fields));
    assert.equal(hook.status, 200);
    assert.notEqual(hook.body.status, "revoked");
    assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);

    // Positive control in the same database: the same key is revocable.
    await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "approved" }));
    assert.equal((await activate(env, key, "txn_test", dev(1))).status, 403);
  });
}

test("a pending refund revokes only when adjustment.updated approves it", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  await webhook(env, adjustment("adjustment.created", { action: "refund", status: "pending_approval" }));
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);

  const hook = await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "approved" }));
  assert.equal(hook.body.status, "revoked");
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 403);
});

test("a refund whose every item is full counts as a full refund", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  await webhook(env, adjustment("adjustment.updated", {
    action: "refund", status: "approved", type: undefined, items: [{ type: "full" }],
  }));
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 403);
});

test("a rejected refund lifts a revocation written by the old any-adjustment rule", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  // What the pre-fix Worker left behind for a refund request: reason "created".
  env.LICENSES.raw.prepare("INSERT INTO revocations VALUES ('txn_test', 'created', '2026-09-01T00:00:00Z')").run();
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 403);

  const hook = await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "rejected" }));
  assert.equal(hook.status, 200);
  assert.equal(hook.body.restored, 1);
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);
});

test("a rejected refund never lifts a real refund or a hand-written revocation", async () => {
  const env = makeEnv();
  const refunded = perpetualKey("txn_test");
  const withdrawn = perpetualKey("txn_comp");
  await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "approved" }));
  env.LICENSES.raw.prepare("INSERT INTO revocations VALUES ('txn_comp', 'withdrawn', '2026-09-01T00:00:00Z')").run();

  await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "rejected" }));
  await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "rejected", transaction_id: "txn_comp" }));
  assert.equal((await activate(env, refunded, "txn_test", dev(1))).status, 403);
  assert.equal((await activate(env, withdrawn, "txn_comp", dev(1))).status, 403);
});

test("a chargeback revokes and a won dispute restores", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  await webhook(env, adjustment("adjustment.created", { action: "chargeback", status: "approved", type: "partial" }));
  const revoked = await activate(env, key, "txn_test", dev(1));
  assert.equal(revoked.status, 403);
  assert.match(revoked.body.error, /\(chargeback\)/);

  await webhook(env, adjustment("adjustment.created", { id: "adj_rev", action: "chargeback_reverse", status: "approved" }));
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);
});

test("the original chargeback moving to reversed also restores", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  await webhook(env, adjustment("adjustment.created", { action: "chargeback", status: "approved" }));
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 403);
  await webhook(env, adjustment("adjustment.updated", { action: "chargeback", status: "reversed" }));
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);
});

test("a chargeback reversal cannot resurrect a key that was also refunded", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "approved" }));
  await webhook(env, adjustment("adjustment.created", { id: "adj_cb", action: "chargeback", status: "approved" }));
  await webhook(env, adjustment("adjustment.created", { id: "adj_rev", action: "chargeback_reverse", status: "approved" }));
  const after = await activate(env, key, "txn_test", dev(1));
  assert.equal(after.status, 403);
  assert.match(after.body.error, /\(refunded\)/);
});

// --- Defect 2: a subscription refund reaches the subscription's key ----------

test("refunding an annual subscription revokes the key minted for it", async () => {
  const env = makeEnv();
  const purchase = await webhook(env, {
    event_type: "transaction.completed",
    occurred_at: "2026-09-01T00:00:00Z",
    data: {
      id: "txn_test_annual", subscription_id: "sub_test_annual", origin: "web",
      billed_at: "2026-09-01T00:00:00Z", customer_id: "ctm_test",
      items: [{ price: { id: "pri_test_business" } }],
    },
  });
  assert.equal(purchase.body.status, "license_issued");
  const key = sentEmails.at(-1).text.match(/EPD1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)[0];
  const order = JSON.parse(Buffer.from(key.split(".")[1], "base64url")).order;
  assert.equal(order, "sub_test_annual");

  const future = new Date(Date.now() + 300 * 86400000).toISOString();
  await webhook(env, {
    event_type: "subscription.created",
    occurred_at: new Date().toISOString(),
    data: { id: "sub_test_annual", status: "active", next_billed_at: future, items: [{ price: { id: "pri_test_business" } }] },
  });
  assert.equal((await activate(env, key, order, dev(1))).status, 200);

  await webhook(env, adjustment("adjustment.updated", {
    action: "refund", status: "approved",
    transaction_id: "txn_test_annual", subscription_id: "sub_test_annual",
  }));
  const after = await activate(env, key, order, dev(1));
  assert.equal(after.status, 403);
  assert.match(after.body.error, /\(refunded\)/);
});

// --- Defect 3: an older subscription event cannot overwrite a newer one ------

function subEvent(type, occurredAt, fields) {
  return {
    event_type: type,
    occurred_at: occurredAt,
    data: { id: "sub_test_order", items: [{ price: { id: "pri_test_business" } }], ...fields },
  };
}

function annualKey(sub, tier = "business") {
  return mintLicense(PEM, PRODUCT, "buyer@example.invalid", sub, "subscription", tier);
}

test("a subscription.updated from before a cancellation, delivered late, is ignored", async () => {
  const env = makeEnv();
  const key = annualKey("sub_test_order");
  const future = new Date(Date.now() + 300 * 86400000).toISOString();

  await webhook(env, subEvent("subscription.created", "2026-09-01T10:00:00.000001Z", { status: "active", next_billed_at: future }));
  assert.equal((await activate(env, key, "sub_test_order", dev(1))).status, 200);

  await webhook(env, subEvent("subscription.canceled", "2026-09-03T10:00:00.000001Z", { status: "canceled" }));
  assert.equal((await activate(env, key, "sub_test_order", dev(1))).status, 402);

  const late = await webhook(env, subEvent("subscription.updated", "2026-09-02T10:00:00.000001Z", { status: "active", next_billed_at: future }));
  assert.equal(late.status, 200);
  assert.equal((await activate(env, key, "sub_test_order", dev(1))).status, 402);
  assert.equal(late.body.status, "subscription_stale_ignored");
});

test("a newer subscription event still applies, including over a pre-fix row", async () => {
  const env = makeEnv();
  const key = annualKey("sub_test_order");
  const future = new Date(Date.now() + 300 * 86400000).toISOString();
  // A row as the old code wrote it: whole-second processing time. The event
  // below is in the same second but later, with Paddle's microseconds, which
  // plain text comparison would wrongly call older.
  env.LICENSES.raw.prepare(
    "INSERT INTO subscriptions VALUES ('sub_test_order', 'past_due', '2026-01-01T00:00:00Z', 'business', '2026-09-02T10:00:00Z')"
  ).run();
  assert.equal((await activate(env, key, "sub_test_order", dev(1))).status, 402);

  const hook = await webhook(env, subEvent("subscription.updated", "2026-09-02T10:00:00.500000Z", { status: "active", next_billed_at: future }));
  assert.equal(hook.body.status, "subscription_recorded");
  assert.equal((await activate(env, key, "sub_test_order", dev(1))).status, 200);
});

// --- Defect 4: a downgrade takes effect on seats -----------------------------

test("downgrading Organisation to Business caps new activations at Business seats", async () => {
  const env = makeEnv();
  const key = annualKey("sub_test_order", "organisation");
  const future = new Date(Date.now() + 300 * 86400000).toISOString();

  await webhook(env, subEvent("subscription.created", "2026-09-01T10:00:00Z", {
    status: "active", next_billed_at: future, items: [{ price: { id: "pri_test_org" } }],
  }));
  // Positive control: while Organisation is paid for, an 11th computer is fine.
  for (let i = 1; i <= 11; i++) {
    assert.equal((await activate(env, key, "sub_test_order", dev(i))).status, 200, `org seat ${i}`);
  }

  await webhook(env, subEvent("subscription.updated", "2026-09-05T10:00:00Z", {
    status: "active", next_billed_at: future, items: [{ price: { id: "pri_test_business" } }],
  }));
  const twelfth = await activate(env, key, "sub_test_order", dev(12));
  assert.equal(twelfth.status, 409);
  assert.equal(twelfth.body.seats, 10);

  // Computers already seated are not thrown off mid-period.
  const existing = await activate(env, key, "sub_test_order", dev(1));
  assert.equal(existing.status, 200);
  assert.equal(existing.body.seats, 10);
});

// --- Unchanged: signature verification ---------------------------------------

test("a webhook signed with the wrong secret is still refused", async () => {
  const env = makeEnv();
  const key = perpetualKey("txn_test");
  const forged = await webhook(env, adjustment("adjustment.updated", { action: "refund", status: "approved" }), "wrong-secret");
  assert.equal(forged.status, 401);
  assert.equal((await activate(env, key, "txn_test", dev(1))).status, 200);
});
