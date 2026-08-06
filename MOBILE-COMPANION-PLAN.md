# Easy-Post Mobile Companion — feasibility and plan

_Analysis for the "Easy-Post Mobile Companion" iOS + Android apps, companions to
Easy-Post Desktop. Written 2026-08-05._

## Verdict

**Feasible — yes, clearly.** The proposed scope (tracking and account-management,
no label buying) is a mostly-read companion, which is exactly what mobile does
well. Two things need real design thought before any code: (1) **where the
EasyPost production key lives and how the phone reaches EasyPost**, and (2) **how
the phone gets History and Reports data**, which today come from the desktop's
local database, not from EasyPost. Both are solvable. Everything else is routine.

The honest caveat is **effort vs. traction**: this is a whole new codebase for
two platforms plus a pairing mechanism — realistically several weeks of work —
for a product that is still very early on adoption. Worth building lean and
validating, not gold-plating up front (see Phasing).

## Scope (your chosen tabs)

From the desktop's "Track & manage" section plus two Tools:
**Tracking, History, Insurance, Claims, Pickups, Reports, HTS Lookup.**
Deliberately excluded: Create Shipment, Batch, Address Book, Connect AI agents,
Settings-heavy config. Good instinct — the phone is for *watching and managing*
in-flight shipments, not composing and buying labels.

## Platforms

- **iOS (iPhone) and Android** are the two real targets.
- **"Microsoft phones" no longer exist** — Windows Phone / Windows 10 Mobile was
  discontinued by Microsoft in 2019–2020 and has no app store. So there is no
  Microsoft *phone* target. If the intent was "Windows devices", the existing
  desktop app already covers Windows tablets and 2-in-1s; a separate touch build
  could ship to the Microsoft Store later, but that is a different track from a
  phone app. **Recommend: iOS + Android only.**

## Framework

The desktop is Python/PySide6, which **does not port to mobile** in any practical
way — the mobile app is a new codebase regardless. Options:

| Framework | Language | Notes |
|---|---|---|
| **Flutter** (recommended) | Dart | Best mobile UX from one codebase, mature, great for list/detail/tracking screens, strong secure-storage and push plugins. |
| React Native | JS/TS | Also one codebase; fine if you prefer the JS ecosystem (shares language with the Cloudflare Workers). |
| .NET MAUI | C# | Microsoft-aligned, can also target Windows/Mac; mobile polish trails Flutter/RN, but keeps you in one ecosystem. |

**Recommendation: Flutter** for the best phone experience and single codebase.
React Native is a reasonable second if you'd rather stay in TypeScript (matching
the Workers). Either way, plan one shared codebase for both stores.

## The core decision: where the key lives

Your sketch is: desktop shows a QR → phone scans → phone holds the production API
key, hidden, re-scan to change it. That works, but the security model deserves a
deliberate choice, because the EasyPost **production** key can spend money and
read the whole account. Three architectures:

**A. Key on the phone (your original idea).**
The QR pairing delivers the key; the phone stores it in the OS secure enclave
(iOS Keychain / Android Keystore) and calls EasyPost directly.
- Pros: simplest; matches your vision; **keeps your "we never see your key"
  promise** (the key goes desktop→phone directly, never through your servers).
- Cons: a fully compromised (jailbroken/rooted) device could eventually extract
  the key. It is the user's *own* key on their *own* device, so the blast radius
  is their own account — a defensible risk, and how many apps store API tokens.
- Important: **do not put the raw key in the QR itself.** A QR can be photographed
  or shoulder-surfed. Put a **short-lived one-time pairing token** in the QR; the
  phone presents that token over an encrypted channel to receive the key. (See
  Pairing.)

**B. Backend proxy (most secure).**
The phone never holds the EasyPost key. It talks to a Cloudflare Worker of yours,
which holds the key relationship and makes the EasyPost calls, returning only the
data the phone is allowed to see.
- Pros: key never on-device; you can enforce **read/manage-only scope**
  server-side (EasyPost has no read-only sub-keys, so a proxy is the *only* way to
  truly stop a phone from buying labels); you can enforce the paid-licence gate
  centrally and revoke instantly.
- Cons: **your server would receive/hold the user's EasyPost key**, which changes
  today's privacy posture (you currently never see it) and makes you responsible
  for securing it. More backend to build and run.

**C. Desktop proxies (via the relay you already built).**
The phone reaches EasyPost *through* the running desktop over the existing MCP
relay Worker; the key never leaves the desktop.
- Pros: no key on phone, no key on your server.
- Cons: **the desktop must be running and online** for the phone to work — poor
  fit for a mobile companion you want to use with the laptop shut.

**Recommendation:** Start with **A** (key on device, delivered by a one-time
pairing token, stored in the secure enclave). It matches your vision, preserves
the key-privacy promise, and is appropriate given the phone can only *view and
manage*, not compose new shipments. Keep **B (backend proxy)** in your back
pocket as the upgrade if you later want hard server-side scope enforcement or
enterprise customers demand the key never touch the device. Avoid **C** — the
desktop-must-be-online requirement defeats the point.

