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
  handleStats,
  recordSubscription,
  revokeOrder,
  TIER_PLANS,
  TIER_SEATS,
} from "./activation.js";

import {
  contactCustomerEmail,
  contactOwnerEmail,
  licenseEmail,
  newCaseId,
} from "./emails.js";

const SIGNATURE_TOLERANCE_SECONDS = 300;

// The advertised launch offer. The website shows how many of the 26 remain and
// hides its banner once they are gone, by reading GET /promo below. The count
// is kept in D1 (promo_redemptions), incremented as discounted purchases
// complete, rather than queried from Paddle on every page load — so a busy
// pricing page costs nothing at Paddle and the Worker's API key stays scoped to
// the minimum. Paddle remains the real gate: it enforces usage_limit at
// checkout regardless of what this count says.
const PROMO = {
  id: "dsc_01kyb9g2cvkr6bsck13gngk3xf",
  code: "SUMMER26",
  limit: 26,
  expiresAt: "2026-09-30T23:59:59Z",
};

// Same allow-list origin for the browser-facing GET endpoints.
const SITE_ORIGIN = "https://easy-post.spencerfields.com";
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": SITE_ORIGIN,
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Max-Age": "86400",
};

// A discounted purchase, recorded once per transaction. INSERT OR IGNORE keeps
// a Paddle webhook retry from double-counting, since the transaction id is the
// primary key.
async function recordPromoRedemption(db, discountId, txnId) {
  if (!discountId || !txnId) return;
  try {
    await db.prepare(
      "INSERT OR IGNORE INTO promo_redemptions (transaction_id, discount_id, at) VALUES (?, ?, ?)"
    ).bind(txnId, discountId, new Date().toISOString().replace(/\.\d{3}Z$/, "Z")).run();
  } catch {
    // A missing table or a write hiccup must never fail the licence webhook —
    // the banner is cosmetic, the licence is not.
  }
}

async function promoStatus(db) {
  let used = 0;
  try {
    const row = await db.prepare(
      "SELECT COUNT(*) AS n FROM promo_redemptions WHERE discount_id = ?"
    ).bind(PROMO.id).first();
    used = row?.n ?? 0;
  } catch {
    used = 0;
  }
  const remaining = Math.max(0, PROMO.limit - used);
  const live = remaining > 0 && Date.now() < Date.parse(PROMO.expiresAt);
  return { code: PROMO.code, limit: PROMO.limit, used, remaining, active: live };
}

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
  // Trim the secret. `wrangler secret put` reads a line from a masked prompt,
  // where a stray trailing newline or CR is invisible and would make every
  // signature fail with a flat 401 — indistinguishable from the wrong secret.
  //
  // This was not the cause of the 2026-08-16 outage (that was the secret being
  // stored under the wrong name entirely; see WEBHOOK-RUNBOOK.md §0), and it is
  // kept purely as hardening: Paddle secrets never carry surrounding
  // whitespace, so trimming can only ever help. Diagnosing a whitespace variant
  // of this costs hours, because nothing in the 401 hints at it.
  const key = String(secret ?? "").trim();
  const expected = createHmac("sha256", key).update(`${ts}:${rawBody}`).digest("hex");
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

// --- Contact-form AI triage -------------------------------------------------
// The cheapest Anthropic model drafts a first reply to general enquiries. It is
// grounded in the facts below and nothing else, gated behind a confidence flag,
// and never lets an outage or a bad answer swallow a message: the owner is
// always forwarded the original regardless of what the AI does.
const AI_MODEL = "claude-haiku-4-5-20251001";
const AI_TRIAGE_DAILY_CAP = 50;
// Only these topics are auto-answered. Activation, refunds and bug reports need
// account-specific action or judgement, so they are acknowledged and routed to
// a human rather than adjudicated by a model.
const AI_AUTO_TOPICS = new Set(["Question before buying", "Something else"]);

