# Easy-Post Desktop — Mac App Store build: a compliant, feature-maximal plan

**Status:** planning document. Nothing here is built yet.
**Author's brief:** ship a Mac App Store (MAS) version of Easy-Post Desktop that
keeps as many of the app's valuable features as Apple's rules allow, using
creative architecture where a naïve port would have to drop a feature.

**One-line conclusion:** a MAS build can keep the entire core shipping workflow
unchanged, must route the paid unlock through Apple's In-App Purchase, and can
keep its two headline differentiators — real-time push and AI-agent (MCP) access
— **only** by moving them off the local-subprocess design onto an outbound
relay. Without that relay work the MAS build simply falls back to polling and
drops MCP, and is still a very complete app. The gating unknown is not any single
feature; it is whether a sandboxed PySide6/Python bundle clears App Review at
all, which a one-week spike settles before any feature work begins.

---

## 1. The three hard constraints

Everything below follows from three Apple rules that do not apply to the
Microsoft Store or the direct download.

1. **The App Sandbox is mandatory.** Every MAS app runs inside
   `com.apple.security.app-sandbox`. File access, spawning executables, and
   inbound network listeners are all restricted to what the app's entitlements
   explicitly grant. This is the rule that collides with the webhook tunnel and
   the MCP helper.

2. **Digital unlocks must use In-App Purchase.** App Review guideline 3.1.1
   requires that unlocking features or functionality inside the app go through
   StoreKit. The existing Paddle → Cloudflare Worker → Ed25519 licence flow
   cannot be used for a MAS sale. This mirrors exactly what was already built for
   the Microsoft Store add-on, so the shape is familiar.

3. **No downloading and running executable code.** Guideline 2.5.2 forbids an
   app fetching and executing a binary. The current push feature downloads and
   runs `cloudflared`; that alone would fail review, independently of the
   sandbox.

Two things that are **not** blockers, contrary to first impressions:

- **Cost.** The $99/year Apple Developer Program membership is already paid (it
  was needed for the Developer ID certificate and notarised `.dmg`). Commission
  is **15%** under the Small Business Program (under $1M/year), not 30%. Net of
  Paddle's ~5% + $0.50, the incremental cost of the Apple channel is roughly
  **8 percentage points**, about $2.50 on a $29.99 sale.
- **Qt/Python on MAS.** It is possible — sandboxed PySide6 apps have shipped —
  but it is uncommon and carries the single biggest technical risk in this plan
  (see §7).

---

## 2. Feature disposition matrix

Every current feature, and what happens to it on MAS.

