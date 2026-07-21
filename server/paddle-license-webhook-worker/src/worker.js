/**
 * Paddle -> Easy-Post Desktop license webhook (Cloudflare Worker).
 *
 * On a completed Paddle transaction for the license price, verifies the signed
 * webhook, mints an Ed25519-signed offline license key (the format
 * app/core/license.py verifies), and emails it to the buyer via Resend.
 *
 * Always-warm, no cold starts. All secrets come from Worker bindings; nothing
 * sensitive is in the repo.
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

const enc = new TextEncoder();
const SIGNATURE_TOLERANCE_SECONDS = 300;

function pemToDer(pem) {
  const b64 = pem
    .replace(/-----BEGIN [^-]+-----/, "")
    .replace(/-----END [^-]+-----/, "")
    .replace(/\s+/g, "");
  const bin = atob(b64);
  const der = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) der[i] = bin.charCodeAt(i);
  return der;
}

function b64url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function toHex(buf) {
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function timingSafeEqual(a, b) {
  if (a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

async function verifyPaddleSignature(rawBody, sigHeader, secret) {
  const parts = Object.fromEntries(
    sigHeader.split(";").map((kv) => kv.split("=").map((s) => s.trim()))
  );
  const { ts, h1 } = parts;
  if (!ts || !h1) return false;
  if (Math.abs(Date.now() / 1000 - Number(ts)) > SIGNATURE_TOLERANCE_SECONDS) return false;
  const key = await crypto.subtle.importKey(
    "raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]
  );
  const mac = await crypto.subtle.sign("HMAC", key, enc.encode(`${ts}:${rawBody}`));
  return timingSafeEqual(toHex(mac), h1);
}

async function mintLicense(pem, product, email, order, iat) {
  const payloadBytes = enc.encode(JSON.stringify({ v: 1, product, email, order, iat }));
  const key = await crypto.subtle.importKey("pkcs8", pemToDer(pem), { name: "Ed25519" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign({ name: "Ed25519" }, key, payloadBytes));
  return `EPD1.${b64url(payloadBytes)}.${b64url(sig)}`;
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
    if (!(await verifyPaddleSignature(raw, sig, env.PADDLE_WEBHOOK_SECRET))) {
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
    const licenseKey = await mintLicense(env.LICENSE_PRIVATE_KEY_PEM, product, email, txn, iat);
    await sendLicenseEmail(env.RESEND_API_KEY, env.LICENSE_FROM_EMAIL, email, licenseKey);

    return json({ status: "license_issued", transaction: txn });
  },
};
