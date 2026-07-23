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
| Site checkout | `pricing.html` + `checkout.js` + `thank-you.html`, live client token set |

## Remaining — three actions

### 1. Publish the site

`site/` is committed but not deployed. Upload to the host serving
`easy-post.spencerfields.com`:

```
site/pricing.html      (Buy buttons now carry data-paddle-price)
site/checkout.js       (new)
site/thank-you.html    (new)
```

Nothing else in `site/` changed. There is no deploy script and no stored
credential for the host, which is why this is manual.

### 2. Resubmit to the Microsoft Store

`store_assets/submission/EasyPostDesktop_1.0.4.0.msix` → Partner Center →
Packages → remove 1.0.3.0, upload 1.0.4.0, wait for "Validated" → Submit.

The previous rejection was certification **10.3.4**, "the product failed to
install through the Store". Cause: the manifest declared 47 resource languages
while the package shipped no `resources.pri` at all. `Add-AppxPackage`
tolerates that, which is why sideload testing passed and the defect reached
certification; Store deployment does not. Fixed by generating a real resource
index and declaring the one language the package actually provides.

### 3. Prove the payment path before announcing

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
