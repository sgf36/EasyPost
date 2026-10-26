# CI — Microsoft Store automated submission setup

One-time setup so the Windows CI leg submits the MSIX to the Microsoft Store
automatically after every push to `main`, matching the Mac App Store
auto-submit on the macOS leg.

The submission API lives at `manage.devcenter.microsoft.com/v1.0/my/…` and
authenticates with an Azure AD client-credentials grant — the same
mechanism the Mac App Store step uses for App Store Connect, but through
Microsoft's identity platform instead of Apple's.

## Prerequisites

- The app must already have at least one completed submission in Partner
  Center, including the age-ratings questionnaire. The API cannot create an
  app from scratch — it can only create new submissions (clones of the last
  published one).
- An Azure AD directory associated with the Partner Center account.

## Step 1 — Associate an Azure AD app with Partner Center

This can be the **same app registration** used for Azure Artifact Signing
(`EasyPostDesktop-GitHubActions`, client ID in `AZURE_CLIENT_ID`) or a new
one. The signing app uses an OIDC federated credential with no client
secret; the Store submission API requires a client secret, so either add
one to the existing app or create a dedicated app.

1. Open **Partner Center → Account settings → User management → Azure AD
   applications**.
2. Click **Associate Azure AD application** and select the app registration
   (or create a new one from this page).
3. Assign the **Manager** role to the application.
4. Copy the **Tenant ID** and **Client ID** from the application settings.
5. Click **Add new key** and copy the **Key** (client secret). It is shown
   only once.

## Step 2 — Find the Partner Center application ID

The API uses an internal application ID, not the public Store product ID
(`9NDSDL5LV5B5`). To find it:

```bash
# Get a token
TOKEN=$(curl -s -X POST \
  "https://login.microsoftonline.com/<TENANT_ID>/oauth2/token" \
  -d "grant_type=client_credentials&client_id=<CLIENT_ID>&client_secret=<SECRET>&resource=https://manage.devcenter.microsoft.com" \
  | python -c "import json,sys; print(json.load(sys.stdin)['access_token'])")

# List apps
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://manage.devcenter.microsoft.com/v1.0/my/applications" \
  | python -m json.tool
```

The response lists each app with its `id` and `primaryName`. Copy the `id`
for Easy-Post Desktop.

Or run the script without `--app-id` — it lists available apps and exits:

```bash
python packaging/submit_to_store.py --msix x --version x --whats-new x
```

## Step 3 — Add repo secrets

Add these four secrets to the repository (`Settings → Secrets → Actions`):

| Secret                         | Value |
|--------------------------------|-------|
| `PARTNER_CENTER_TENANT_ID`     | Azure AD tenant ID from step 1 |
| `PARTNER_CENTER_CLIENT_ID`     | Azure AD app client ID from step 1 |
| `PARTNER_CENTER_CLIENT_SECRET` | Client secret (key) from step 1 |
| `PARTNER_CENTER_APP_ID`        | Internal application ID from step 2 |

The CI step is gated on `PARTNER_CENTER_CLIENT_SECRET` — until that secret
exists the step is silently skipped, just like the Mac App Store and Azure
Artifact Signing steps.

## How the CI step works

After the MSIX is signed and the artifacts are uploaded, the Windows leg:

1. Extracts the marketing version from `app/config.py`.
2. Extracts the "What's New" copy from `store_assets/RELEASE-NOTES-{version}.md`.
3. Runs `packaging/submit_to_store.py`, which:
   - Obtains an Azure AD access token.
   - Finds the app by `PARTNER_CENTER_APP_ID`.
   - Deletes any pending submission (left over from a failed run or a
     Partner Center edit — the API requires no pending submission to exist).
   - Creates a new submission (a clone of the last published one).
   - Marks old packages for deletion and adds the new MSIX.
   - Sets `releaseNotes` in every listing locale.
   - Uploads the MSIX (inside a ZIP) to the SAS blob URL.
   - Commits the submission.
   - Polls until the submission is accepted for processing (PreProcessing
     or Certification).

Certification and publish happen asynchronously on Microsoft's side (usually
a few hours to a few days).

## Verifying

After adding the secrets, push a commit to `main`. The "Submit MSIX to
Microsoft Store" step should appear in the Windows build leg. If it fails,
the build artifact is still uploaded — the submission step runs after
`Upload build artifact`, so a failure there does not lose the MSIX.

Check the submission status in Partner Center or via the API:

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://manage.devcenter.microsoft.com/v1.0/my/applications/<APP_ID>/submissions/<SUB_ID>/status"
```

## Security notes

- **Never commit the client secret.** It is a repo secret, same as
  `ASC_API_KEY_P8_BASE64` and `MACOS_CERTIFICATE_PASSWORD`.
- The client secret has no OIDC binding — it works from anywhere, not just
  CI on `main`. Rotate it if it leaks.
- The CI step only runs on pushes to `main` (not pull requests), and only
  when the secret exists.
- Deleting a pending submission is destructive — if someone is editing a
  submission in Partner Center and a CI run fires, the CI run will delete
  that draft. This is the same behaviour as the MAS step (which creates a
  new version if one doesn't exist) and is documented by Microsoft as the
  expected API usage pattern. Don't edit a submission in Partner Center
  while CI might run.
