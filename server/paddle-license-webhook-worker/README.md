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

npx wrangler deploy
```

Deploy prints your URL, e.g. `https://easypost-license-webhook.<you>.workers.dev`.
Health check: `GET /health`. Webhook endpoint: `POST /paddle/webhook`.

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
