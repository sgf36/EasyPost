# easypost-mobile-proxy

Backend for **Easy-Post Mobile Companion** (iOS + Android). A Cloudflare Worker
that lets a paired phone read and manage EasyPost shipments **without the phone
ever holding the raw production key** and **without the operator being a standing
custodian of usable keys**.

See `../../MOBILE-COMPANION-BUILD-BRIEF.md` for the full design.

## Security model (zero standing custody)

- The desktop registers its production EasyPost key once. The Worker generates a
  random **KEK**, AES-GCM encrypts the key with it, and stores **only the
  ciphertext** in D1.
- The **KEK is handed to the phone** at claim time (stored in the phone's secure
  enclave) and then **deleted server-side**.
- Every proxied request carries the KEK; the Worker decrypts the key **in memory**
  for that one call and discards it.
- Therefore: a stolen D1 is undecryptable ciphertext; a stolen phone holds a KEK
  that only works through this scope-limited proxy (no label-buys, key never
  returned). There is **no server-held master secret** to leak.
- Residual ceiling: full compromise of the live Worker *code* could log KEKs from
  active requests — unavoidable in any proxy; mitigated by keeping this Worker
  minimal and OIDC-deployed.

## Endpoints

| Method + path | Who | Purpose |
|---|---|---|
| `GET /health` | any | liveness |
| `POST /pair/register` | desktop | `{ pairing_token, easypost_key, license }` → verifies licence, stores ciphertext, stashes KEK short-TTL |
| `POST /pair/claim` | phone | `{ pairing_token, platform }` → `{ device_token, kek }`; burns the pairing token + server KEK |
| `POST /pair/demo` | reviewer | `{ code }` → demo device on a TEST-mode key (needs `REVIEW_CODE` + `DEMO_EASYPOST_TEST_KEY` secrets) |
| `ANY /ep/*` | phone | scope-enforced proxy to EasyPost; headers `Authorization: Bearer <device_token>` + `X-EP-KEK: <kek>` |

Allow-list (everything else → 403): read trackers/shipments/insurances/pickups/
claims; create insurance, pickup (+cancel), claim. **Shipment create / rate-buy /
label purchase are refused** so a phone can never spend on labels.

## Develop / test

```bash
npm install
node --test              # crypto unit tests (Ed25519 + AES-GCM)
npm run dev              # wrangler dev --local  (needs a local D1, see below)
node test/integration.mjs  # end-to-end smoke against wrangler dev on :8799
```

Local dev needs a throwaway licence public key + demo secrets in `.dev.vars`
(gitignored) and the schema applied to the local D1:

```bash
wrangler d1 execute easypost-mobile --local --file schema.sql
```

## Deploy (owner)

```bash
wrangler d1 create easypost-mobile        # then paste the id into wrangler.toml
wrangler d1 execute easypost-mobile --remote --file schema.sql
wrangler deploy
# Reviewer access (optional but needed for store approval):
wrangler secret put REVIEW_CODE
wrangler secret put DEMO_EASYPOST_TEST_KEY
```

No secrets are needed for the core pairing + proxy flow — the encryption key
lives on the phone, not here.
