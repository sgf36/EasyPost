# Paddle licence webhook — runbook

**Read this before touching the webhook. Every time.**

This document exists because the same failure has now cost four separate
debugging sessions across three months, and each time it presented as a
*different* fault. It is not a different fault. It is almost always the same
one, described below in §2.

Last verified: **2026-08-16**.

---

## 0. THE ACTUAL ROOT CAUSE (2026-08-16) — read before anything else

Every previous session, including three re-pastes in one evening, blamed the
wrong thing. The real cause was this:

> **The literal string `PADDLE_WEBHOOK_SECRET` in the command was being replaced
> with the secret value itself.**

That is, this was run:

```bash
npx wrangler secret put <the-actual-70-char-secret> --name easypost-license-webhook
```

`wrangler secret put <KEY>` takes the **name** of the secret as its argument and
prompts for the **value**. So this created a *new secret whose name was the
signing secret*, and then stored whatever was typed at the prompt under it —
while `PADDLE_WEBHOOK_SECRET` kept its old, wrong value.

**The command is literal. `PADDLE_WEBHOOK_SECRET` is typed exactly as written.
The secret goes at the interactive prompt, never on the command line.**

```bash
npx wrangler secret put PADDLE_WEBHOOK_SECRET --name easypost-license-webhook
#                       ^^^^^^^^^^^^^^^^^^^^^ literal, do not substitute
```

### How to spot it in ten seconds

`wrangler secret list` should show **exactly** these seven names:

```
ANTHROPIC_API_KEY  CONTACT_SHARED_SECRET  LICENSE_PRIVATE_KEY_PEM
PADDLE_API_KEY     PADDLE_WEBHOOK_SECRET  RESEND_API_KEY  STATS_SECRET
```

Any name that looks like a credential **is** a credential, sitting in plain
text in the Cloudflare dashboard. Delete it immediately and clean up per §8.

### The fingerprint trick that finally solved it

Do not guess whether the right value landed. Compare one-way hashes — safe,
instant, and definitive. Temporarily add to the Worker:

```js
if (request.method === "GET" && url.pathname === "/__secretprobe") {
  const s = String(env.PADDLE_WEBHOOK_SECRET ?? "");
  const sha = async (v) => {
    const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(v));
    return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
  };
  return Response.json({
    len: s.length,
    sha256_trimmed: await sha(s.trim()),
    charset_ok: /^[A-Za-z0-9_]+$/.test(s),   // a real Paddle secret is [A-Za-z0-9_] only
    distinct_chars: new Set(s).size,
  });
}
```

Then hash Paddle's `endpoint_secret_key` and compare. **A correct value reads
`len: 70`, `charset_ok: true`, `distinct_chars: ~41`.** The wrong value in this
incident read `len: 70`, `charset_ok: false`, `distinct_chars: 36` — same
length, so length alone would not have caught it.

**Remove the probe endpoint and redeploy as soon as the comparison is done.**

---

## 1. What the thing actually is

```
Paddle (payment)  --webhook-->  Cloudflare Worker  --D1-->  licence row
                                       |
                                       +--Resend--> licence key email
```

- **Worker:** `easypost-license-webhook`, account `a42628a9005b34444464195eecce2564`
  (`spencer@spencerfields.com`)
- **URL:** `https://easypost-license-webhook.sgf36.workers.dev/paddle/webhook`
- **Auth:** HMAC-SHA256. Paddle sends `Paddle-Signature: ts=<unix>;h1=<hex>`,
  where the hex is `HMAC(secret, "<ts>:<raw body>")`.
- **Secret:** Worker secret `PADDLE_WEBHOOK_SECRET`, which must equal the
  `endpoint_secret_key` of **the Paddle notification destination that is
  actually delivering the event**.

That last clause is the whole problem.

---

## 2. THE TRAP — read this one first

**At least FOUR Paddle notification destinations point at the exact same URL,
each with a DIFFERENT signing secret.**

