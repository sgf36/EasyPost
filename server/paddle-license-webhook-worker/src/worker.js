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

const SIGNATURE_TOLERANCE_SECONDS = 300;

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
export function mintLicense(pem, product, email, order, iat) {
  const payload = Buffer.from(JSON.stringify({ v: 1, product, email, order, iat }), "utf8");
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

async function sendLicenseEmail(apiKey, from, to, licenseKey) {
  const text =
    "Thank you for buying Easy-Post Desktop.\n\n" +
    "Your license key:\n\n" +
    `${licenseKey}\n\n` +
    "To activate: open Easy-Post Desktop, paste this key on the activation " +
    "screen, and click Activate. Keep this email for your records.\n\n" +
    "Questions? https://github.com/sgf36/EasyPost/issues\n";
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
    if (request.method !== "POST" || url.pathname !== "/paddle/webhook") {
      return new Response("Not found", { status: 404 });
    }

    const raw = await request.text();
    const sig = request.headers.get("Paddle-Signature") || "";
    if (!verifyPaddleSignature(raw, sig, env.PADDLE_WEBHOOK_SECRET)) {
      return new Response("invalid signature", { status: 401 });
    }

    const event = JSON.parse(raw);
    if (event.event_type !== "transaction.completed") return json({ ignored: event.event_type });

    const data = event.data || {};
    const priceIds = (data.items || []).map((i) => i.price && i.price.id);
    if (!priceIds.includes(env.PADDLE_PRICE_ID)) return json({ ignored: "other-price" });

    const base = env.PADDLE_API_BASE || "https://api.paddle.com";
    const product = env.LICENSE_PRODUCT_ID || "easypost-desktop";
    const txn = data.id || "";
    // Deterministic iat from the event, so Paddle retries mint the identical key.
    const iat = event.occurred_at || "1970-01-01T00:00:00Z";

    const email = await getCustomerEmail(base, env.PADDLE_API_KEY, data.customer_id);
    const licenseKey = mintLicense(env.LICENSE_PRIVATE_KEY_PEM, product, email, txn, iat);
    await sendLicenseEmail(env.RESEND_API_KEY, env.LICENSE_FROM_EMAIL, email, licenseKey);

    return json({ status: "license_issued", transaction: txn });
  },
};
