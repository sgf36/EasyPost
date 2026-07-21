/**
 * Paddle -> Easy-Post Desktop license webhook (Cloudflare Worker).
 *
 * On a completed Paddle transaction for the license price, verifies the signed
 * webhook, mints an Ed25519-signed offline license key (the format
 * app/core/license.py verifies), and emails it to the buyer via Resend.
 *
 * Crypto uses node:crypto (requires the `nodejs_compat` compatibility flag,
 * set in wrangler.toml). That API is fully supported in Workers, accepts the
 * PEM key directly, and is byte-for-byte the same code path we test locally
 * under Node — safer than relying on WebCrypto's Ed25519 algorithm naming,
 * which differed historically in the Workers runtime.
 *
 * Secrets (wrangler secret put ...):
 *   LICENSE_PRIVATE_KEY_PEM  Ed25519 private key, PKCS8 PEM (public half is embedded in the app)
 *   PADDLE_WEBHOOK_SECRET    signing secret of the Paddle notification destination
 *   PADDLE_API_KEY           Paddle API key (to look up the buyer email)
 *   RESEND_API_KEY           Resend API key (to send the email)
 * Vars (wrangler.toml [vars]):
 *   PADDLE_PRICE_ID          only mint for this price
 *   LICENSE_FROM_EMAIL       verified Resend "from" address
 *   PADDLE_API_BASE          optional, default https://api.paddle.com
 *   LICENSE_PRODUCT_ID       optional, default "easypost-desktop"
 */

import { createHmac, createPrivateKey, sign as nodeSign, timingSafeEqual } from "node:crypto";

import {
  handleActivate,
  handleDeactivate,
  handleDevices,
  recordSubscription,
  revokeOrder,
  TIER_PLANS,
  TIER_SEATS,
} from "./activation.js";

const SIGNATURE_TOLERANCE_SECONDS = 300;

/**
 * Which tier a Paddle price buys. Set PRICE_TIERS in wrangler.toml as JSON:
 *   { "pri_abc": "personal", "pri_def": "business", "pri_ghi": "organisation" }
 * An unrecognised price mints nothing, so a new product cannot accidentally
 * hand out licences before its tier has been decided.
 */
function tierForPrice(env, priceIds) {
  let table = {};
  try {
    table = JSON.parse(env.PRICE_TIERS || "{}");
  } catch {
    table = {};
  }
  // Legacy single-price config predates tiers and means the entry tier.
  if (env.PADDLE_PRICE_ID && !table[env.PADDLE_PRICE_ID]) {
    table[env.PADDLE_PRICE_ID] = "personal";
  }
  for (const id of priceIds) {
    if (id && table[id]) return table[id];
  }
  return null;
}

function b64url(buf) {
  return Buffer.from(buf).toString("base64url");
}

