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

## What the developer collects

Nothing. The application contains no analytics, no telemetry and no
developer-operated servers. The developer cannot see your API keys, your
shipping data or your usage of the application.

## Children

The application is a business shipping tool and is not directed at children.

## Changes to this policy

Any changes to this policy will be published at this address. Material changes
will be noted with an updated date above.

## Contact

Questions about this policy can be raised through the project's issue tracker at
[github.com/sgf36/EasyPost](https://github.com/sgf36/EasyPost/issues).
