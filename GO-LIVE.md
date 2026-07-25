# Go-live checklist

State as of 2026-07-23. Everything in the repository is finished, committed and
pushed. What remains needs either a credential this project does not hold or a
button only the account owner should press.

## Done

| Piece | State |
|---|---|
| Store package `1.0.4.0` | Uploaded to Partner Center and in certification. Verified — `resources.pri` present, single `en-US` resource language, signed, no variant flags, no MCP helper, no donation strings. WACK run locally: overall WARNING (a pass), the one FAIL an optional Blocked-executables false-positive, the one WARNING DPIAwarenessValidation |
| Store package `1.0.5.0` | Prepared: embeds a Per-Monitor v2 DPI-aware manifest in the exe to clear the WACK DPI warning. Will be built by CI on push; supersedes 1.0.4.0 at the next submission |
| Store listings | 47 languages, 9 screenshots and 9 captions each, imported and verified |
| Paddle catalogue | Product `pro_01ky2h8cfe2ven8ypchnmfbena`; Personal $29 one-time, Business $149/yr, Organisation $349/yr — matches the Worker's `PRICE_TIERS` exactly |
| Webhook destination | `ntfset_01ky3g1b29r9zvgz1vyw9n6wyh`, active, subscribed to the eight events the Worker handles |
| Licence Worker | Deployed at `easypost-license-webhook.sgf36.workers.dev`; `/health` returns 200, `/paddle/webhook` rejects a bad signature with 401 |
| Site checkout | Published live; Paddle overlay confirmed rendering $29 Personal |
| Checkout domain | `easy-post.spencerfields.com` approved, Apple Pay verified |
| Default payment link | Set — this was the last blocker |

## Site — published and verified

`pricing.html`, `checkout.js` and `thank-you.html` are live on
`easy-post.spencerfields.com`, byte-for-byte identical to the repository.

The cPanel upload widget 404s on this Bluehost build, so publishing goes
through cPanel's UAPI from an authenticated browser session:

```
POST /cpsess<token>/execute/Fileman/save_file_content
     dir=/home2/spencgh6/easy-post.spencerfields.com
     file=<name>  content=<utf-8>  from_charset=UTF-8  to_charset=UTF-8
```

Verify afterwards by fetching each file over HTTPS and comparing byte counts —
the File Manager listing alone does not prove what the web server serves.

## Checkout — working end to end

Verified live: clicking Buy opens a real Paddle overlay showing
"Easy-Post Desktop License — Personal, US$29.00 now", with the discount field
and payment step present.

Two account-level settings had to be right, and only one was obvious:

- **Checkout domain approved.** `easy-post.spencerfields.com`,
  `chedom_01ky33xg2xzcaehr4ja6rshm9b`, status `approved`, Apple Pay verified.
- **Default payment link set.** This one is easy to miss: without it, both
  `Paddle.Checkout.open` and API transaction creation fail, and the only
  symptom in the browser is a bare "Something went wrong" overlay. The
  catalogue, webhook, domain approval and `Paddle.PricePreview` all work
  perfectly without it, so nothing else points at the gap. The API is what
  named it — the browser never will.

Diagnosing this cost two wrong hypotheses. When checkout misbehaves, create a
transaction through the API first: it returns the real error in one call.

## Remaining — one action, and it is yours to take

### Prove the licence email actually sends

**This is the one thing not to assume.** Every component is verified in
isolation and checkout is verified live, but no purchase has completed the
chain. The failure mode is silent and expensive: a customer pays, no licence
arrives, and the first you hear of it is a complaint.

Two Worker secrets cannot be read back — Cloudflare does not expose secret
values through its API, by design:

- `RESEND_API_KEY` — wrong and the licence is minted but never emailed
- `LICENSE_PRIVATE_KEY_PEM` — wrong and the minted key fails to verify in the app

A discount code is already created and waiting:

| | |
|---|---|
| Code | `EPDPIPELINETEST` |
| Discount | 100% |
| Usage limit | **1** |
| Restricted to | `pri_01ky2hekjfm1c9nspf5pnqv0jv` (Personal only) |
| Expires | 2026-07-26 |
| Paddle ID | `dsc_01ky9be49hjb3yhd0cwm720nrh` |

The limit, restriction and expiry are not optional hygiene — an unbounded 100%
code on a live store is a standing liability.

To run it:

1. Open <https://easy-post.spencerfields.com/pricing.html> and click
   **Buy — $29**.
2. Enter your email, then **Add discount** → `EPDPIPELINETEST`. Total becomes
   $0.00.
3. Complete the checkout. Nothing is charged.
4. Confirm the licence email arrives and the key activates the application.
5. The code is single-use, so it burns itself out. Nothing to clean up.

Why you and not the tooling: completing a checkout means entering personal
details and pressing the final confirm on a live payment page. That is the
account owner's action, even at zero cost.

**If the email does not arrive**, the licence was almost certainly minted and
the failure is delivery. Check the Worker's logs and `RESEND_API_KEY` first.
**If the email arrives but the key will not activate**, suspect
`LICENSE_PRIVATE_KEY_PEM` — the app verifies against the public half, so a
mismatched pair produces a well-formed key that fails verification.

## Worth knowing

**The webhook signing secret is in this session's transcript.** Paddle returns
`endpoint_secret_key` in plaintext from `notificationSettings.list`, so it was
written to the local `.jsonl` log. Nothing is compromised, but if that bothers
you: regenerate the destination's signing secret in Paddle, then
`npx wrangler secret put PADDLE_WEBHOOK_SECRET`. Do it *after* the test above
passes, so a failure is never ambiguous between two causes.

**The live API key now holds `discount.write`.** It was widened to create the
test code. Consider narrowing it again once the pipeline is proven — a live key
that can mint 100% discounts is worth more to an attacker than one that cannot.

**Enterprise stays a mailto** on the pricing page. It is an enquiry, not a
purchase, and should not open a checkout.
