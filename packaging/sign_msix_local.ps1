<#
.SYNOPSIS
    Self-signs dist\EasyPostDesktop.msix for local test-install only.

.DESCRIPTION
    Generates a throwaway self-signed certificate whose Subject matches the
    package's reserved Publisher identity, trusts it locally (Local Machine
    Trusted People — requires an elevated/admin PowerShell session), and
    signs the package with SignTool.

    This certificate is for LOCAL TESTING ONLY. It is not needed for the
    actual Microsoft Store submission: the Store strips whatever signature
    is present and re-signs with a Microsoft certificate during publishing,
    so a self-signed package is accepted for upload as-is.
    See: https://learn.microsoft.com/windows/apps/publish/publish-your-app/msix/app-package-requirements

.EXAMPLE
    .\packaging\sign_msix_local.ps1
#>

param(
    [string]$MsixPath = (Join-Path $PSScriptRoot "..\dist\EasyPostDesktop.msix")
)

$ErrorActionPreference = "Stop"

$subject = "CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788"

if (-not (Test-Path $MsixPath)) {
    throw "$MsixPath not found. Run packaging\build_msix.py first."
}

Write-Host "Creating self-signed test certificate ($subject)..."
$cert = New-SelfSignedCertificate -Type Custom -KeyUsage DigitalSignature `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}") `
    -Subject $subject -FriendlyName "EasyPostDesktop local test signing"

$pfxPath = Join-Path $env:TEMP "easypostdesktop_local_signing.pfx"
$pfxPassword = ConvertTo-SecureString -String ([System.Guid]::NewGuid().ToString("N")) -Force -AsPlainText

try {
    Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $pfxPassword | Out-Null

    Write-Host "Trusting the certificate locally (requires admin — Cert:\LocalMachine\TrustedPeople)..."
    Import-PfxCertificate -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" -Password $pfxPassword -FilePath $pfxPath | Out-Null

    $signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) {
        throw "signtool.exe not found under C:\Program Files (x86)\Windows Kits\10\bin\*\x64. Install the Windows 10/11 SDK."
    }

    $plainPassword = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
        [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($pfxPassword))

    Write-Host "Signing $MsixPath..."
    # No /a: it makes signtool enumerate every registered certificate
    # provider (including third-party ones like Certum SimplySign, if
    # installed), which can pop an unrelated login prompt. /f + /p already
    # specify the exact certificate, so /a adds nothing here.
    & $signtool.FullName sign /fd SHA256 /f $pfxPath /p $plainPassword $MsixPath
    if ($LASTEXITCODE -ne 0) {
        throw "signtool exited with code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
    Remove-Item "Cert:\CurrentUser\My\$($cert.Thumbprint)" -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "Signed. To install locally:"
Write-Host "    Add-AppxPackage -Path `"$MsixPath`""
Write-Host "To remove the test install afterward:"
Write-Host "    Get-AppxPackage SFields.Easy-PostDesktop | Remove-AppxPackage"
Write-Host "To remove the trusted test certificate from this machine afterward:"
Write-Host "    Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object Subject -eq `"$subject`" | Remove-Item"
