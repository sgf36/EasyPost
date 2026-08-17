// easypost-mobile-proxy — the backend for Easy-Post Mobile Companion.
//
// Design (see MOBILE-COMPANION-BUILD-BRIEF.md): zero-standing-custody proxy.
// The desktop registers its production EasyPost key; the Worker encrypts it
// under a fresh random KEK and stores ONLY the ciphertext. The KEK is handed to
// the phone at claim time and then deleted server-side. Every proxied request
// carries the KEK, which the Worker uses to decrypt in-memory for that one call.
// A leaked database is therefore undecryptable ciphertext, and the phone holds a
// KEK — not the raw key — that only works through this scope-limited proxy.

import {
  randomToken,
  encryptWithNewKek,
  decryptWithKek,
  verifyLicense,
} from "./crypto.js";

function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function now() {
  return Math.floor(Date.now() / 1000);
}

// EasyPost operations the phone is allowed to invoke. Anything not listed —
// notably shipment creation, rate buying and label purchase — is refused, so a
// compromised phone can never buy a label or read the raw key.
const ALLOW = [
  { method: "GET", re: /^\/trackers(\/[^/]+)?$/ },
  { method: "GET", re: /^\/shipments(\/[^/]+)?$/ },
  { method: "GET", re: /^\/insurances(\/[^/]+)?$/ },
  { method: "POST", re: /^\/insurances$/ }, // buy insurance — confirm-gated in-app
  { method: "GET", re: /^\/pickups(\/[^/]+)?$/ },
  { method: "POST", re: /^\/pickups$/ }, // schedule — confirm-gated in-app
  { method: "POST", re: /^\/pickups\/[^/]+\/(cancel|buy)$/ },
  { method: "GET", re: /^\/claims(\/[^/]+)?$/ },
  { method: "POST", re: /^\/claims$/ }, // file a claim
];

function isAllowed(method, epPath) {
  return ALLOW.some((a) => a.method === method && a.re.test(epPath));
}

// ---- pairing -------------------------------------------------------------

