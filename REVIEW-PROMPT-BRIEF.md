# In-app review prompt — build brief

**Drafted 2026-08-28. Not built.**

## Why this exists

All three storefronts show **zero ratings** — Mac App Store, Microsoft Store and
the iPhone App Store (Microsoft confirmed via the Store catalogue API on
2026-08-28: `RatingCount: 0`). A zero-rated listing both ranks worse and converts
worse, so every channel in `marketing/README.md` §4 is throttled behind this, and
driving traffic at the listings first makes it compound rather than solve itself.

The obvious fix — ask existing users by name — does not scale here, and it is
worth being precise about why:

- **Store buyers are not contactable.** Neither Apple nor Microsoft gives the
  developer the customer. They can only be reached inside the app.
- **The directly-contactable pool is tiny.** Paddle customers (the `SUMMER26`
  counter read one use), TestFlight testers, and anyone who has emailed support.

Ten ratings cannot be manufactured out of five people. **The in-app prompt is the
only route that scales**, which makes it a precondition for Product Hunt rather
than a nicety to add afterwards.

## What this costs — less than it looks

Both platform SDKs are **already dependencies**, because the Production Unlock
entitlement needs them:

```
requirements.txt:28   winrt-Windows.Services.Store>=3.2 ; win32
requirements.txt:35   pyobjc-framework-StoreKit>=10.0   ; darwin
```

`Windows.Services.Store` carries `StoreContext.RequestRateAndReviewAppAsync`, and
`StoreKit` carries `SKStoreReviewController`. So this is **one new module and one
call site**, not a new SDK integration.

---

## Design

### A third symmetric bridge

Add `app/core/review_prompt.py`, following the shape already established by
`app/core/store_entitlement.py` (Windows) and `app/core/mac_store_entitlement.py`
(macOS): a single public surface, per-channel implementations behind it, and
**imports guarded so the module can never break a build it does not apply to**.

Public surface:

```python
review_available() -> bool          # does this build have a store to rate on
note_successful_shipment() -> None  # count a success; cheap, call freely
maybe_request_review(parent) -> None  # apply the gates, then ask the OS
```

`maybe_request_review` is the only thing a view calls. Every gate lives inside it
so no call site has to know the rules.

### Per channel

| Build | Flag | Mechanism | Behaviour |
|---|---|---|---|
| Microsoft Store | `STORE_BUILD` | `StoreContext.RequestRateAndReviewAppAsync()` | Returns a status — the only channel that tells you anything |
| Mac App Store | `MAS_BUILD` | `SKStoreReviewController.requestReview()` | Fire and forget; no callback, no outcome |
| Direct download | `LICENSE_REQUIRED` | **none** | No automatic prompt at all — see below |

**The direct-download build must never show a review prompt.** There is nowhere
to leave a review: Microsoft requires ownership through the Store, and Mac App
Store reviews require the app to have come from it. Prompting a direct-download
user sends them somewhere they cannot act, which is worse than not asking.

The honest ask for that channel is different — a **passive** menu item under
Help, *"Star the project on GitHub"*, opening the repository. No dialog, no
timing logic, no counter. It is also the only channel where the repository's
single star can move.

### The gates

`maybe_request_review` returns without doing anything unless **all** hold:

1. `review_available()` — a store build.
2. **Production mode**, and `production_allowed()`. A test-mode user has not yet
   had the experience being rated, and has paid nothing.
3. **At least 3 successful shipments or batches**, cumulative across sessions.
   Not the first: a first label is a nervous test, and asking then rates the
   anxiety rather than the product.
4. **At least 7 days since first run.**
5. **At least 120 days since the last prompt.**
6. **Fewer than 3 prompts ever.** Apple's own ceiling is three per 365 days; this
   is deliberately below it.
7. **Nothing went wrong this session** — no failed purchase, no refund, no API
   error, no licence-gate dialog, no update notice. A prompt following friction
   collects the friction.
8. **The window is active.** Do not prompt into a background window.

### When to fire it