> Note on scope safety: even in model A, the key technically *can* buy labels via
> the raw API if extracted — the mobile UI simply doesn't expose it. If "the phone
> can never spend money, full stop" is a hard requirement, that forces model B.

## QR pairing flow (secure design)

1. Desktop (already in an activated production state — see Licence gating) shows
   a QR encoding a **one-time, short-lived pairing token** (not the key), plus a
   rendezvous URL.
2. Phone scans, opens an encrypted channel to the rendezvous (reuse the **relay
   Worker** as the meeting point, or a small pairing endpoint), and presents the
   token.
3. The desktop, seeing a valid token presented, sends the **production API key
   encrypted to the phone's public key** over that channel. Phone stores it in
   the OS secure enclave. Token is burned.
4. "Change key / re-pair" = show a new QR, new token, new transfer. Old device
   can be de-authorised.

This keeps the raw key out of the QR image and off your servers, and gives you a
clean revoke/re-pair story.

## Licence gating (mostly free — reuse what exists)

You already have the answer: **production is gated behind a licence on the
desktop**, so **only an activated/licensed desktop can display a valid pairing
QR** in the first place. No licence → no production key → no QR → no mobile
access. That is the gate, enforced naturally.

Strengthen it by having the phone periodically check licence validity against
your **activation Worker + D1 seat ledger** (using a token from pairing), so a
revoked or lapsed licence disables the mobile app too. This also lets you count a
paired phone as (or alongside) a device seat if you wish.

## Per-tab feasibility

| Tab | Feasibility | Notes |
|---|---|---|
| **Tracking** | Easy | EasyPost Trackers API. **Best mobile feature: push notifications** on tracking updates — reuse your existing webhook system (EasyPost webhook → your Worker → push via APNs/FCM). This is the single most compelling reason the app should exist. |
| **HTS Lookup** | Easy | Public USITC API, no auth; direct call + local cache. Trivially portable. |
| **Insurance** | Straightforward | EasyPost Insurance API — buy/list insurance. |
| **Claims** | Straightforward | EasyPost Claims API — file/track claims. Good on-the-go feature. |
| **Pickups** | Straightforward | EasyPost Pickups API — schedule/list/cancel. |
| **History** | Needs a data source | Desktop History is its **local SQLite mirror** of shipments. The phone has no such mirror. Fetch from EasyPost's **Shipments list API** directly (works, paginated), or sync from a backend. |
| **Reports** | Trickiest | `reports.py` = *local aggregate over shipment history, current mode only*. The phone must first obtain that history (as above), then aggregate. Feasible but it is the most involved screen, so schedule it last. |

## Reuse of existing infrastructure

You are not starting from zero — three pieces you already run map directly onto
this:
- **Relay Worker** (`server/mcp-relay-worker`) → pairing rendezvous / secure
  channel between desktop and phone.
- **Activation Worker + D1** → licence validity checks and device/seat tracking
  for the phone.
- **Webhook system** (`app/core/webhook_manager`, the Cloudflare tunnel) → the
  backbone for **push notifications** (repoint the webhook at a Worker that fans
  out to APNs/FCM).

## Distribution

- **iOS App Store** — covered by your existing $99/yr Apple Developer membership
  (same one as the Mac App Store). App can be **free** (gated by desktop licence
  via pairing), so no StoreKit IAP needed → simpler review.
- **Google Play** — one-time $25 registration. Also free/gated.
- Both stores are fine with a "companion app, requires the paid desktop product"
  model as long as the listing is honest about it.

## Effort and phasing

A realistic build, solo, is **several weeks**. Phase it so value lands early and
the hard parts come when justified:

- **Phase 0 — spike (½–1 week):** prove the pairing flow end-to-end (desktop QR →
  token → encrypted key transfer → secure-enclave storage) and one live EasyPost
  read (a tracker) from the phone. This retires the only novel risk.
- **Phase 1 — MVP (highest value):** Tracking + **push notifications** + HTS
  Lookup. This alone is a genuinely useful app and the real reason to have one.
- **Phase 2:** Claims, Pickups, Insurance (management actions).
- **Phase 3:** History, then Reports (the data-source work) last.

## Risks and honest take

- **Security posture of the key** is the main design risk — resolved by choosing
  model A vs B up front (recommend A, with the token-not-raw-key pairing).
- **History/Reports data** is the main engineering risk — resolved by sourcing
  from EasyPost's Shipments API rather than assuming a local mirror.
- **Return on effort:** two mobile apps is a large investment relative to current
  desktop adoption. Building **Phase 0 + Phase 1 only** (tracking + push) gets a
  shippable, differentiated app for a fraction of the cost, and you can gauge
  whether users want the rest before building it.

## Recommendation, in one line

Yes, build it — **Flutter, iOS + Android, key-on-device via a one-time-token QR
pairing into the secure enclave, licence-gated through your existing activation
backend, starting with a tracking-plus-push MVP** — and treat History/Reports as
a later phase once you have a shipment-history data source.