// Desktop → proxy. Registers a one-time pairing token bound to the (encrypted)
// production key, gated on a valid licence. Only ciphertext is persisted; the
// KEK is stashed transiently until the phone claims it.
async function handleRegister(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }
  const { pairing_token, easypost_key, license } = body || {};
  if (!pairing_token || !easypost_key || !license) {
    return json({ error: "missing_fields" }, 400);
  }

  const lic = await verifyLicense(license, env);
  if (!lic) return json({ error: "invalid_license" }, 403);

  const { kek, ciphertext, iv } = await encryptWithNewKek(String(easypost_key));

  await env.PAIRING.prepare(
    `INSERT OR REPLACE INTO pending_pairs
       (pairing_token, ciphertext, iv, kek, license_order, license_tier, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(pairing_token, ciphertext, iv, kek, lic.order, lic.tier, now())
    .run();

  return json({ ok: true, tier: lic.tier });
}

// Phone → proxy. Presents the pairing token, receives a long-lived device token
// and the KEK, after which the server's copy of the KEK is destroyed.
async function handleClaim(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }
  const { pairing_token, platform } = body || {};
  if (!pairing_token) return json({ error: "missing_fields" }, 400);

  // Scannable reviewer pairing.
  //
  // App Review asked for "a demo QR code ... to fully assess the app features"
  // (guideline 2.1(a), submission 74049fb7). A real pairing QR cannot be handed
  // over: each token is single-use, expires after PAIR_TTL_SECONDS, and is bound
  // to a paying customer's own production EasyPost key.
  //
  // So a QR carrying "demo:<REVIEW_CODE>" is routed to exactly the same demo
  // path as /pair/demo — the TEST-mode demo key, tier "demo". It reuses the
  // review code as its only credential, so there is no new secret to manage, no
  // pending_pairs row, and no new trust boundary: anyone who can scan the image
  // could equally have typed the code, which is already printed in the App Store
  // Connect reviewer notes.
  //
  // Deliberately NOT single-use and NOT time-limited: a reviewer may scan it
  // more than once, or on more than one device, and a code that works once and
  // then silently fails is worse than none at all.
  const DEMO_PREFIX = "demo:";
  if (String(pairing_token).startsWith(DEMO_PREFIX)) {
    if (!env.REVIEW_CODE || !env.DEMO_EASYPOST_TEST_KEY) {
      return json({ error: "demo_disabled" }, 403);
    }
    const supplied = String(pairing_token).slice(DEMO_PREFIX.length);
    if (!safeEqual(supplied, env.REVIEW_CODE)) {
      return json({ error: "invalid_review_code" }, 403);
    }
    const demo = await encryptWithNewKek(String(env.DEMO_EASYPOST_TEST_KEY));
    const demoToken = randomToken(32);
    const demoPlat = platform === "ios" || platform === "android" ? platform : null;
    await env.PAIRING.prepare(
      `INSERT INTO devices
         (device_token, ciphertext, iv, license_order, license_tier, platform, created_at, last_seen, revoked)
       VALUES (?, ?, ?, 'REVIEW', 'demo', ?, ?, ?, 0)`,
    )
      .bind(demoToken, demo.ciphertext, demo.iv, demoPlat, now(), now())
      .run();
    return json({ device_token: demoToken, kek: demo.kek, tier: "demo", demo: true });
  }

  const row = await env.PAIRING.prepare(
    `SELECT ciphertext, iv, kek, license_order, license_tier, created_at
       FROM pending_pairs WHERE pairing_token = ?`,
  )
    .bind(pairing_token)
    .first();

  if (!row) return json({ error: "unknown_or_used_token" }, 404);

  const ttl = parseInt(env.PAIR_TTL_SECONDS || "600", 10);
  if (now() - row.created_at > ttl) {
    await env.PAIRING.prepare(`DELETE FROM pending_pairs WHERE pairing_token = ?`)
      .bind(pairing_token)
      .run();
    return json({ error: "token_expired" }, 410);
  }

  const deviceToken = randomToken(32);
  const plat = platform === "ios" || platform === "android" ? platform : null;

  // Move the ciphertext into the persistent device row (WITHOUT the KEK), then
  // burn the pending pair — this is the moment custody of the KEK leaves us.
  await env.PAIRING.batch([
    env.PAIRING.prepare(
      `INSERT INTO devices
         (device_token, ciphertext, iv, license_order, license_tier, platform, created_at, last_seen, revoked)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)`,
    ).bind(deviceToken, row.ciphertext, row.iv, row.license_order, row.license_tier, plat, now(), now()),
    env.PAIRING.prepare(`DELETE FROM pending_pairs WHERE pairing_token = ?`).bind(pairing_token),
  ]);

  return json({ device_token: deviceToken, kek: row.kek, tier: row.license_tier });
}

// Reviewer / demo access. App-store reviewers cannot complete QR pairing (they
// have no licensed desktop), so they enter a review code instead. It pairs the
// app against a demo EasyPost TEST-mode key held as a Worker secret — a fully
// working app on fixture data, no real money, no user's key involved. Disabled
// (403) unless both REVIEW_CODE and DEMO_EASYPOST_TEST_KEY secrets are set.
async function handleDemo(request, env) {
  if (!env.REVIEW_CODE || !env.DEMO_EASYPOST_TEST_KEY) {
    return json({ error: "demo_disabled" }, 403);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "bad_json" }, 400);
  }
  const { code, platform } = body || {};
  // Constant-time-ish compare to avoid leaking the code via timing.
  if (!code || !safeEqual(String(code), env.REVIEW_CODE)) {
    return json({ error: "invalid_review_code" }, 403);
  }

  const { kek, ciphertext, iv } = await encryptWithNewKek(String(env.DEMO_EASYPOST_TEST_KEY));
  const deviceToken = randomToken(32);
  const plat = platform === "ios" || platform === "android" ? platform : null;
  await env.PAIRING.prepare(
    `INSERT INTO devices
       (device_token, ciphertext, iv, license_order, license_tier, platform, created_at, last_seen, revoked)
     VALUES (?, ?, ?, 'REVIEW', 'demo', ?, ?, ?, 0)`,
  )
    .bind(deviceToken, ciphertext, iv, plat, now(), now())
    .run();

  return json({ device_token: deviceToken, kek, tier: "demo", demo: true });
}

function safeEqual(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ---- proxy ---------------------------------------------------------------

async function handleProxy(request, env, url) {
  const auth = request.headers.get("authorization") || "";
  const deviceToken = auth.startsWith("Bearer ") ? auth.slice(7) : null;
  const kek = request.headers.get("x-ep-kek");
  if (!deviceToken || !kek) return json({ error: "unauthenticated" }, 401);

  const dev = await env.PAIRING.prepare(
    `SELECT ciphertext, iv, revoked FROM devices WHERE device_token = ?`,
  )
    .bind(deviceToken)
    .first();
  if (!dev || dev.revoked) return json({ error: "unauthenticated" }, 401);

  const epPath = url.pathname.slice("/ep".length) || "/";
  if (!isAllowed(request.method, epPath)) {
    return json({ error: "operation_not_permitted" }, 403);
  }

  let easypostKey;
  try {
    easypostKey = await decryptWithKek(kek, dev.ciphertext, dev.iv);
  } catch {
    return json({ error: "bad_kek" }, 401);
  }

  // `url.search` must reach EasyPost unmodified. The mobile app's list screens
  // page with `page_size` and `before_id`; before they were forwarded, one
  // unparameterised GET returned only the first page — in the review account
  // that meant 25 of 54 trackers, every one of them `delivered`, so the app
  // looked incapable of showing any other status.
  //
  // This is a cross-repo contract. The code is here; the dependency on it is in
  // `sgf36/Easy-Post-Mobile-Companion`, so nothing in this repository fails if
  // it breaks. Strip the query, filter it, or start allow-listing on the full
  // URL instead of the path, and the mobile lists silently go short. No error,
  // no failing test here, just less data.
  //
  // `isAllowed` checks method and path only, deliberately — see its call above.
  const upstream = `${env.EASYPOST_API_BASE}${epPath}${url.search}`;
  const headers = {
    authorization: "Basic " + btoa(easypostKey + ":"),
  };
  const init = { method: request.method, headers };
  if (request.method === "POST") {
    init.body = await request.text();
    headers["content-type"] = "application/json";
  }

  const resp = await fetch(upstream, init);
  easypostKey = null; // drop plaintext promptly

  env.PAIRING.prepare(`UPDATE devices SET last_seen = ? WHERE device_token = ?`)
    .bind(now(), deviceToken)
    .run()
    .catch(() => {});

  const text = await resp.text();
  return new Response(text, {
    status: resp.status,
    headers: { "content-type": resp.headers.get("content-type") || "application/json" },
  });
}

// ---- router --------------------------------------------------------------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "easypost-mobile-proxy" });
    }
    if (request.method === "POST" && url.pathname === "/pair/register") {
      return handleRegister(request, env);
    }
    if (request.method === "POST" && url.pathname === "/pair/claim") {
      return handleClaim(request, env);
    }
    if (request.method === "POST" && url.pathname === "/pair/demo") {
      return handleDemo(request, env);
    }
    if (url.pathname === "/ep" || url.pathname.startsWith("/ep/")) {
      return handleProxy(request, env, url);
    }
    return json({ error: "not_found" }, 404);
  },
};
