# Go-live checklist

State as of 2026-07-23. Everything in the repository is finished, committed and
pushed. What remains needs either a credential this project does not hold or a
button only the account owner should press.

## Done

| Piece | State |
|---|---|
| Store package `1.0.4.0` | Built by CI, downloaded and verified — `resources.pri` present, single `en-US` resource language, signed, no variant flags, no MCP helper, no donation strings |
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

## Remaining — one action

### Prove the payment path before announcing

**This is the one thing not to assume.** Every component has been verified in
isolation, but no purchase has ever run end to end. The failure mode is silent
and expensive: a customer pays, no licence arrives, and the first you hear of
it is a complaint.

Two secrets could not be checked, because Cloudflare does not expose secret
values through its API:

- `RESEND_API_KEY` — wrong and the licence is minted but never emailed
- `LICENSE_PRIVATE_KEY_PEM` — wrong and the minted key fails to verify in the app

Run one free purchase:

1. Paddle → Discounts → 100% off, code `EPDPIPELINETEST`, **usage limit 1**,
   **restricted to** `pri_01ky2hekjfm1c9nspf5pnqv0jv`, **expiring** in days.
   The limit, restriction and expiry are not optional hygiene — an unbounded
   100% code on a live store is a standing liability.
2. Buy Personal on the published pricing page with that code, total $0.
3. Confirm a licence email arrives and the key activates the app.
4. Delete or let the code expire.

## Worth knowing

**The webhook signing secret is in this session's transcript.** Paddle returns
`endpoint_secret_key` in plaintext from `notificationSettings.list`, so it was
written to the local `.jsonl` log. Nothing is compromised, but if that bothers
you: regenerate the destination's signing secret in Paddle, then
`npx wrangler secret put PADDLE_WEBHOOK_SECRET`. Do it *after* the test above
passes, so a failure is never ambiguous between two causes.

**The API key still lacks `discount.write`**, which is why the test code above
is created by hand rather than by tooling. Leave it that way unless there is a
reason to widen a live key.

**Enterprise stays a mailto** on the pricing page. It is an enquiry, not a
purchase, and should not open a checkout.