At the moment of satisfaction, which here is **the label rendering successfully**
— not the click that bought it. In
`app/ui/views/create_shipment_view.py` the purchase runs at line ~1516
(`buy_shipment` via `run_async`); the hook belongs in its success handler, after
the label is displayed. `app/ui/views/batch_view.py` has the equivalent on batch
completion.

Fire on a **single-shot ~2 second timer** after the success UI settles, so the
prompt never races the label render or interrupts a print. If the user has
navigated away or the window lost focus in those two seconds, skip it — there
will be another shipment.

### Settings to add

`app/core/settings.py`, alongside the existing `store_unlock_confirmed_at`:

```python
first_run_at: Optional[str] = None          # ISO; needed for gate 4
review_success_count: int = 0               # gate 3
review_last_prompted_at: Optional[str] = None   # gate 5
review_prompt_count: int = 0                # gate 6
```

`first_run_at` does not currently exist and must be stamped on first launch.
Back-fill it for existing installs to *now* rather than leaving it null, so the
7-day gate starts from the upgrade rather than firing immediately.

---

## Policy constraints — the ones that get apps rejected

**Do not sentiment-gate.** The tempting design — "Enjoying Easy-Post? " and only
sending the people who say yes to the store — **violates Apple's guidelines** and
is the single most common way this feature gets an app rejected. Everyone who
passes the gates gets the same system prompt, regardless of what they think.

**Do not build a custom dialog.** Use each platform's native call. A homemade
"Rate us" window that mimics the system one is both a rejection risk and less
effective.

**Do not incentivise.** No discount, no unlock, no "rate us to remove this".

**Do not ask for five stars.** The ask is for an honest rating. Anything else is
review manipulation on both stores.

**Expect nothing back from Apple.** `SKStoreReviewController.requestReview()` may
show nothing at all — the system decides, throttles to three per year per device,
and never reports what happened. Treat a call as *requested*, never as *shown*
and never as *rated*. Microsoft's `RequestRateAndReviewAppAsync` does return a
status, which makes it the only channel with any feedback.

---

## Measurement

Local instrumentation can only ever count **prompts requested** — no platform
tells you whether a rating resulted. So the real measurement is store-side, and it
already has a route:

- **Microsoft:** `RatingCount` and `AverageRating` from the public display
  catalogue, no authentication —
  `https://displaycatalog.mp.microsoft.com/v7.0/products/9NDSDL5LV5B5?languages=en-US&market=US`
- **Apple:** `customerReviews` on the App Store Connect API, using the key already
  wired into `tools/asc_analytics.py`.

Worth a small `tools/ratings_watch.py` printing all three counts in one line, run
weekly. That, against the local prompt counter, gives a crude conversion rate —
which is all anyone gets here.

---

## Test plan

The gates are the whole feature, so test those rather than the platform call.

- Each gate individually blocks, by manipulating settings directly.
- All gates passing calls the platform shim exactly once.
- The platform shim is mockable: assert the *decision*, not Apple's or
  Microsoft's behaviour, neither of which is testable in CI.
- Import safety: importing `review_prompt` on Linux, on a dev run, and with the
  SDK missing must not raise — same guarantee the two entitlement modules make.
- `LICENSE_REQUIRED` build never calls anything.
- Timer path: window loses focus within the 2 seconds → no prompt.

---

## The iPhone companion is a separate job

`easypost_mobile_companion` is Flutter, so none of the above applies. It needs the
`in_app_review` package and its own trigger — the natural moment there is a
tracked parcel reaching *delivered*, not app launch. Same gates in spirit, same
policy constraints, different codebase. Worth doing, but as its own piece of work.

---

## Sequencing

This blocks Product Hunt (`marketing/product-hunt-launch.md`), which is gated on
ratings existing. It does not block the Microsoft listing rewrite or the macOS
promotional text, both of which are ready and independent.

Reasonable order: build this → let it run while the listing copy goes in → watch
the counts weekly → launch when the listings are no longer showing zero.