const TRIAGE_FACTS = `- Easy-Post Desktop is an independent, open-source desktop app for Windows and macOS that drives the customer's OWN EasyPost account. It does not sell postage; labels are bought through the customer's EasyPost account and EasyPost bills them directly.
- An EasyPost account (free at easypost.com) and API key are required. A test-mode key lets them try everything with no real charges.
- Pricing: Personal is $29 one-time for up to 3 computers and never expires. Business is $149/year for up to 10 computers. Organisation is $349/year for up to 30. Both annual tiers are subscriptions, cancellable at any time. Enterprise (more than 30 computers) is by enquiry.
- Summer 2026 offer: 26% off Personal for the first 26 customers with code SUMMER26 at checkout, bringing it to $21.46.
- Every purchase has a 30-day money-back guarantee, no reason required. Refunds are handled by Paddle (the Merchant of Record) back to the original payment method, usually within 3 to 5 business days.
- The licence key is emailed immediately after purchase to the address used at checkout; if it is missing, check the spam folder. The Microsoft Store version needs no licence key.
- Each licence covers a set number of computers; the first run on a machine claims a place. A machine can be released from Settings, or from a new machine; a computer not seen for six months releases its place automatically.
- The Windows direct download shows a SmartScreen "Windows protected your PC" warning because it is not yet code-signed: click More info, then Run anyway. The Microsoft Store build is signed by Microsoft and shows no warning. The macOS build is signed and notarized by Apple.
- Supported systems: Windows 10 version 1809 or later (64-bit), or macOS 12 or later (Apple Silicon or Intel). The interface is available in fifty languages.
- Downloads and checksums: https://easy-post.spencerfields.com/download.html . Full FAQ: https://easy-post.spencerfields.com/faq.html . Source code: https://github.com/sgf36/EasyPost .
- The application stores data locally and has no analytics or telemetry; activation sends only a one-way fingerprint. Details: https://easy-post.spencerfields.com/privacy.html .
- Contact: Apps@spencerfields.com or +44 20 8132 5790.`;

