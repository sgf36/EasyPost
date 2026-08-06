# Easy-Post Mobile Companion — build brief

_Actionable build plan. Supersedes the exploratory MOBILE-COMPANION-PLAN.md on
every point of conflict. Decisions locked 2026-08-06._

## Locked decisions

| Decision | Choice |
|---|---|
| Platforms | iOS + Android only (no Microsoft phone exists; WSA/Amazon Appstore retired) |
| Framework | Flutter (Dart), one codebase for both stores |
| Key architecture | **Model B, hardened — zero-standing-custody proxy.** The Worker stores only an AES-GCM *ciphertext* of the EasyPost key; the decryption key (KEK) lives on the phone. The server cannot decrypt any key at rest. |
| Pairing | Desktop shows a QR carrying a **one-time pairing token** (never the raw key). |
| Licence gate | Only a licensed desktop can mint a QR; proxy re-checks licence via the existing licence backend; phone re-checks periodically. |
| Scope safety | Proxy whitelists read/manage endpoints; **label purchase is refused server-side.** Insurance-buy and Pickup-schedule are allowed but require in-app confirmation (they cost money). |
| History/Reports | In scope, **last phase**, sourced from EasyPost Shipments list API via the proxy. |
| Distribution | iOS App Store (existing Apple Developer, $99/yr); Google Play ($25 one-time). Both apps free, gated by desktop licence. |
| Languages | **Same 50 locales as the desktop app** (parity). Flutter `intl`/ARB catalogs, translated via the same parallel-agent workflow; store listings localised to match where practical. |

## Why this design (zero-standing-custody) and its one ceiling

A plain proxy would make the operator a standing custodian of every user's
production key — the risk Spencer explicitly wants minimised. This design removes
that: the server holds only ciphertext it **cannot decrypt on its own**, because
the decryption key (KEK) lives on the phone.

Mandatory properties:

1. **Client-held KEK.** At pairing the Worker generates a random per-user KEK,
   AES-GCM encrypts the EasyPost key with it, stores **only the ciphertext** in
   D1, hands the KEK to the phone (stored in the secure enclave), then **deletes
   its own copy of the KEK.** Every proxied request carries the KEK; the Worker
   decrypts in memory for that one call and discards it. KEK never persisted,
   never logged.
2. **Scope whitelist.** The proxy exposes only an allow-list of EasyPost
   operations. Any label-buy / rate-buy path returns 403. The raw key is never
   returned to the phone.
3. **Spend confirmation.** Insurance purchase and Pickup scheduling *do* incur
   charges, so those are behind an explicit in-app confirm; there is no silent
   spend.
4. **Disclosure.** Privacy-policy + in-app disclaimer: the backend stores an
   encrypted copy of the production key that it cannot read without the paired
   phone; re-pair/revoke wipes it.

**Risk outcomes:**
- *D1 / database dump* → useless ciphertext (no KEK server-side).
- *Phone stolen* → holds a KEK, not the raw key; usable only through the
  scope-limited proxy, so no label-buys and no key exfiltration. Strictly safer
  than key-on-device.
- *One residual ceiling* → full compromise of the **live Worker code** could log
  KEKs from active requests. Unavoidable in any proxy; mitigated by keeping the
  Worker minimal, OIDC-deployed, and auditable. The "whole DB leaked" scenario
  is eliminated.

If this bar is ever judged insufficient, the fallback is key-on-device (phone
holds the raw key, server holds nothing) — a smaller code change from here.

## Architecture

```
 Desktop (PySide6, licensed, production mode)
   │  shows QR = { pairing_token (one-time, short TTL), proxy_url }
   │  and POSTs { pairing_token, easypost_key, licence } to the proxy
   ▼
 Cloudflare Worker  "easypost-mobile-proxy"  ── D1: paired devices + key CIPHERTEXT (no KEK)
   │  - POST /pair/register  (desktop → token + key; Worker encrypts, stashes KEK short-TTL)
   │  - POST /pair/claim     (phone presents token → gets { device_token, kek }; KEK deleted server-side)
   │  - ALL /ep/*            (phone → proxy with device_token + KEK; allow-list enforced)
   │  - POST /ep-webhook     (EasyPost webhook → fan out push via APNs/FCM)
   │  - licence re-check against paddle-license backend
   ▼
 Phone (Flutter)  device token in secure enclave (iOS Keychain / Android Keystore)
   - Tracking (+ push), HTS Lookup, Insurance, Claims, Pickups, History, Reports
```

### Pairing flow (zero-standing-custody)
1. Desktop (licensed, production) generates a one-time `pairing_token`
   (`crypto.randomUUID`, short TTL) and calls `POST /pair/register` with the
   token, the production EasyPost key (TLS in transit), and the licence proof.
   The Worker verifies the licence, generates a random **KEK**, AES-GCM encrypts
   the key with it, stores **only the ciphertext** in D1, and stashes the KEK in
   a short-TTL pending-pair record keyed by the pairing token (the only window in
   which the server holds a KEK).
2. Desktop renders a QR encoding `{ pairing_token, proxy_url }` — **not the key,
   not the KEK.**
