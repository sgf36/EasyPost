# Windows code signing via Azure Artifact / Trusted Signing

Replaces the abandoned Certum route. Azure signs the **direct-download Windows
`.exe`** with a Microsoft-managed certificate, which removes the SmartScreen
"unknown publisher" warning. (The Microsoft Store MSIX is already Microsoft-
signed on publish, so this only matters for the `.zip` people download from the
site.)

The CI is already wired (`.github/workflows/build.yml` → "Azure login (OIDC) for
signing" + "Sign Windows build (Azure Artifact Signing)"). It is a **no-op**
until the repo **variable** `AZURE_SIGNING_READY` is set to `true`, so nothing
changes on CI until you finish the Azure side below and flip it on.

Authentication is **OIDC federated credential** — no client secret is ever
stored. Cost: Azure Trusted Signing is ~US$9.99/month (far below a Certum cert).

## The workflow expects these exact names

The wired step hard-codes them; if you name anything differently in Azure, tell
me and I'll change the workflow to match:

| Thing | Value |
|---|---|
| Signing account name | `EasyPostDesktop` |
| Certificate profile name | `EasyPostDesktop` |
| Region / endpoint | West Europe → `https://weu.codesigning.azure.net/` |

## Azure portal steps (owner-only — your Azure account)

1. **Register the resource provider** (once): Azure Portal → Subscriptions →
   your subscription → *Resource providers* → search `Microsoft.CodeSigning` →
   **Register**.
2. **Create the Trusted Signing account**: portal search → *Trusted Signing
   Accounts* → **Create**. Name it **`EasyPostDesktop`**, Region **West Europe**,
   pick the Basic pricing tier.
3. **Identity validation**: in the account → *Identity validations* → **New** →
   **Public**. Enter your details (as an individual, your legal name + address +
   the ID docs it asks for). **Microsoft reviews this — it can take 1–7 business
   days.** Signing cannot work until it shows **Completed**.
4. **Certificate profile**: once identity validation is Completed → the account →
   *Certificate profiles* → **Create** → type **Public Trust** → name it
   **`EasyPostDesktop`** → link the validated identity.
5. **App registration for GitHub OIDC**: Microsoft Entra ID → *App
   registrations* → **New registration** (name e.g. `github-easypost-signing`).
   Note its **Application (client) ID** and **Directory (tenant) ID**.
6. **Federated credential**: that app → *Certificates & secrets* → *Federated
   credentials* → **Add** → scenario **GitHub Actions deploying Azure
   resources**:
   - Organization `sgf36`, Repository `EasyPost`
   - Entity **Branch**, Branch **`main`**
   - (subject becomes `repo:sgf36/EasyPost:ref:refs/heads/main`)
7. **Give the app permission to sign**: the Trusted Signing account → *Access
   control (IAM)* → **Add role assignment** → role **Code Signing Certificate
   Profile Signer** (a.k.a. *Trusted Signing Certificate Profile Signer*) →
   assign to the app registration from step 5.

## GitHub side (you can do these from Windows)

```powershell
# Secrets — from the app registration (step 5) and your subscription:
gh secret set AZURE_CLIENT_ID       -R sgf36/EasyPost --body "<Application (client) ID>"
gh secret set AZURE_TENANT_ID       -R sgf36/EasyPost --body "<Directory (tenant) ID>"
gh secret set AZURE_SUBSCRIPTION_ID -R sgf36/EasyPost --body "<Subscription ID>"

# The switch that turns signing ON (a VARIABLE, not a secret) — flip this LAST,
# only once identity validation is Completed and the profile exists:
gh variable set AZURE_SIGNING_READY -R sgf36/EasyPost --body "true"
```

## Then

- Push to `main` (or re-run the Build workflow). The Windows leg logs into Azure
  over OIDC and signs `dist\EasyPostDesktop\EasyPostDesktop.exe`. Tell me and
  I'll confirm the run and re-cut the current release so the download is signed.
- After that, I'll also update the site/README to drop the "SmartScreen warning
  is expected" copy, since the direct download will no longer trip it.

## Notes

- If Azure only offers the newer **`azure/trusted-signing-action`** (the action
  was renamed from `artifact-signing-action`), tell me and I'll swap the action
  name — the inputs are the same.
- Individual (non-company) identity validation is supported but Microsoft may ask
  for more documentation than for an organisation; the account + profile names
  above are all the workflow cares about.
