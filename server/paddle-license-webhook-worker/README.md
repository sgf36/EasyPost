# Paddle → license webhook (Cloudflare Worker)

Always-warm, free-at-this-volume Worker that turns a completed Paddle purchase
into an emailed license key. On `transaction.completed` for the license price,
it verifies the signed webhook, mints an Ed25519-signed key (the format
`app/core/license.py` verifies offline), and emails it via Resend.

Ed25519 signing and HMAC verification use `node:crypto` (hence the
`nodejs_compat` compatibility flag in `wrangler.toml`). That API is fully
supported in Workers, takes the PEM key directly, and is the same code path we
verify locally under Node — avoiding the Workers runtime's historical
WebCrypto Ed25519 algorithm-naming differences. Stateless; all secrets are
Worker bindings.

> This is the recommended deployment. A container/FastAPI equivalent lives in
> `../paddle-license-webhook/` if you ever want to self-host instead.

## Deploy

```bash
cd server/paddle-license-webhook-worker
npm install
npx wrangler login            # opens Cloudflare auth in your browser

# non-secret config: edit wrangler.toml [vars]
#   PADDLE_PRICE_ID     = pri_01ky2hekjfm1c9nspf5pnqv0jv
#   LICENSE_FROM_EMAIL  = licenses@yourdomain.com

# secrets (you'll be prompted to paste each value):
npx wrangler secret put LICENSE_PRIVATE_KEY_PEM    # the Ed25519 private key PEM
npx wrangler secret put PADDLE_WEBHOOK_SECRET       # from the Paddle notification destination
npx wrangler secret put PADDLE_API_KEY              # Paddle API key
npx wrangler secret put RESEND_API_KEY              # Resend API key
npx wrangler secret put CONTACT_SHARED_SECRET       # shared with the site's contact.php

npx wrangler deploy
```

Deploy prints your URL, e.g. `https://easypost-license-webhook.<you>.workers.dev`.
Health check: `GET /health`. Webhook endpoint: `POST /paddle/webhook`.

## Contact-form relay: `POST /contact`

The product site's contact form posts here rather than calling Resend itself,
so the Resend API key never sits on shared hosting. `site/contact.php` does
the spam filtering, then relays the cleaned fields with an
`X-EPD-Contact-Secret` header; this Worker checks that secret (timing-safe)
and sends via Resend to `CONTACT_TO_EMAIL`.

The shared secret is deliberately low-value by design: the worst anyone can
do with it is send Spencer an email at his own address. A leaked Resend key,
by contrast, would let them send mail *as* the domain — which is exactly why
it lives here and not on the web host. Rotating the shared secret is two
commands: write a new value to `~/.epd-contact-secret` on the host, and
`wrangler secret put CONTACT_SHARED_SECRET`.

`contact.php` falls back to PHP `mail()` if this route is unreachable or
Resend rejects the send. A message landing in Junk is a much better failure
than a message silently lost.

### Why Resend at all, for a form that already worked

`mail()` delivered fine, but Bluehost's shared outbound relays carry poor
reputation with Exchange Online: with SPF, DKIM **and** DMARC all passing,
Microsoft still scored the message `SCL:5` / `CAT:SPM` and filed it under
Junk. That is a reputation judgement, not an authentication failure, so no
DNS record fixes it. A sender with its own standing does.

## Wire up Paddle & Resend

1. **Resend:** create an account, verify your sending domain, create an API key
   (`RESEND_API_KEY`), and set `LICENSE_FROM_EMAIL` to an address on that domain.
2. **Paddle notification destination** (Developer tools → Notifications → New):
   - URL: `https://<your-worker-url>/paddle/webhook`
   - Event: **`transaction.completed`**
   - Save, then copy the destination's **signing secret** → `PADDLE_WEBHOOK_SECRET`.
3. **Paddle API key** (Developer tools → Authentication) → `PADDLE_API_KEY`.

## Test in sandbox first

Set `PADDLE_API_BASE = "https://sandbox-api.paddle.com"` in `[vars]`, point a
sandbox notification destination at the Worker, and run a sandbox checkout with
a test card. Confirm the buyer gets a key and it activates in the app. Then
switch the vars/secrets to live values and redeploy.

## Notes

- **Idempotent-ish:** the license `iat` is the event's `occurred_at`, so Paddle
  retries mint the identical key rather than a new one.
- **Manual fallback:** `tools/issue_license.py` still issues keys by hand.


## Live wiring (as configured 2026-07-22)

Paddle product `pro_01ky2h8cfe2ven8ypchnmfbena` — "Easy-Post Desktop License":

| Price id | Tier | Amount | Billing |
|---|---|---|---|
| `pri_01ky2hekjfm1c9nspf5pnqv0jv` | personal | $29 | one-time |
| `pri_01ky3fxcf519vwq9vw8cwy35k9` | business | $149 | yearly |
| `pri_01ky3fxdv0gcg0xnf04npzpb2k` | organisation | $349 | yearly |

Notification destination `ntfset_01ky3g1b29r9zvgz1vyw9n6wyh` posts to
`/paddle/webhook` for `transaction.completed`, `adjustment.created` and the
`subscription.*` lifecycle events.

**`transaction.refunded` is not a Paddle event.** Refunds arrive as
`adjustment.created`; subscribing to the former makes the whole destination
fail validation.

The Paddle API key (`Licence webhook (Worker, read-only)`) is scoped to
**`customer.read` + `transaction.read` only** — the sole runtime call is
`GET /customers/{id}` to find the buyer's email. It cannot create or change
anything, so a leak of the Worker's secrets cannot be used to alter prices,
issue refunds or touch the catalogue.

It expires **18 July 2027**. Paddle caps key lifetime at roughly a year (a
2036 expiry was rejected outright), so this needs rotating before then or
licence emails stop being sent.

The products and prices above were created with a temporary broad key, which
was revoked immediately afterwards.

**The permission checkboxes in Paddle's key dialog only respond to real mouse
clicks** — setting them programmatically appears to succeed and silently does
not register, producing a "Select at least one" error on save.
