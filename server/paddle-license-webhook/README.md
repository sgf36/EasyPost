# Paddle → Easy-Post Desktop license webhook

A tiny service that turns a completed Paddle purchase into an emailed license
key. On `transaction.completed` for the license price, it mints an Ed25519-signed
key (the format `app/core/license.py` verifies offline) and emails it to the
buyer via Resend.

```
Buyer completes Paddle checkout
        │  transaction.completed  (signed webhook)
        ▼
  this service  ──verifies signature──►  mints signed license  ──►  emails buyer
```

No database, no dashboard — stateless. Secrets live only in environment
variables; nothing sensitive is in the repo.

## 1. Deploy (any container host)

It's a standard FastAPI app with a `Dockerfile`, so it runs on Render,
Railway, Fly.io, Google Cloud Run, etc. Example (Render): "New Web Service" →
point at this folder → it builds the Dockerfile → set the env vars below.

Locally:
```bash
pip install -r requirements.txt
uvicorn app:app --port 8080
```
Health check: `GET /health` → `{"ok": true}`. Webhook endpoint: `POST /paddle/webhook`.

## 2. Environment variables

See `.env.example`. In short:

| Var | What |
|-----|------|
| `LICENSE_PRIVATE_KEY_PEM` | Ed25519 private signing key (PEM). Public half is embedded in the app. |
| `PADDLE_WEBHOOK_SECRET` | Signing secret of the Paddle notification destination. |
| `PADDLE_API_KEY` | Paddle API key, used to fetch the buyer's email. |
| `PADDLE_PRICE_ID` | Only mint for this price (`pri_01ky2hekjfm1c9nspf5pnqv0jv`). |
| `RESEND_API_KEY` / `LICENSE_FROM_EMAIL` | Send the license email (Resend). |

## 3. Resend (email)

1. Create a Resend account, add and verify your sending domain.
2. Create an API key → `RESEND_API_KEY`.
3. Set `LICENSE_FROM_EMAIL` to an address on the verified domain.

## 4. Paddle notification destination (the webhook)

In Paddle: **Developer tools → Notifications → New destination**.
- URL: `https://<your-deploy-host>/paddle/webhook`
- Events: **`transaction.completed`** (that's all this service needs).
- Save, then copy the destination's **signing secret** into `PADDLE_WEBHOOK_SECRET`.

Also create a Paddle **API key** (Developer tools → Authentication) → `PADDLE_API_KEY`.

## 5. Test before going live

Use Paddle **Sandbox**: set `PADDLE_API_BASE=https://sandbox-api.paddle.com`,
point a sandbox notification destination at your deployed URL, and run a
sandbox checkout with a test card. Confirm the buyer receives a key and that it
activates in the app. Then repeat with live values.

## Notes

- **Idempotent-ish:** the license `iat` is derived from the event's
  `occurred_at`, so Paddle retries mint the identical key rather than a new one.
- **Manual fallback:** `tools/issue_license.py` still issues keys by hand for
  any order, independent of this service.