async function resendSend(env, { from, to, replyTo, subject, text, html }) {
  const payload = { from, to: [to], reply_to: replyTo, subject, text };
  if (html) payload.html = html;
  return fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: "Bearer " + env.RESEND_API_KEY,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

// Best-effort record of a contact case so a support reference (EPD-YYMMDD-XXXX)
// can be traced back to the original enquiry later. Never blocks a reply: the
// table is created on demand and any failure is swallowed.
async function logContactCase(db, { caseId, name, email, topic, autoReplied }) {
  if (!db) return;
  try {
    await db
      .prepare(
        "CREATE TABLE IF NOT EXISTS contact_cases (case_id TEXT PRIMARY KEY, at TEXT, name TEXT, email TEXT, topic TEXT, auto_replied INTEGER)"
      )
      .run();
    await db
      .prepare(
        "INSERT OR IGNORE INTO contact_cases (case_id, at, name, email, topic, auto_replied) VALUES (?,?,?,?,?,?)"
      )
      .bind(caseId, new Date().toISOString(), name, email, topic, autoReplied ? 1 : 0)
      .run();
  } catch {}
}

async function underAiCap(db) {
  if (!db) return false;
  try {
    await db.prepare("CREATE TABLE IF NOT EXISTS ai_triage_log (at TEXT)").run();
    const since = new Date(Date.now() - 864e5).toISOString();
    const row = await db
      .prepare("SELECT COUNT(*) AS n FROM ai_triage_log WHERE at > ?")
      .bind(since)
      .first();
    return (row?.n ?? 0) < AI_TRIAGE_DAILY_CAP;
  } catch {
    return false;
  }
}

async function logAiUse(db) {
  try {
    await db.prepare("INSERT INTO ai_triage_log (at) VALUES (?)").bind(new Date().toISOString()).run();
  } catch {}
}

// Returns { confident: boolean, reply: string } or null if the call or parse
// fails. The model is told to answer only from TRIAGE_FACTS and to set
// confident=false whenever it cannot, which is the gate that keeps a guessed
// answer from ever reaching a customer.
async function aiAnswer(env, { topic, message }) {
  const system =
    "You are the automated first-response assistant for Easy-Post Desktop customer support. " +
    "Answer the customer's question using ONLY the facts below. Write in British English, warm and concise " +
    "(a short paragraph or two). Do not add a greeting with the customer's name, a signature, or any note that " +
    "the reply is automated — those are added around your text. If the question cannot be fully and confidently " +
    "answered from these facts, or needs account-specific action (looking up an order, issuing a refund, debugging " +
    "a crash), set confident to false and leave reply empty. Never invent prices, policies or dates. Never promise " +
    "a refund or make commitments on the business's behalf.\n\n" +
    "Also judge whether the message is a genuine enquiry from a prospective or existing customer, or unsolicited " +
    "business outreach — a marketing pitch, SEO or link-building offer, guest-post or backlink request, directory " +
    "or listing solicitation, agency or freelancer touting services, partnership or investment approach, or any " +
    "message whose real aim is to sell the reader something or get them to visit or sign up to the sender's own " +
    "site rather than to buy or use Easy-Post Desktop. If so, set spam to true; otherwise set spam to false. A " +
    "product-related question dressed up as small talk is NOT spam; be conservative and only flag clear outreach. " +
    "When spam is true, also set confident to false and leave reply empty.\n\nFACTS:\n" +
    TRIAGE_FACTS +
    '\n\nRespond with STRICT JSON only — no prose, no code fences: {"confident": true|false, "spam": true|false, "reply": "..."}';
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
      "content-type": "application/json",
    },
    body: JSON.stringify({
      model: AI_MODEL,
      max_tokens: 700,
      system,
      messages: [{ role: "user", content: `Topic: ${topic}\n\n${message}` }],
    }),
  });
  if (!r.ok) throw new Error("anthropic " + r.status);
  const data = await r.json();
  const raw = (data.content && data.content[0] && data.content[0].text) || "";
  let parsed;
  try {
    parsed = JSON.parse(raw.trim().replace(/^```json\s*/i, "").replace(/```$/, "").trim());
  } catch {
    return null;
  }
  if (typeof parsed.confident !== "boolean") return null;
  parsed.spam = parsed.spam === true;
  return parsed;
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

  // 1) Triage. Try an AI first-reply for general enquiries only, with a key
  //    present and under the daily cap. Anything unexpected here degrades to
  //    "no auto-reply" — it never blocks the forward to the owner below.
  let aiReply = null;
  let isSpam = false;
  let triageNote = "Not eligible for auto-reply; routed to you.";
  const eligible = AI_AUTO_TOPICS.has(topic) && env.ANTHROPIC_API_KEY;
  if (eligible && (await underAiCap(env.LICENSES))) {
    try {
      const res = await aiAnswer(env, { topic, message });
      await logAiUse(env.LICENSES);
      if (res && res.spam) {
        // Unsolicited business outreach (directory listing, SEO/link-building,
        // agency pitch, partnership approach). Flag it for the owner but send
        // NO acknowledgement to the sender: a reply only confirms a live,
        // monitored address and invites more of the same.
        isSpam = true;
        triageNote =
          "AI flagged as likely spam / unsolicited business outreach; no acknowledgement sent. Routed to you for review.";
      } else if (res && res.confident && res.reply && res.reply.trim().length > 20) {
        aiReply = res.reply.trim();
        triageNote = "AUTO-REPLIED to the customer:\n\n" + aiReply;
      } else {
        triageNote = "AI declined (low confidence); acknowledged and routed to you.";
      }
    } catch (e) {
      triageNote = "AI call failed (" + String(e).slice(0, 80) + "); acknowledged and routed to you.";
    }
  } else if (eligible) {
    triageNote = "AI daily cap reached; acknowledged and routed to you.";
  }

  // Assign a support reference and record it (best-effort). Generated before
  // sending so the same number appears in the customer reply, the owner forward,
  // and both subject lines.
  const caseId = newCaseId();
  const autoReplied = Boolean(aiReply);
  await logContactCase(env.LICENSES, { caseId, name, email, topic, autoReplied });

  // 2) Reply to the customer — the AI answer if we have one, otherwise a plain
  //    acknowledgement. Best-effort: a failure here must not lose the message.
  //    Skipped entirely for spam: acknowledging unsolicited outreach only
  //    confirms the address and invites more, and the owner still gets it below.
  if (!isSpam) {
    const customer = contactCustomerEmail({ name, topic, caseId, aiReply });
    try {
      await resendSend(env, {
        from: "Easy-Post Desktop Support <" + env.LICENSE_FROM_EMAIL + ">",
        to: email,
        replyTo: to,
        subject: customer.subject,
        text: customer.text,
        html: customer.html,
      });
    } catch {}
  }

  // 3) Forward the original to the owner. This is the safety net, so its success
  //    is what the endpoint reports — the customer reply above is a bonus.
  const owner = contactOwnerEmail({
    name, email, topic, ip, caseId, triageNote, message, autoReplied, spam: isSpam,
  });
  const r = await resendSend(env, {
    from: "Easy-Post Desktop <" + env.LICENSE_FROM_EMAIL + ">",
    to,
    replyTo: email,
    subject: owner.subject,
    text: owner.text,
    html: owner.html,
  });
  if (!r.ok) {
    // Surface the reason so the PHP side can log it and fall back to mail().
    const detail = await r.text().catch(() => "");
    return json({ error: "resend", status: r.status, detail: detail.slice(0, 300) }, 502);
  }
  return json({ status: "sent", auto_replied: autoReplied, case_id: caseId });
}

async function sendLicenseEmail(apiKey, from, to, licenseKey, tier = "personal") {
  const seats = TIER_SEATS[tier] ?? TIER_SEATS.personal;
  const annual = TIER_PLANS[tier] === "annual";
  const { subject, text, html } = licenseEmail({ licenseKey, seats, annual });
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from, to: [to], subject, text, html }),
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

    // Aggregate demand counts by platform/tier. Guarded by X-EPD-Stats-Secret;
    // no personal data, only counts of one-way device hashes. Read-only.
    if (request.method === "GET" && url.pathname === "/stats") {
      return handleStats(request, env, json);
    }

    // Public, read-only: how many of the launch discount remain. Cached briefly
    // at the edge so a burst of pricing-page views collapses to one D1 read.
    if (url.pathname === "/promo") {
      if (request.method === "OPTIONS") {
        return new Response(null, { status: 204, headers: CORS_HEADERS });
      }
      if (request.method === "GET") {
        const status = await promoStatus(env.LICENSES);
        return new Response(JSON.stringify(status), {
          headers: {
            "content-type": "application/json",
            "cache-control": "public, max-age=60",
            ...CORS_HEADERS,
          },
        });
      }
    }
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
    return handlePaddleWebhook(request, env);
  },
};