/** Verify Paddle's `Paddle-Signature: ts=<unix>;h1=<hex hmac of "ts:body">`. */
export function verifyPaddleSignature(rawBody, sigHeader, secret) {
  let parts;
  try {
    parts = Object.fromEntries(
      String(sigHeader).split(";").map((kv) => kv.split("=").map((s) => s.trim()))
    );
  } catch {
    return false;
  }
  const { ts, h1 } = parts;
  if (!ts || !h1) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > SIGNATURE_TOLERANCE_SECONDS) return false;
  const expected = createHmac("sha256", secret).update(`${ts}:${rawBody}`).digest("hex");
  const a = Buffer.from(expected, "utf8");
  const b = Buffer.from(h1, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Mint the offline license key the desktop app verifies. */
export function mintLicense(pem, product, email, order, iat, tier = "personal", seats = null) {
  // Tier, seats and plan are all signed: the app reads them from the key rather
  // than from a table it would have to keep in step with ours.
  //
  // Note what is NOT in here: an expiry. Annual plans expire, but baking that
  // into the key would mean reissuing and re-pasting one every year. The key is
  // permanent and names the subscription; the activation receipt carries the
  // date and renews itself quietly.
  const allowance = seats === null ? (TIER_SEATS[tier] ?? TIER_SEATS.personal) : seats;
  const plan = TIER_PLANS[tier] || "perpetual";
  const payload = Buffer.from(
    JSON.stringify({ v: 2, product, email, order, tier, seats: allowance, plan, iat }),
    "utf8"
  );
  // Ed25519 takes a null digest algorithm.
  const signature = nodeSign(null, payload, createPrivateKey(pem));
  return `EPD1.${b64url(payload)}.${b64url(signature)}`;
}

async function getCustomerEmail(base, apiKey, customerId) {
  const r = await fetch(`${base}/customers/${customerId}`, {
    headers: { Authorization: `Bearer ${apiKey}` },
  });
  if (!r.ok) throw new Error(`paddle customers ${r.status}`);
  return (await r.json()).data.email;
}

/**
 * Relay a contact-form submission from easy-post.spencerfields.com.
 *
 * The site PHP handler does the spam filtering and then posts here rather
 * than calling Resend itself, so the Resend key never sits on shared hosting.
 * The shared secret guarding this route is deliberately low-value: the worst
 * anyone can do with it is send Spencer email at his own address. A leaked
 * Resend key, by contrast, would let them send mail *as* the domain.
 */
async function handleContact(request, env) {
  const supplied = request.headers.get("X-EPD-Contact-Secret") || "";
  const expected = env.CONTACT_SHARED_SECRET || "";
  const a = Buffer.from(supplied, "utf8");
  const b = Buffer.from(expected, "utf8");
  if (!expected || a.length !== b.length || !timingSafeEqual(a, b)) {
    return new Response("forbidden", { status: 403 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad json" }, 400);
  }

  const name = String(body.name || "").slice(0, 100).replace(/[\r\n]/g, " ").trim();
  const email = String(body.email || "").slice(0, 150).replace(/[\r\n]/g, " ").trim();
  const topic = String(body.topic || "Something else").slice(0, 60).replace(/[\r\n]/g, " ").trim();
  const message = String(body.message || "").slice(0, 4000);
  if (!name || !email || message.length < 10) {
    return json({ error: "missing fields" }, 400);
  }

  const to = env.CONTACT_TO_EMAIL;
  const ip = String(body.ip || "unknown").slice(0, 45);
  const text =
    "A message was sent from the Easy-Post Desktop contact form.\n\n" +
    "Name:  " + name + "\nEmail: " + email + "\nTopic: " + topic + "\n" +
    "IP:    " + ip + "\n\n" +
    "-----------------------------------------\n\n" + message + "\n";

  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + env.RESEND_API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: "Easy-Post Desktop <" + env.LICENSE_FROM_EMAIL + ">",
      to: [to],
      reply_to: email,
      subject: "[Easy-Post Desktop] " + topic + " — " + name,
      text,
    }),
  });

  if (!r.ok) {
    // Surface the reason so the PHP side can log it and fall back to mail().
    const detail = await r.text().catch(() => "");
    return json({ error: "resend", status: r.status, detail: detail.slice(0, 300) }, 502);
  }
  return json({ status: "sent" });
}