3. Phone scans, calls `POST /pair/claim` with the token. Worker validates it
   (unused, unexpired, licence still valid), mints a **long-lived device token**,
   returns `{ device_token, kek }` to the phone, then **deletes the pending-pair
   record** (burning the token and the server's copy of the KEK).
4. Phone stores `device_token` + `kek` in the OS secure enclave. All later calls
   go to `/ep/*` with the device token **and** the KEK; the Worker decrypts the
   key in-memory for that one call, hits EasyPost, filters, returns, discards.
5. "Change key / re-pair" on desktop mints a new token → new KEK/ciphertext,
   rotating the key and letting old device tokens be revoked.

### Endpoint allow-list (server-enforced)
- **Allowed (read):** trackers retrieve/list, shipments list/retrieve, HTS is
  external (USITC, no key — proxy or call direct), claim status, pickup list.
- **Allowed (spend, confirm-gated):** insurance create, pickup create/cancel,
  claim create.
- **Refused (403):** shipment rate-buy, label purchase, any key/account mutation.

### Push notifications
The proxy is a permanent public URL, so it becomes the EasyPost webhook target
directly — no `cloudflared` tunnel needed (that was a desktop-only limitation).
`tracker.updated` events fan out to APNs (iOS) / FCM (Android) for the paired
devices tracking that shipment. This is the single most compelling reason the
app exists.

## Repos & infra
- **New private repo:** `github.com/sgf36/EasyPost-Mobile` (Flutter app +
  `server/easypost-mobile-proxy` Worker). Keeps mobile churn out of the desktop
  repo; the proxy sits beside it for one-command deploys.
- **Reuse:** the `paddle-license-webhook` backend for licence validity; the
  Cloudflare account already used by `mcp-relay-worker`.
- **New:** D1 database `easypost-mobile` (paired devices, encrypted keys, push
  tokens); Worker secrets for the envelope master key + APNs/FCM credentials.

## CI / build & release (mirrors the Mac App Store leg)
- **Android:** GitHub Actions builds the signed `.aab` (needs an upload keystore
  — you generate it, set as secrets, same pattern as the MAS certs). Uploaded to
  Play Console.
- **iOS:** `macos-latest` runner archives + signs the `.ipa` (reuses your Apple
  Developer cert + a new App ID + provisioning profile) and `altool`-uploads to
  App Store Connect, exactly like the MAS `.pkg` step.
- I write the workflows; you set the signing secrets (secret-set is blocked for
  me by design) and click "Submit for Review" in each store console.

## What I build vs. what you do

**I do (no blockers, starting now):**
- The `easypost-mobile-proxy` Worker (pairing, proxy allow-list, encryption,
  webhook→push) — I can deploy it via the Cloudflare/Wrangler toolchain here.
- Desktop-side pairing screen (QR generation + `/pair/register` call) in the
  PySide6 app, licence-gated.
- The full Flutter app (I can write all Dart; it compiles in CI, not on this
  Windows box).
- Both CI workflows + all setup docs (like CI-MAS-SETUP.md).

**You do (when we reach each):**
- Register a **Google Play Console** account ($25 one-time).
- Confirm the new repo name `EasyPost-Mobile` (I'll create it on your `gh`).
- Create the iOS App ID + provisioning profile, Android keystore; set the
  signing secrets (I'll give exact PowerShell commands).
- Store listing assets (icon is reused/resized; screenshots come from the built
  app) and the "Submit for Review" clicks.
- A one-line privacy-policy addition (key custody) — I'll draft it.

## Reviewer / demo access (required for store approval)

App-store reviewers (Apple App Store Connect, Google Play) cannot complete QR
pairing — they have no licensed desktop — so a build with no bypass **will be
rejected**. The app therefore offers a "Enter review code" path on the pairing
screen. The reviewer types a code; the app calls `POST /pair/demo`, the Worker
validates it against the `REVIEW_CODE` secret and pairs the app against a **demo
EasyPost TEST-mode key** (`DEMO_EASYPOST_TEST_KEY` secret) — a fully working app
on fixture data, no real money, no user's key involved. The demo device is
tagged `tier=demo` and can be revoked any time. The same code + notes go in each
store's "notes for reviewer" field at submission. (This is the mobile equivalent
of the desktop's comp-key unlock.)

## Phases
- **Phase 0 — spike:** proxy (`/pair/*` + one `/ep/trackers` read) deployed +
  desktop QR screen + a minimal Flutter app that pairs and shows one live
  tracker. Retires the only novel risk (end-to-end pairing).
- **Phase 1 — MVP:** Tracking + **push** + HTS Lookup. Shippable, differentiated.
- **Phase 2:** Insurance, Claims, Pickups (confirm-gated writes).
- **Phase 3:** History, then Reports (Shipments API + on-device aggregation).

## Open items to confirm as we go (not blocking Phase 0)
- Repo name `EasyPost-Mobile` OK?
- Privacy-policy wording for key custody (I draft, you approve/publish).
- Whether to count a paired phone as a licence seat.