| ID | Name | Secret fragment | Role |
|---|---|---|---|
| `ntfset_01kyfxwd5ah6djw2cc3fn5wagx` | LIVE licence webhook - USE THIS ONE | `01kyfxwd` | **Canonical. Keep.** All Aug purchase history; the Worker holds this secret. |
| `ntfset_01kyfxsvp00xzb11tnkn9x26t0` | DUPLICATE - DO NOT USE | `01kyfxsv` | Dead. Never received an event. |
| `ntfset_01ky3g1b29r9zvgz1vyw9n6wyh` | Easy-Post Desktop licence issuer (Cloudflare Worker) | `01ky3g1b` | Dead since July. **The worst-named decoy** — it sounds the most correct. |
| `ntfset_01m00qas4nz06bbqp09ffk47m2` | Easy-Post Licensing Webhook | `01m00qas` | Created 2026-08-14 and left **active**. Invisible to event-grouping because it never fired. |

### Why there are so many: Paddle cannot rotate a secret in place

**There is no "rotate secret" action.** To change a signing secret you must
**create a new notification destination**, which gets a **new ID and a new
secret**, then retire the old one. So *every rotation permanently adds another
destination pointing at the same URL*. The trap in this section is not an
accident — it is the direct, cumulative result of rotating four times.

Consequences that have each caused a real outage:

- **The identification prefix changes on every rotation.** A secret is
  `pdl_` + *its own* destination id + `_` + 32 random chars, so after rotating,
  the live secret no longer starts with the old id. Any note saying "the correct
  one contains `01kyfxwd`" is only true until the next rotation. **Treat the
  prefix as an identifier for *today's* destinations, not a permanent fact.**
- **Two steps, not one.** Creating the destination does nothing on its own; the
  new secret must also go into the Worker. Doing only the Paddle half gives
  `401 invalid signature`; doing only the Worker half gives the same.
- **Disable the old destination**, or it stays active with a secret the Worker
  no longer holds and 401s on every event forever.

**The correct rotation procedure:**

1. Create a new destination in Paddle, URL
   `https://easypost-license-webhook.sgf36.workers.dev/paddle/webhook`,
   subscribed to the same events, `traffic_source: all`.
2. Copy its secret (copy button) and run §4's `wrangler secret put`.
3. Verify per §5 — replay, expect **200**.
4. **Only then** disable the previous destination.
5. Delete any destination that is not the live one, so the list stays at one.

Doing step 4 before step 3 leaves a window with no working destination.

**The live destination is whichever one's secret is in the Worker — not
whichever one is named most convincingly.** Confirm by replay or SHA-256, never
by name.

### ⚠️ The API cannot enumerate destinations — the dashboard is the only list

`notificationSettings.list()` returns `[]` under this key for **every** filter
combination: default, `active: true`, `active: false`, both `traffic_source`
values, explicit `per_page`, explicit `order_by`. All seven were tried on
2026-08-16. `get(<id>)` works, but only if the id is already known.

Grouping notifications by `notification_setting_id` finds only destinations that
have **received an event**, so anything newly created is invisible. The fourth
destination above was missed exactly this way and surfaced only because the
account owner read it off the dashboard.

**Never state that the destination list is complete on API evidence alone. Ask
for the dashboard list.**

### Identifying a destination from its secret (verified 2026-08-16)

A Paddle secret is built as **`pdl_` + the destination's own public ID + `_` +
32 random characters** — 70 characters total. So the **first 38 characters are
public**, derivable from the destination ID, and safe to write down, quote or
paste into a ticket. Only the final 32 are secret.

| | Full 38-character prefix | Verdict |
|---|---|---|
| **A** | `pdl_ntfset_01kyfxwd5ah6djw2cc3fn5wagx_` | **KEEP — live** |
| B | `pdl_ntfset_01kyfxsvp00xzb11tnkn9x26t0_` | delete |
| C | `pdl_ntfset_01ky3g1b29r9zvgz1vyw9n6wyh_` | delete |

**Read the first 19 characters to tell them apart.** All three share
`pdl_ntfset_01ky`, which is precisely why they get confused:

```
pdl_ntfset_01kyfxwd...   A  KEEP
pdl_ntfset_01kyfxsv...   B  delete
pdl_ntfset_01ky3g1b...   C  delete
                ^^^^
           characters 16-19
```

Character 16 separates C from A/B. **Character 18 is the one that matters:
`w` = keep, `s` = delete.**

**So you can always check what you copied without revealing anything secret:**
the correct value contains **`01kyfxwd`**. If it contains `01kyfxsv` or
`01ky3g1b`, it is the wrong one, and the Worker will return `401 invalid
signature` no matter how many times you re-paste it.

### Why this keeps happening

Three entries, one URL, similar names, and the *most descriptive* name
(`…licence issuer (Cloudflare Worker)`) is attached to a **dead** destination.
Every instinct says pick that one. It is wrong.

**Permanent fix: delete the two dead destinations.** They hold nothing worth
keeping. As long as three entries exist, this will recur.

---

## 3. Diagnosis — do this in order, do not skip

### Step 1: is a destination even active?

`notificationSettings.list()` **returns `[]`** under the scoped API key, for
every `traffic_source`. This looks exactly like "no webhook is configured" and
**it is a lie**. Never conclude anything from an empty list.

Use `notificationSettings.get(<id>)` instead — that works. Get IDs by reading
`notification_setting_id` off any notification:

```js
const n = await client.notifications.get("<any notification id>");
n.notification_setting_id
```

To enumerate every destination that has *ever* received an event, group all
notifications by `notification_setting_id`. That is how the third destination
was finally found on 2026-08-16, after two sessions assumed there were two.

### Step 2: read the delivery log, not the status

The notification's own `status` is too coarse. Get the real HTTP code:

```js
await client.notifications.logs.list("<notification id>", { per_page: 10 })
// -> [{ response_code, response_body, attempted_at }]
```

### Step 3: map the symptom

| Symptom | Meaning | Fix |
|---|---|---|
| `status: failed`, `times_attempted: 0`, no log entries | Destination is **inactive**. Paddle never even tried. | Activate it. |
| Log `401` `invalid signature` | Destination active, **secret mismatch**. | §4 — and check *which* destination you copied from. |
| Log `500` | Worker threw. The error boundary names the stage in the body. | Read the stage. |
| `status: delivered`, log `200` | Working. | — |

---

## 4. The fix

1. Paddle → Developer tools → Notifications → open **"LIVE licence webhook -
   USE THIS ONE"**. Confirm the ID ends `…wagx`.
2. Reveal the secret. **Confirm with your eyes that it contains `01kyfxwd`.**
   Use the copy button if there is one — a dragged selection can pick up
   trailing whitespace, which is invisible at a masked prompt.
3. In an interactive terminal — **never as a command-line argument**, which
   would put the secret into shell history and the process list:

```bash
npx wrangler secret put PADDLE_WEBHOOK_SECRET --name easypost-license-webhook
```

   The `--name` flag makes it work from any directory. Without it, and without
   a `wrangler.toml` in the current directory, you get
   `[ERROR] Required Worker name missing`.

4. Verify with §5. **Do not skip this.** "Wrangler said success" only proves the
   value was stored, not that it is the right value.

### Confirming the secret actually landed

`wrangler secret put` creates and deploys a **new Worker version**. So:

```bash
npx wrangler versions list      # newest entry ≈ the moment you ran it
npx wrangler deployments list   # Source: "Secret Change", (100%) <version>
```

If the newest version predates your `secret put`, it did not take.

---

## 5. Verification — the only proof that counts

Replay a **no-op** event and read the response code.

```js
// transaction.updated is a no-op: the Worker only acts on adjustment.created,
// subscription.*, and transaction.completed. Everything else -> json({ignored}).
// So this tests signature handling WITHOUT minting a licence or sending email.
const r = await client.notifications.replay("ntf_01m00k7bdjfz0qqh91wg6efdvt");
// then read logs for r.notification_id -> expect response_code 200
```