async function sendLicenseEmail(apiKey, from, to, licenseKey, tier = "personal") {
  const seats = TIER_SEATS[tier] ?? TIER_SEATS.personal;
  const annual = TIER_PLANS[tier] === "annual";
  const allowance = seats === 0
    ? "This key has no computer limit."
    : `This key covers up to ${seats} computer${seats === 1 ? "" : "s"}.`;
  const billing = annual
    ? "This is an annual subscription. Keep this key — it stays the same every "
      + "year, and renewals are applied automatically. You will not be sent a new one."
    : "This is a one-time purchase. The key does not expire.";
  const text =
    "Thank you for buying Easy-Post Desktop.\n\n" +
    "Your license key:\n\n" +
    `${licenseKey}\n\n` +
    `${allowance}\n${billing}\n\n` +
    "To activate: open Easy-Post Desktop, paste this key on the activation " +
    "screen, and click Activate. Keep this email for your records.\n\n" +
    "Changing computers? Open Settings and release the old one first, or " +
    "release it from the new computer when prompted.\n\n" +
    "Questions? Apps@spencerfields.com\n";
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from, to: [to], subject: "Your Easy-Post Desktop license key", text }),
  });
  if (!r.ok) throw new Error(`resend ${r.status}`);
}

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") return json({ ok: true });
    if (request.method === "POST" && url.pathname === "/contact") {
      return handleContact(request, env);
    }
    // Seat activation. Each verifies the licence signature and a proof of key
    // possession itself, so none of them needs a shared secret with the app.
    if (request.method === "POST" && url.pathname === "/activate") {
      return handleActivate(request, env, json);
    }
    if (request.method === "POST" && url.pathname === "/devices") {
      return handleDevices(request, env, json);
    }
    if (request.method === "POST" && url.pathname === "/deactivate") {
      return handleDeactivate(request, env, json);
    }
    if (request.method !== "POST" || url.pathname !== "/paddle/webhook") {
      return new Response("Not found", { status: 404 });
    }

    const raw = await request.text();
    const sig = request.headers.get("Paddle-Signature") || "";
    if (!verifyPaddleSignature(raw, sig, env.PADDLE_WEBHOOK_SECRET)) {
      return new Response("invalid signature", { status: 401 });
    }

    const event = JSON.parse(raw);
    const data = event.data || {};

    // A refunded or charged-back purchase must stop working. Revoking also frees
    // the seats, so a replacement key issued later starts from a clean slate.
    //
    // Paddle signals this with adjustment.created, NOT transaction.refunded -
    // that event does not exist and the notification destination rejects it as
    // an invalid subscription. The second name is kept only as a harmless guard.
    if (event.event_type === "adjustment.created"
        || event.event_type === "transaction.refunded") {
      const txnId = data.transaction_id || data.id || "";
      if (txnId && env.LICENSES) {
        await revokeOrder(env.LICENSES, txnId, event.event_type.split(".")[1]);
        return json({ status: "revoked", transaction: txnId });
      }
      return json({ ignored: "no-transaction-id" });
    }

    // Subscription lifecycle. The licence key never changes; what changes is how
    // long an activation receipt is worth, so all these do is keep the record of
    // what has been paid for up to date.
    if (event.event_type.startsWith("subscription.")) {
      const subId = data.id || "";
      if (!subId || !env.LICENSES) return json({ ignored: "no-subscription-id" });

      const priceIds = (data.items || []).map((i) => i.price && i.price.id);
      const subTier = tierForPrice(env, priceIds) || "";
      const status = event.event_type === "subscription.canceled"
        ? "canceled"
        : (data.status || "active");
      // next_billed_at is what has actually been paid up to; current_billing_period
      // is the fallback when a subscription is cancelled and simply runs out.
      const periodEnd = data.next_billed_at
        || (data.current_billing_period && data.current_billing_period.ends_at)
        || "";

      await recordSubscription(env.LICENSES, subId, status, periodEnd, subTier);
      return json({ status: "subscription_recorded", subscription: subId, state: status });
    }

    if (event.event_type !== "transaction.completed") return json({ ignored: event.event_type });

    const priceIds = (data.items || []).map((i) => i.price && i.price.id);
    const tier = tierForPrice(env, priceIds);
    if (!tier) return json({ ignored: "other-price" });

    const base = env.PADDLE_API_BASE || "https://api.paddle.com";
    const product = env.LICENSE_PRODUCT_ID || "easypost-desktop";
    const txn = data.id || "";
    const subId = data.subscription_id || "";
    const annual = TIER_PLANS[tier] === "annual";

    // An annual key names the subscription, not the transaction, so it stays
    // valid across every renewal and activation can look up what is paid for.
    const orderRef = annual && subId ? subId : txn;

    // A renewal is a fresh transaction against a key the customer already has.
    // Minting is harmless (it is deterministic), but emailing again would be
    // noise, so only the first transaction sends anything.
    const isRenewal = data.origin === "subscription_recurring";

    // Deterministic iat, so Paddle retries mint the identical key. For a
    // subscription that means every renewal reproduces the original key rather
    // than a new one the customer would have to paste.
    const iat = annual && subId
      ? (data.billed_at || event.occurred_at || "1970-01-01T00:00:00Z")
      : (event.occurred_at || "1970-01-01T00:00:00Z");
    const stableIat = annual && subId ? "subscription" : iat;

    const email = await getCustomerEmail(base, env.PADDLE_API_KEY, data.customer_id);
    const licenseKey = mintLicense(
      env.LICENSE_PRIVATE_KEY_PEM, product, email, orderRef, stableIat, tier
    );

    if (isRenewal) {
      return json({ status: "renewal_noted", subscription: subId, tier });
    }
    await sendLicenseEmail(env.RESEND_API_KEY, env.LICENSE_FROM_EMAIL, email, licenseKey, tier);

    return json({ status: "license_issued", transaction: txn, order: orderRef, tier });
  },
};
