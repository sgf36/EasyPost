# Automating the Mac App Store build in CI

Once the five repo secrets below exist, **every push to `main` builds, signs and
uploads a fresh Mac App Store package to App Store Connect automatically** — no
Mac session, no manual `codesign`/`altool`. The build number auto-increments
from the CI run number, and the marketing version tracks `app/config.APP_VERSION`.

The workflow step is already wired (`.github/workflows/build.yml` → "Build &
upload Mac App Store package (.pkg)"). It stays a **no-op** until
`MAS_CERTIFICATE_P12_BASE64` is set, so nothing changes until you finish this.

All commands assume you have `gh` authenticated (you do). Run them once.
**Windows PowerShell** commands are given (there is no `base64` command in
PowerShell — use `[Convert]::ToBase64String` as below).

## 1–3. The three you can set from Windows right now

These are not private keys — just the identity strings and the provisioning
profile (already in your OneDrive `Claude MacOS/signing/` folder).

```powershell
gh secret set MAS_SIGN_APP_IDENTITY -R sgf36/EasyPost --body "Apple Distribution: Spencer Fields (7WA4F8P743)"

gh secret set MAS_SIGN_INSTALLER_IDENTITY -R sgf36/EasyPost --body "3rd Party Mac Developer Installer: Spencer Fields (7WA4F8P743)"

$prof = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\SpencerFields\OneDrive - Spencer Fields\Apps\Claude MacOS\signing\EasyPost_Desktop.provisionprofile"))
gh secret set MAS_PROVISION_PROFILE_BASE64 -R sgf36/EasyPost --body $prof
```

## 4–5. The `.p12` — the one step that needs a Mac (once)

The signing **private keys** are not in the `signing/` folder (it holds only the
public `.cer` files). You export them into a single `.p12` from the Keychain of
the Mac where the certificates were created (the cloud Mac used for build 3, or
any Mac you re-issue them on):

1. Open **Keychain Access** → *login* keychain → *My Certificates*.
2. Select **both** certificates together (⌘-click):
   - `Apple Distribution: Spencer Fields (7WA4F8P743)`
   - `3rd Party Mac Developer Installer: Spencer Fields (7WA4F8P743)`

   Each must show a disclosure triangle with a private key under it. If a private
   key is missing, that cert must be re-created (Xcode → Settings → Accounts →
   Manage Certificates → +), which also refreshes the profile.
3. Right-click → **Export 2 items…** → save as `mas-certs.p12`, set a password.
4. Move `mas-certs.p12` to your Windows machine, then in **PowerShell** (edit the
   path and the real password):
   ```powershell
   $p12 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\Users\SpencerFields\Downloads\mas-certs.p12"))
   gh secret set MAS_CERTIFICATE_P12_BASE64 -R sgf36/EasyPost --body $p12

   gh secret set MAS_CERTIFICATE_PASSWORD -R sgf36/EasyPost --body 'YOUR-REAL-P12-PASSWORD'
   ```
   (Or on the Mac, `base64 -i mas-certs.p12 | gh secret set MAS_CERTIFICATE_P12_BASE64 -R sgf36/EasyPost` — `base64` exists there.)
5. **Delete `mas-certs.p12`** afterwards — GitHub has it now.

## After that

- Push anything to `main` (or re-run the Build workflow) → the macOS leg builds
  the MAS `.pkg` and uploads it to App Store Connect. It appears under your app's
  TestFlight/Builds a few minutes later, ready to attach to a version.
- Upload reuses the existing `APPLE_ID` + `APPLE_APP_PASSWORD` secrets — **no App
  Store Connect API key needed**, so you can still revoke the `.p8` in
  `signing/`.
- Submitting a version for review remains a manual App Store Connect step (yours,
  with your Apple ID) — CI only produces and uploads the build.

## Notes

- **Build number:** `CFBundleVersion` is set to the GitHub run number, which is
  well above the last manual build (3) and increases every run — so App Store
  Connect never rejects a duplicate. If it ever does, it means the run number
  dipped below the last uploaded build; the fix is a one-line offset in the
  workflow.
- **Security:** the `.p12` and its password are the only sensitive values; they
  live only as encrypted GitHub secrets and in a throwaway keychain created and
  deleted inside each CI run.
