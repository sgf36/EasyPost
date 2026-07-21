# Privacy Policy

**Easy-Post Desktop**

Last updated: 14 July 2026

Easy-Post Desktop is a desktop application that connects to your own
[EasyPost](https://www.easypost.com/) account to manage shipping. This policy
explains what the application does with your data. In short: your data stays on
your computer and is sent only to the services you choose to use. The developer
does not collect, receive, store or have any access to your data.

## Data stored on your device

All application data is stored locally on the computer where the application
runs. Nothing is uploaded to the developer.

- **EasyPost API keys.** The keys you enter are stored in your operating
  system's native credential vault (Windows Credential Manager on Windows,
  Keychain on macOS, Secret Service on Linux). They are never written to a
  plain-text file and are never transmitted anywhere other than EasyPost's own
  API.
- **Shipping data.** Addresses, shipments, tracking numbers, pickups, claims,
  batch jobs, saved package presets and cached tariff-code lookups are stored
  in a local database file in your user profile. This mirrors your EasyPost
  account for fast local search and reporting.
- **Application settings.** Non-sensitive preferences such as your chosen
  language are stored in a local settings file.

You can remove all of this data at any time by uninstalling the application and
deleting its data folder.

## Network connections

The application makes network connections only to the services required to
perform the actions you request:

- **EasyPost API** (`api.easypost.com`) — used for every shipping operation
  (address verification, rate shopping, label purchase, tracking, insurance,
  pickups, claims and batches), authenticated with the API key you provide.
  Data you enter, such as addresses and customs information, is sent to EasyPost
  as needed to complete these operations. EasyPost's handling of that data is
  governed by [EasyPost's own privacy policy](https://www.easypost.com/privacy-policy).
- **U.S. International Trade Commission** (`hts.usitc.gov`) — used by the
  optional HTS Lookup feature. Only the search term you type is sent; no
  personal or account data is transmitted.
- **Cloudflare** (`trycloudflare.com`) — used only if you explicitly enable the
  optional "Real-time tracking (advanced)" feature, which opens a temporary
  tunnel so EasyPost can push tracking updates. This feature is off by default.
- **Stripe** (`donate.stripe.com`) — reached only if you click the optional
  donation link, which opens a Stripe-hosted page in your web browser. Any
  payment is handled entirely by Stripe under
  [Stripe's privacy policy](https://stripe.com/privacy); the developer receives
  no card or contact details.
- **Licence activation** (`easypost-license-webhook.sgf36.workers.dev`) — used
  by the direct-download build only, and only to record that this computer is
  using one of the places your licence covers. See below for exactly what is
  sent. The Microsoft Store build does not contact it at all.

## Licence activation

Licences cover a set number of computers, so activation records which computers
are using one. This is the only developer-operated service the application
contacts, and it is contacted **once** — when you activate, and again only if
you release a computer or activation is repeated. It returns a signed
confirmation that is stored on your computer and checked without the network
from then on. There is no periodic check-in and no usage reporting.

**What is sent:**

- A **one-way fingerprint** of the computer: a HMAC-SHA256 of a machine
  identifier, keyed by your licence key. The machine identifier itself never
  leaves your computer, and the value cannot be reversed to recover it. Because
  your licence key is the HMAC key, the same computer under a different licence
  produces an unrelated value, so activations cannot be linked across customers.
- A **name for the computer**, taken from its hostname, so that you can tell
  your own machines apart when releasing one. You can see this in the
  application before it is sent.
- The **order reference** contained in your licence key, and the **date**.

**What is never sent:** your EasyPost API keys, addresses, shipments, tracking
data, customs information, any file you open, your IP address as a stored
record, or anything about how you use the application.

**How long it is kept:** for as long as the licence is in use. Releasing a
computer deletes its row immediately. A computer that has not activated for six
months is deleted automatically. Refunding a purchase deletes all of its rows.

**If the service is unavailable:** the application continues to work. It grants
itself a limited grace period and retries later. An outage on the developer's
side is not permitted to stop software you have paid for from running.

## What the developer collects

No analytics and no telemetry. The developer cannot see your API keys, your
shipping data, or how you use the application.

The single exception is the licence activation record described above: for the
direct-download build, the developer holds a fingerprint that cannot be
reversed, a computer name you can see beforehand, an order reference and a
date. That exists solely to count computers against the licence you bought.
Nothing else about your use of the application is recorded anywhere.

## Children

The application is a business shipping tool and is not directed at children.

## Changes to this policy

Any changes to this policy will be published at this address. Material changes
will be noted with an updated date above.

## Contact

Questions about this policy can be raised through the project's issue tracker at
[github.com/sgf36/EasyPost](https://github.com/sgf36/EasyPost/issues).