// Every throw in the webhook path used to surface at Paddle as a bare
// "500 error code: 1101" — Cloudflare's way of saying "the Worker raised
// something", with no file, no line and no stage. Diagnosing the first real
// paid order therefore meant reading the source and reasoning about which call
// could raise. It was none of the obvious candidates: the deployed bundle had
// been built by an older toolchain that routed node:crypto's `sign` through an
// unenv "not implemented" stub, so mintLicense threw on a Worker whose source
// was entirely correct.
//
// This wrapper does not stop that happening again. It makes it a one-minute
// diagnosis instead of an afternoon: the stage is named in the response Paddle
// records, and the exception is logged with its stack.
//
// It deliberately still returns 500. Paddle retries on 5xx, and a licence that
// failed to mint SHOULD be retried — swallowing the error into a 200 would lose
// the order silently, which is the one outcome worse than a slow diagnosis.
async function handlePaddleWebhook(request, env) {
  let stage = "read-body";
  try {
    return await processPaddleWebhook(request, env, (s) => { stage = s; });
  } catch (err) {
    // Logged, not just returned: the response body is capped and Paddle's log
    // is awkward to read, whereas `wrangler tail` shows this immediately.
    // Requires [observability] in wrangler.toml — see that file.
    console.error(`paddle webhook failed at stage=${stage}:`, err && err.stack ? err.stack : err);
    return new Response(
      JSON.stringify({
        error: "webhook_failed",
        stage,
        // Our own throw messages ("paddle customers 401"), never an env value.
        message: String((err && err.message) || err).slice(0, 300),
      }),
      { status: 500, headers: { "content-type": "application/json" } },
    );
  }
}

async function processPaddleWebhook(request, env, setStage) {
  setStage("read-body");
  const raw = await request.text();
  const sig = request.headers.get("Paddle-Signature") || "";

  setStage("verify-signature");
  if (!verifyPaddleSignature(raw, sig, env.PADDLE_WEBHOOK_SECRET)) {
    return new Response("invalid signature", { status: 401 });
  }

  setStage("parse-payload");
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
      setStage("revoke-order");
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

    setStage("record-subscription");
    await recordSubscription(env.LICENSES, subId, status, periodEnd, subTier);
    return json({ status: "subscription_recorded", subscription: subId, state: status });
  }

  if (event.event_type !== "transaction.completed") return json({ ignored: event.event_type });

  const priceIds = (data.items || []).map((i) => i.price && i.price.id);
  const tier = tierForPrice(env, priceIds);
  if (!tier) return json({ ignored: "other-price" });

  // Count the launch discount against its 26, so the website can show how many
  // are left. Only real first purchases carry it; a renewal never does.
  //
  // Note this runs BEFORE minting, so a redemption is recorded even on an
  // attempt that later throws. That is why /promo read "used: 1" while no key
  // existed during the 2026-08-14 incident.
  if (env.LICENSES && data.discount_id === PROMO.id && data.origin !== "subscription_recurring") {
    setStage("record-promo-redemption");
    await recordPromoRedemption(env.LICENSES, data.discount_id, data.id || "");
  }

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

  setStage("customer-lookup");
  const email = await getCustomerEmail(base, env.PADDLE_API_KEY, data.customer_id);

  // The stage that failed on 2026-08-14, and the reason this wrapper exists: a
  // stale deployed bundle routed node:crypto's sign through an unenv stub, so
  // this line threw on source that was entirely correct.
  setStage("mint-licence");
  const licenseKey = mintLicense(
    env.LICENSE_PRIVATE_KEY_PEM, product, email, orderRef, stableIat, tier
  );

  if (isRenewal) {
    return json({ status: "renewal_noted", subscription: subId, tier });
  }

  // Awaited before the success response, and sendLicenseEmail throws on any
  // non-2xx from Resend — so a license_issued body proves Resend accepted the
  // message. It does not prove the mail reached an inbox.
  setStage("send-email");
  await sendLicenseEmail(env.RESEND_API_KEY, env.LICENSE_FROM_EMAIL, email, licenseKey, tier);

  return json({ status: "license_issued", transaction: txn, order: orderRef, tier });
}