| Feature | MAS disposition | What changes |
|---|---|---|
| First-run setup / API-key entry | **Keep** | none |
| Credential store (Keychain via `keyring`) | **Keep** | `keychain-access-groups` entitlement (app's own group) |
| Rate shopping | **Keep** | `network.client` |
| Buy label | **Keep** | none (network) |
| Print label | **Keep** | `print` entitlement |
| Save label (PNG/PDF/ZPL/EPL) | **Modify** | write via Save panel or to `~/Downloads`; add "Reveal in Finder" |
| Tracking — polling refresh | **Keep** | baseline, always on |
| Address book + verification | **Keep** | none |
| Customs + HTS lookup (USITC) | **Keep** | `network.client`; SQLite cache in container |
| Batch CSV import + bulk buy | **Modify** | open the CSV via the file panel (user-selected access) |
| Refunds / void | **Keep** | none |
| Insurance | **Keep** | none |
| Pickups | **Keep** | none |
| Claims | **Keep** | none |
| History / reporting (SQLite) | **Keep** | DB lives in the app container |
| 50-language i18n | **Keep** | none |
| Test/production mode banner | **Keep** | production gate reads StoreKit instead of the licence |
| **Production unlock ($29.99)** | **Re-implement** | StoreKit non-consumable IAP; Paddle/seat-ledger bypassed |
| **Real-time tracking push** | **Re-architect or drop** | outbound Worker relay (keep) — or fall back to polling (drop) |
| **AI-agent access (MCP)** | **Re-architect or drop** | companion helper or hosted remote MCP (keep) — or omit (drop) |
| In-app updater | **Disable** | the Mac App Store delivers updates |
| Donation banner (Stripe) | **Remove** | guideline 3.2.2: for-profit apps may not solicit donations via external links |
| Seat ledger / device list / machine-id | **Drop for MAS** | Apple ties the entitlement to the Apple ID; no seat counting needed |

Count: of the ~20 user-facing capabilities, **13 are kept unchanged**, **4 are
trivial modifications**, **1 is re-implemented on StoreKit**, and only **2**
(push, MCP) require real architectural work to retain — or graceful degradation
if not.

---

## 3. The two features that need creativity

### 3.1 Real-time tracking push

**Why the current design fails on MAS.** Today: the app opens a local HTTP
receiver on `127.0.0.1`, launches `cloudflared` as a subprocess to expose it on
a public `*.trycloudflare.com` URL, and registers that URL as an EasyPost
webhook. Under the sandbox, spawning `cloudflared` is not permitted, and
downloading it violates 2.5.2. An inbound listener would need
`network.server` and still would not solve the tunnel.

**Creative fix — invert the direction (recommended).** EasyPost already has a
public, stable place to send webhooks: the **Cloudflare Worker** that mints
licences. Point EasyPost's webhook at the Worker, and have the desktop app hold
a persistent **outbound** WebSocket to the Worker, which pushes tracker events
down it.

```
Before (local, sandbox-hostile):
  EasyPost ──webhook──▶ *.trycloudflare.com ──▶ cloudflared ──▶ 127.0.0.1 (app)

After (relay, sandbox-clean):
  EasyPost ──webhook──▶ Cloudflare Worker ──WebSocket push──▶ app (outbound only)
```

- **Sandbox-legal:** the app only makes an *outbound* connection
  (`network.client`). No subprocess, no bundled binary, no inbound port, no
  `network.server`. Nothing to download.
- **Implementation:** a Cloudflare **Durable Object** per account holds the live
  socket and routes incoming EasyPost webhook events to it. The app authenticates
  the socket with a per-account token (derived the same way the activation proof
  is, so no new secret store). Events are re-fetched from EasyPost by the app
  before display, so a spoofed push cannot fabricate a delivery.
- **Bonus:** this upgrade is **cross-platform**. Adopting it lets the Windows and
  direct builds drop the `cloudflared` dependency too — less to install, less to
  break, one code path. This is the highest-leverage item in the whole plan
  because it improves every edition, not just MAS.
- **Fallback if not built:** the MAS build simply keeps polling (already the
  default). The user loses "instant" but not "tracked". Acceptable for a v1.

### 3.2 AI-agent access (MCP)

**Why the current design is awkward on MAS.** Today the app ships an
`easypost-mcp` helper that an external AI client (Claude Desktop, Cursor)
launches over stdio; it reads the same local data and Keychain item. On MAS: (a)
a sandboxed GUI app cannot install a CLI onto the user's `PATH`; (b) the helper,
when launched by the AI client, runs outside our sandbox and would need to be
signed into the same Keychain access group to read the key; (c) writing the AI
client's config file lives outside the app container.

Three routes, most-to-least ambitious:

- **(a) Hosted remote MCP over the relay (most elegant, unifies with §3.1).**
  Expose a remote MCP server on the Worker that the AI client connects to by URL,
  authenticated per account. Read-only queries (shop rates, look up a shipment)
  are brokered to the app through the *same outbound socket* used for push; the
  EasyPost key never leaves the machine for anything but EasyPost, preserving the
  privacy promise. No local helper, fully within MAS rules. Pairs naturally with
  the push relay — build both on one Durable Object.
- **(b) Separately-distributed companion helper (pragmatic).** Ship
  `easypost-mcp` as its own notarised download or Homebrew formula — a *separate
  product*, not delivered through MAS (which is allowed). Sign it into a shared
  Keychain access group and an App Group container so it can read the MAS app's
  key and data. The MAS app shows an "Enable AI agent access" screen that hands
  the user the helper and a copy-paste config snippet (the app cannot write the
  AI client's config from inside its sandbox, so that stays a documented manual
  step).
- **(c) Omit from MAS v1 (safe fallback).** MCP stays a feature of the direct
  download and the Windows Store build. The MAS listing simply does not advertise
  it. Least work, loses the differentiator on this one channel.

Recommendation: ship **(c)** in MAS v1 to get to market, then build **(a)**
alongside the push relay so both differentiators return together.

---

## 4. The paid unlock on StoreKit

This is not optional and not a workaround — it is how money must move on MAS.

- **Product:** a **non-consumable** In-App Purchase, "Production Unlock", priced
  in App Store Connect (Apple's price tiers; set the one nearest $29.99). One
  purchase, restorable, eligible for Family Sharing if enabled.
- **Gate:** the production-action gate (buying real labels) consults the StoreKit
  entitlement instead of `license.py`. This is the *exact* pattern already built
  for the Microsoft Store: a `store_build.flag` variant, `store_entitlement.py`,
  and a `StoreUnlockGate`. The MAS version is a direct analogue.
- **Validation:** verify the StoreKit transaction/receipt (StoreKit 2's
  `Transaction.currentEntitlements`, or on-device receipt validation). Reached
  from Python via **PyObjC** calling the StoreKit framework, or a tiny
  Swift/Obj-C helper bundled in the app that the Python layer calls.
- **What is bypassed on MAS:** Paddle, the Ed25519 licence key, the Cloudflare
  seat ledger, the device list, and `machine_id()`. Apple owns the transaction,
  the "Restore Purchases" flow, and refunds. (Note: `machine_id()` currently
  shells out to `ioreg`, which the sandbox may block — moot here because MAS does
  not seat-count, but if ever needed under sandbox, replace the subprocess with
  an IOKit call via `ctypes`/PyObjC.)
- **Team / multi-seat licensing:** the seat model does not map to Apple's
  per-Apple-ID entitlement. For MAS v1, offer only the single Personal unlock as
  IAP and describe volume licensing as plain text pointing to the website (Apple
  permits describing it; in-app links to external purchase remain legally
  region-dependent, so do not rely on them). Business/Organisation tiers can
  later become auto-renewable subscription IAPs if MAS demand justifies it.

---

## 5. Entitlements

The MAS entitlements plist, minimised to what is actually used (the relay design
lets us **drop** `network.server`, tightening the sandbox):

| Entitlement | Why |
|---|---|
| `com.apple.security.app-sandbox` | required for MAS |
| `com.apple.security.network.client` | EasyPost API, USITC HTS, StoreKit, push/MCP relay |
| `com.apple.security.files.user-selected.read-write` | open a CSV, save a label to a chosen folder |
| `com.apple.security.files.downloads.read-write` | optional: drop labels straight into `~/Downloads` |
| `com.apple.security.print` | print labels |
| `keychain-access-groups` | Keychain credential storage (and sharing with the MCP companion, route 3.2b) |
| `com.apple.security.application-groups` | only if the MCP companion (3.2b) shares the container |

Deliberately **absent**: `network.server` (no local listener with the relay),
any temporary-exception entitlement (those draw review scrutiny).

---

## 6. Packaging, signing and submission

Distinct from the notarised-`.dmg` pipeline that already exists; MAS is a
separate lane.

1. **App Store Connect record:** new macOS app, bundle id (e.g.
   `com.spencerfields.easypostdesktop`), category, the IAP product(s),
   screenshots, and review notes explaining it drives the user's *own*
   third-party EasyPost account with a test-mode option for the reviewer.
2. **Certificates & profile:** "Apple Distribution" and "Mac Installer
   Distribution" certificates, plus a **Mac App Store provisioning profile**
   embedding the entitlements above.
3. **Build:** produce a sandboxed, fully-signed `.app` — every nested
   framework/dylib signed — then wrap it in a signed installer package:
   `productbuild --component App.app /Applications --sign "3rd Party Mac Developer Installer: …"`.
   Upload to App Store Connect with **Transporter** (or `xcrun altool --upload-app`).
4. **CI:** a new `macos` lane parallel to the existing notarised-`.dmg` job,
   carrying the extra signing identities and provisioning profile as secrets.
   Heavier than the current pipeline because of IAP and MAS signing.
5. **TestFlight:** exercise the sandboxed build and the IAP sandbox purchase
   before submitting for review.

---

## 7. The real risk: PySide6/Python through App Review

This is the make-or-break unknown and deserves a spike **before** any feature
work.

- Sandboxed PyQt/PySide apps **have** been accepted on MAS, but it is not a
  well-trodden path. Apple's static analysis rejects any use of private API; some
  older Qt builds tripped this. Modern Qt 6 / PySide6 is generally cleanable but
  **must be verified** against the exact version bundled here.
- PyInstaller output may not be MAS-acceptable as-is (signing of nested binaries,
  bundle layout). `py2app` may produce a cleaner, more reliably-signable `.app`
  for this lane; evaluate both.
- The app must be fully self-contained (its own Python), invoke no system Python,
  and spawn no external binaries — which the relay design already ensures.

**De-risking spike (Phase 0):** build a hello-world sandboxed PySide6 `.app`,
sign it with the MAS identities, and push it through App Store Connect to
TestFlight. If that clears, the platform risk is essentially retired and feature
work is "just" engineering. If it does not, that is the answer — cheaply, before
investing in StoreKit and the relay.

---

## 8. Sequencing and effort

| Phase | Scope | Rough effort | Outcome |
|---|---|---|---|
| **0. Spike** | Sandboxed PySide6 `.app` → TestFlight | ~1 week | Go/no-go on Python-on-MAS |
| **1. MAS v1** | Variant flag, StoreKit IAP unlock, disable updater, remove donation banner, label-save via panel, submit | ~2–3 weeks | Full app on MAS **minus** push + MCP |
| **2. Push relay** | Worker Durable Object, outbound socket; cross-platform | ~1–2 weeks | Real-time push back on **all** platforms |
| **3. MCP on MAS** | Remote MCP over the relay (3.2a) or companion helper (3.2b) | ~1–2 weeks | AI-agent access restored on MAS |

Phases 2 and 3 are the "keep the differentiators" investment and can follow once
MAS v1 proves the channel. Phase 1 alone is a shippable, compelling product.

---

## 9. Recommendation

1. **Do not start with Phase 1.** Start with the **Phase 0 spike** — it is cheap
   and it answers the only question that can sink the whole effort.
2. **Gate the decision on data.** The per-platform activation counter (shipped
   alongside this document) will show whether macOS is a meaningful share of paid
   activations. With no Mac App Store yet, every macOS activation is a
   direct-download customer, so it is a clean demand signal. Build MAS only once
   that signal is real.
3. **When you build, aim for the relay.** The outbound-relay architecture (§3.1)
   is the linchpin: it keeps push and MCP within Apple's rules *and* improves
   every other edition by retiring the `cloudflared` dependency. It turns "MAS
   forces us to drop features" into "MAS pushed us to a better architecture".
4. **MAS v1 can ship without push/MCP** and still be a complete, honest product:
   the full shipping workflow, on the user's own account, with a one-time
   StoreKit unlock — exactly the value proposition that already sells on Windows.

**Net:** with the relay work, a MAS build reaches near-parity with the other
editions, lacking only the seat-based team licensing (which moves to StoreKit or
the website). Without it, MAS loses only instant push and MCP. Either way, no
part of the *core* shipping product has to be sacrificed to Apple's sandbox — the
constraints bite only at the edges, and the sharpest of them (push, MCP) have a
clean architectural answer that is worth building regardless of the App Store.
