# Windows code signing via Azure Artifact Signing

The direct-download Windows build is signed with a Microsoft-managed **Public
Trust** certificate, which is what removes the SmartScreen "unknown publisher"
warning. The Microsoft Store MSIX is re-signed by Microsoft on publish, so this
applies only to the `.zip` people download from the site.

Cost is about US$9.99/month on the Basic tier — far below a conventional
code-signing certificate, and unlike Certum (abandoned) there is no hardware
token, so GitHub-hosted runners can sign. Authentication is an **OIDC federated
credential**: no client secret exists anywhere, in a repo secret or otherwise.

## The live configuration

Read these off the resource, never from memory — the workflow hard-codes all
three and a mismatch fails at sign time with an unhelpful error.

| Thing | Value |
|---|---|
| Signing account | `EasyPostDesktop` |
| Certificate profile | `SpencerFieldsSoftware` — **not** `EasyPostDesktop` |
| Profile type | Public Trust |
| Region / endpoint | West Europe → `https://weu.codesigning.azure.net/` |
| Subscription | M365 Subscription, `96d4ff81-7f5c-4028-bde2-92e1e24e057f` |
| Resource group | `EasyPostDesktop` |
| Tenant | Spencer Fields, `9b52d991-eac1-46ce-a0ee-158ce7579674` |
| Certificate subject | `CN=Spencer Fields, O=Spencer Fields, L=Poole, S=Dorset, C=GB` |
| Identity validation id | `a84ea857-5df0-4999-8900-19f793299ba9` |

**The account and the profile are named differently, and that is the single
easiest thing to get wrong.** The account is named for the product it was first
created for; the profile is named for the publisher, because one profile signs
everything the business ships. An earlier version of this document told you to
name the profile `EasyPostDesktop` to match the account, and the workflow was
written to that instruction — it was wrong.

**A three-day expiry on the certificate is not a fault.** Artifact Signing mints
a short-lived certificate per signing request and the portal shows the current
one; there is nothing to renew and nothing to rotate.

## Identity plumbing (all of this already exists)

| Piece | Value |
|---|---|
| App registration | `EasyPostDesktop-GitHubActions` |
| Application (client) ID | `3bc64444-7f28-46d6-9ee6-e90ed9b56649` |
| Federated credential | `github-main-branch` |
| Subject | `repo:sgf36/EasyPost:ref:refs/heads/main` |
| Role assignment | *Artifact Signing Certificate Profile Signer*, scoped to the signing account |

The federated credential is bound to `refs/heads/main`, so **signing can only
ever happen on main**. A pull-request build, a tag build or a build from any
other branch will fail the OIDC exchange rather than sign — which is deliberate,
but it does mean release artifacts have to come from a main build.

## GitHub side

```powershell
gh secret set AZURE_CLIENT_ID       -R sgf36/EasyPost --body "3bc64444-7f28-46d6-9ee6-e90ed9b56649"
gh secret set AZURE_TENANT_ID       -R sgf36/EasyPost --body "9b52d991-eac1-46ce-a0ee-158ce7579674"
gh secret set AZURE_SUBSCRIPTION_ID -R sgf36/EasyPost --body "96d4ff81-7f5c-4028-bde2-92e1e24e057f"

# The switch that turns signing on. A VARIABLE, not a secret.
gh variable set AZURE_SIGNING_READY -R sgf36/EasyPost --body "true"
```

None of the three is a credential — they are directory identifiers, and the
OIDC exchange is what proves the workflow is entitled to use them. They are
repo secrets only because that is where `azure/login` expects to read them.

## What the workflow does

`.github/workflows/build.yml`, Windows leg, in order:

1. Rebuild the direct-download variant with PyInstaller.
2. `azure/login@v2` over OIDC.
3. `azure/artifact-signing-action@v2` signs both top-level executables —
   `EasyPostDesktop.exe` and `easypost-mcp.exe` — with an RFC3161 timestamp.
4. **Verify** with `signtool verify /pa`, which fails the build if either file
   is unsigned or the chain does not validate.
5. *Then* generate `SHA256SUMS.txt`.

**Step 5 must stay after step 3.** Signing rewrites the executables in place, so
checksums taken beforehand describe files nobody will ever download — and those
checksums are published on the download page as the thing people verify against.
The steps used to be in the wrong order.

Step 4 exists because a signing step that silently signs nothing looks exactly
like one that worked; the failure would otherwise surface as a SmartScreen
prompt on a stranger's machine.

## After the first signed build ships

The `.exe` no longer trips SmartScreen, so the copy that warns about it becomes
untrue. Update it **only once a signed build is actually published**, not when
the workflow merges:

- `site/download.html` and its translations — the "a certificate is being
  arranged" paragraph.
- `site/faq.html` and its translations — the SmartScreen question.
- `README.md` — the "Windows SmartScreen warning" section.

Publishing the reassurance before the signed build exists is worse than the
warning, because someone would then meet a prompt the site says cannot happen.

Reputation still accrues per certificate: a brand-new Public Trust certificate
clears SmartScreen far faster than an unsigned binary, but a *Smart App Control*
block on a very fresh certificate is possible on locked-down machines.

## How the identity validation was obtained

Kept because it took two attempts and the constraints are not obvious.

- Apply as **Organization**, not Individual. Microsoft restricts
  individual-developer validation to the **United States and Canada**; Public
  Trust for organizations is available in the **United Kingdom**. Applying as an
  individual from the UK is a dead end.
- Organization validation still runs an AU10TIX face check on the named
  representative, so the first and last name must match the **government photo
  ID** exactly.
- The **Street field caps at 30 characters**. `13 Freeland Park, Wareham Road`
  is exactly 30 and fits. `Lytchett House, 13 Freeland Park` is 32 and does not.
  Do not abbreviate to make something else fit.
- Business identifier: D-U-N-S `506523021`, or ICO `ZC224921`.
- Supporting documents must be **issued within the previous 12 months**, with
  any expiry at least **two months** away. The ICO certificate (17/08/2026 →
  16/08/2027) satisfied both. The first attempt stalled three weeks and Vetting
  Ops then asked for company-registration documents, which a UK sole trader does
  not have.
- There are only **three documentation attempts** before a request is dead.
- Public records are checked. The D&B record and the ICO register both carry the
  Poole address.

## Notes

- The action was renamed: Trusted Signing → Azure Artifact Signing.
  `azure/artifact-signing-action@v2` is current, and
  `azure/trusted-signing-action` now resolves to the same action with the same
  inputs. `signing-account-name` is preferred over the deprecated
  `trusted-signing-account-name`.
- The action authenticates through `DefaultAzureCredential`, picking up the CLI
  session `azure/login` leaves behind. Dropping the login step breaks signing
  even though the secrets are still present.
- Only the two top-level `.exe` files are signed, not the ~100 DLLs under
  `_internal`. That is what SmartScreen evaluates, and signing the rest would
  spend quota to no visible end.
