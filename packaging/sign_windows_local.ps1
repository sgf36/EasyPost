<#
.SYNOPSIS
    Code-sign the Windows direct-download build with a Certum (or any) code-signing
    certificate, locally, then repackage it and print the new checksum.

.DESCRIPTION
    Certum's Open Source Code Signing certificate — like every standard/OV
    code-signing certificate issued since June 2023 — keeps its private key on
    hardware you cannot export: Certum's SimplySign cloud HSM, or a physical
    cryptographic card. That rules out the .pfx-in-a-secret approach the CI
    workflow was scaffolded for, and it rules out GitHub-hosted runners, which
    cannot reach the key. So the Windows build is signed here, on your machine,
    where the key lives.

    Both SimplySign and the card expose the certificate through the Windows
    certificate store once their middleware is running (SimplySign Desktop, or
    proCertum CardManager). This script is therefore method-agnostic: it signs
    with a certificate already present in the store, whichever way it got there.

    Before running:
      SimplySign  — open SimplySign Desktop and log in (you approve on your phone).
                    The cloud certificate appears in Cert:\CurrentUser\My.
      Card        — insert the card + reader and start its middleware.

.PARAMETER SourceDir
    Folder containing EasyPostDesktop.exe (the unzipped direct-download build).
    Default: dist-download\win\EasyPostDesktop next to the repo, if present.

.PARAMETER Thumbprint
    SHA1 thumbprint of the code-signing certificate to use. Omit to have the
    script list the code-signing certificates it can see; if there is exactly
    one it uses it, otherwise it asks you to pass -Thumbprint.

.PARAMETER Out
    Path of the signed .zip to produce.
    Default: dist-download\EasyPostDesktop-Windows-x64.zip

.EXAMPLE
    .\packaging\sign_windows_local.ps1
    .\packaging\sign_windows_local.ps1 -Thumbprint 1A2B3C... -SourceDir C:\tmp\EasyPostDesktop
#>
[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$Thumbprint,
    [string]$Out
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (-not $SourceDir) { $SourceDir = Join-Path $repo "dist-download\win\EasyPostDesktop" }
if (-not $Out)       { $Out       = Join-Path $repo "dist-download\EasyPostDesktop-Windows-x64.zip" }

if (-not (Test-Path (Join-Path $SourceDir "EasyPostDesktop.exe"))) {
    throw "EasyPostDesktop.exe not found under $SourceDir. Point -SourceDir at the unzipped Windows build."
}

# --- Locate signtool (newest Windows SDK) -------------------------------------
$signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $signtool) {
    throw "signtool.exe not found. Install the Windows 10/11 SDK (it ships the signing tools)."
}

# --- Pick the certificate -----------------------------------------------------
$codeSigningOid = "1.3.6.1.5.5.7.3.3"
$candidates = @(
    Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.EnhancedKeyUsageList.ObjectId -contains $codeSigningOid -or
                       ($_.Extensions | Where-Object { $_.EnhancedKeyUsages.ObjectId -contains $codeSigningOid }) }
)

if (-not $Thumbprint) {
    if ($candidates.Count -eq 0) {
        throw @"
No code-signing certificate is visible to Windows.
If you are using SimplySign, open SimplySign Desktop and log in first — the
cloud certificate only appears in the store while that app is connected.
If you are using a card, insert it and start proCertum CardManager.
"@
    }
    if ($candidates.Count -gt 1) {
        Write-Host "More than one code-signing certificate found. Re-run with -Thumbprint <one below>:`n"
        $candidates | ForEach-Object {
            Write-Host ("  {0}  {1}  (expires {2:yyyy-MM-dd})" -f $_.Thumbprint, $_.Subject, $_.NotAfter)
        }
        throw "Ambiguous certificate; -Thumbprint required."
    }
    $Thumbprint = $candidates[0].Thumbprint
    Write-Host ("Using the only code-signing certificate found: {0}" -f $candidates[0].Subject)
}

# --- Sign every executable in the build --------------------------------------
# The launched app is EasyPostDesktop.exe; easypost-mcp.exe ships alongside it in
# the direct-download variant. Sign both so neither trips SmartScreen.
$timestamp = "http://time.certum.pl"   # Certum's RFC3161 timestamp authority
$exes = Get-ChildItem $SourceDir -Recurse -Filter *.exe

foreach ($exe in $exes) {
    Write-Host "Signing $($exe.Name) ..."
    & $signtool.FullName sign /sha1 $Thumbprint /fd SHA256 `
        /tr $timestamp /td SHA256 /v $exe.FullName
    if ($LASTEXITCODE -ne 0) { throw "signtool failed on $($exe.FullName)" }
}

# --- Verify -------------------------------------------------------------------
foreach ($exe in $exes) {
    & $signtool.FullName verify /pa /v $exe.FullName
    if ($LASTEXITCODE -ne 0) { throw "verification failed on $($exe.FullName)" }
}

# --- Repackage ----------------------------------------------------------------
if (Test-Path $Out) { Remove-Item $Out -Force }
$parent = Split-Path -Parent $Out
if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
Write-Host "Repackaging signed build to $Out ..."
Compress-Archive -Path $SourceDir -DestinationPath $Out -CompressionLevel Optimal -Force

# --- New checksum -------------------------------------------------------------
$hash = (Get-FileHash $Out -Algorithm SHA256).Hash.ToLower()
$sizeMb = [math]::Round((Get-Item $Out).Length / 1MB)

Write-Host ""
Write-Host "======================================================================"
Write-Host " Signed build ready:  $Out  ($sizeMb MB)"
Write-Host " SHA-256:  $hash"
Write-Host "======================================================================"
Write-Host ""
Write-Host "Next, because the zip's checksum has changed:"
Write-Host "  1. Replace the Windows asset on the v1.0.4 GitHub release with this file."
Write-Host "  2. Update the SHA-256 for the Windows zip in site\download.html and in"
Write-Host "     the release notes."
Write-Host "  3. Re-run 'signtool verify /pa' on a machine WITHOUT your cert to be"
Write-Host "     sure the timestamp and chain validate for a stranger."