**200 = fixed. Anything else = not fixed.**

⚠️ **A replay only ever goes to the destination the original notification
belonged to.** You cannot use a replay to test a *different* destination. To
test another one you need a simulation (`client.simulations.create` with that
`notification_setting_id`) or a real event.

The genuine end-to-end proof is buying one real licence and confirming the key
arrives by email. Until that has happened, the money path is unproven.

---

## 6. False leads — all four measured and disproved, do not re-investigate

Each of these cost real time. The evidence against them is recorded so nobody
re-runs the same dead end.

**"The 300-second signature tolerance rejects replayed old events."**
No. `SIGNATURE_TOLERANCE_SECONDS = 300`, but Paddle **re-signs every delivery
attempt with a fresh timestamp**. Proof: `ntf_01m00k7bgdtae9910ez9stjttc`
occurred at 17:20:08 and was delivered at 17:28:39 after 8 attempts — 33
minutes later, and it returned 200. Replaying old events is a valid test.

**"`node:crypto` is stubbed by unenv in the deployed bundle."**
This *was* true once and was fixed by redeploy, so it is a tempting repeat
diagnosis. Verified false on 2026-08-16: the deployed bundle line 935 reads
`import { createHmac as createHmac2, ... } from "node:crypto"`. The unenv stubs
present are only `performance` and `console`. Check with
`workers_get_worker_code` and grep for `createHmac` before ever blaming this
again.

**"The `secret put` did not land / went to the wrong worker."**
Checkable, and was false all three times on 2026-08-16. `wrangler versions
list` showed a new version within a minute of each attempt, deployed at 100%,
on the correct account.

**"Paddle has no webhook destination configured."**
Artefact of `notificationSettings.list()` returning `[]` under a scoped key.
See §3 Step 1.

**"`PADDLE_API_KEY` is the cause."**
Measured false in an earlier session — the failing stack was inside
`mintLicense`, not `getCustomerEmail`. **Do not re-put `PADDLE_API_KEY`.**

---

## 8. If the secret has been exposed — the cleanup

Passing a secret as a command-line argument leaks it into more places than the
terminal. All four were found and cleaned on 2026-08-16:

1. **Cloudflare secret names.** `wrangler secret list`, delete anything not in
   the seven-name allowlist in §0:
   ```bash
   npx wrangler secret delete "<leaked-name>" --name easypost-license-webhook
   ```
2. **Wrangler log files.** These record the full command line.
   `%APPDATA%\xdg.config\.wrangler\logs\*.log` — five files held the secret.
   Redact or delete them.
3. **PowerShell history.**
   `%APPDATA%\Microsoft\Windows\PowerShell\PSReadline\ConsoleHost_history.txt`
   (was clean in this incident, but check).
4. **Terminal scrollback.** Close the window.

Then **rotate the secret in Paddle** and re-set it per §4, because the old value
was recoverable from any of the above. Verify with §5.

---

## 9. Known-good state (2026-08-16, verified)

- Worker holds destination A's secret — confirmed by SHA-256 comparison.
- Replay of `transaction.updated` returns **200** `{"ignored":"transaction.updated"}`.
- The original purchase `txn_01m00k1xjf81rzm2rxah3hn27y` issued
  `license_issued`, tier `personal`, at 2026-08-14T17:28:39Z.
- Seven secrets, all correctly named. No probe endpoint deployed.
- **Outstanding:** destination C is still `active` while holding a secret the
  Worker does not have, so it 401s on every real event and generates retry
  noise. Deactivate or delete C and B, leaving only A.

---

## 7. Rules

- **Never** pass a secret as a command-line argument. Interactive prompt only.
- **Never** print secret material into a transcript, a file, or a chat. The
  destination-ID fragment (`01kyfxwd`) is public and safe; the rest is not.
- **Always** verify with §5 after any change. Three separate sessions have
  ended with "it should work now" and it did not.
- **Delete the dead destinations.** This trap is entirely self-inflicted and
  entirely removable.
