<#
.SYNOPSIS
    Run the Windows App Certification Kit (WACK) against a Store MSIX and report
    the result. This is the local pre-submission gate that would have caught the
    1.0.3.0 install failure (cert 10.3.4) before it ever reached Microsoft.

.DESCRIPTION
    WACK runs Microsoft's own certification tests locally — including the
    deployment/install validation the Store performs — so a failure shows up here
    as a report line instead of a rejected submission days later.

    appcert.exe requires administrator rights and has to deploy the package to
    test it. A Store MSIX is signed with a self-signed certificate (the Store
    re-signs on publish), which Windows will not deploy unless that certificate
    is trusted first. This script trusts the package's own signer certificate,
    runs the kit, writes the XML report next to the package, and prints a plain
    PASS/FAIL summary.

    Run from an ELEVATED PowerShell:
        .\packaging\run_wack.ps1
        .\packaging\run_wack.ps1 -MsixPath C:\path\to\Some.msix
#>
[CmdletBinding()]
param(
    [string]$MsixPath
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if (-not $MsixPath) {
    $MsixPath = Join-Path $repo "dist\EasyPostDesktop.msix"
}
if (-not (Test-Path $MsixPath)) { throw "MSIX not found: $MsixPath" }
# appcert.exe rejects a relative -reportoutputpath ("must be valid path to the
# report file"), so always hand it an absolute path.
$MsixPath = (Resolve-Path -LiteralPath $MsixPath).Path

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Run this from an elevated PowerShell (Run as administrator) — appcert.exe needs admin." }

$appcert = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\App Certification Kit\appcert.exe" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $appcert) { throw "appcert.exe not found. Install the Windows App Certification Kit (part of the Windows SDK)." }

# appcert writes the report itself, and is unreliable when the path contains
# spaces (the repo lives under "OneDrive - Spencer Fields"). Generate into a
# space-free temp dir, then copy the result next to the package for convenience.
$workDir = Join-Path $env:TEMP "epd-wack"
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
$report  = Join-Path $workDir "EasyPostDesktop.WACK-report.xml"
$summary = [IO.Path]::ChangeExtension($MsixPath, $null) + "WACK-summary.txt"

# 1) Trust the package's signing certificate so Windows will deploy it for testing.
Write-Host "Trusting the package signer certificate ..."
$cert = (Get-AuthenticodeSignature $MsixPath).SignerCertificate
if ($null -eq $cert) { throw "The MSIX is not signed; WACK cannot deploy it." }
$cerPath = Join-Path $env:TEMP "epd-wack-signer.cer"
[IO.File]::WriteAllBytes($cerPath, $cert.Export("Cert"))
Import-Certificate -FilePath $cerPath -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null

# 2) Reset any prior WACK state, then run the certification tests.
Write-Host "Running the Windows App Certification Kit — this takes several minutes ..."
& $appcert.FullName reset | Out-Null
& $appcert.FullName test -appxpackagepath "$MsixPath" -reportoutputpath "$report"

# 3) Summarise. The report's OVERALL_RESULT attribute is the verdict; pull out
#    any failed tests so the summary is readable without opening the XML.
"WACK report for: $MsixPath" | Set-Content $summary
"Generated:       $report`n" | Add-Content $summary
try {
    [xml]$xml = Get-Content $report
    $overall = $xml.REPORT.OVERALL_RESULT
    "OVERALL RESULT: $overall`n" | Add-Content $summary
    $failed = $xml.SelectNodes("//*[@RESULT='FAIL']")
    if ($failed.Count -gt 0) {
        "Failed tests:" | Add-Content $summary
        foreach ($f in $failed) {
            ("  - {0}: {1}" -f $f.NAME, ($f.MESSAGE -join " ")) | Add-Content $summary
        }
    } else {
        "No failed tests." | Add-Content $summary
    }
} catch {
    "Could not parse the report XML: $($_.Exception.Message)" | Add-Content $summary
}

Write-Host ""
Get-Content $summary
Write-Host ""
Write-Host "Full report: $report"
Write-Host "Summary:     $summary"
