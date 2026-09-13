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
- Therefore: a stolen copy of `devices` is undecryptable ciphertext; a stolen
  phone holds a KEK that only works through this scope-limited proxy (read-only
  apart from cancelling a pickup, key never returned) and only until it is
  revoked. There is **no server-held master secret** to leak.
- Exception, bounded: a `pending_pairs` row holds the KEK beside the ciphertext
  until the phone claims it, so for that window it *is* a usable key to anyone
  reading the database. Unclaimed rows are deleted once `PAIR_TTL_SECONDS`
  (10 minutes) has passed — by the next `/pair/register`, or by the cron sweep
  every 15 minutes — so none outlives its QR by more than 25 minutes.
- `owner_hash` is a domain-separated SHA-256 of the EasyPost key. It lets the
  desktop revoke its phones by presenting the key; it cannot be reversed, since
  an EasyPost key is far too random to guess.
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
| `POST /pair/revoke` | phone | header `Authorization: Bearer <device_token>` → `{ ok, revoked: 1 }`; that token 401s from then on and its ciphertext is blanked. Repeatable. Call on unpair, before wiping local credentials |
| `POST /pair/revoke-all` | desktop | `{ easypost_key, license? }` → `{ ok, revoked: n }`; revokes every phone paired to that key and deletes its waiting QR. With a valid licence, also revokes phones paired before `owner_hash` existed, matched by licence order. 400 without a key, 403 for an invalid licence |
| `ANY /ep/*` | phone | scope-enforced proxy to EasyPost; headers `Authorization: Bearer <device_token>` + `X-EP-KEK: <kek>` |

Allow-list (everything else → 403) is exactly what the mobile app calls:
`GET /trackers`, `/shipments`, `/insurances`, `/pickups`, `/claims` (collections,
paged by query string) and `POST /pickups/{id}/cancel`. **Nothing that spends
money is reachable** — not labels, rates, insurance, pickups or claims — because
a copied device credential can call any allowed route with curl, whatever the
app's interface offers.

## Develop / test

```bash
npm install
npm test                 # unit + Worker tests (in-memory SQLite, Node 22.13+)
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
# Upgrading a database created before revocation instead: run this once, and
# BEFORE the deploy — the new Worker writes columns the old tables lack.
#   wrangler d1 execute easypost-mobile --remote --file migrations/0001_revocation_and_expiry.sql
wrangler deploy
# Reviewer access (optional but needed for store approval):
wrangler secret put REVIEW_CODE
wrangler secret put DEMO_EASYPOST_TEST_KEY
```

No secrets are needed for the core pairing + proxy flow — the encryption key
lives on the phone, not here.
