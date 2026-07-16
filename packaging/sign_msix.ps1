<#
.SYNOPSIS
    Signs dist\EasyPostDesktop.msix for Microsoft Store UPLOAD (no admin needed).

.DESCRIPTION
    Sign-only flow: generates a throwaway self-signed certificate whose Subject
    matches the package's reserved Publisher identity and signs the package with
    SignTool. Unlike sign_msix_local.ps1, it does NOT install the certificate
    into LocalMachine\TrustedPeople, so it does not require an elevated session.
    This is all that is needed to upload to Partner Center: the Store strips the
    signature and re-signs with a Microsoft certificate on publish, so a
    self-signed package is accepted as-is.

    The certificate is built entirely in managed .NET via
    System.Security.Cryptography.X509Certificates.CertificateRequest (NOT
    New-SelfSignedCertificate / certreq.exe), which on this machine crash with
    0xC0000005 inside certenroll.dll. CertificateRequest never touches that DLL.

    Run in PowerShell 7 (pwsh); no admin required.

.EXAMPLE
    pwsh -File packaging\sign_msix.ps1
#>

param(
    [string]$MsixPath = (Join-Path $PSScriptRoot "..\dist\EasyPostDesktop.msix")
)

$ErrorActionPreference = "Stop"

$subject = "CN=A7D4B6C0-27D4-4F66-82EB-82F5DD466788"

if (-not (Test-Path $MsixPath)) {
    throw "$MsixPath not found. Run packaging\build_msix.py first."
}

$pfxPath = Join-Path $env:TEMP "easypostdesktop_upload_signing.pfx"
$plainPassword = [System.Guid]::NewGuid().ToString("N")

Write-Host "Creating self-signed certificate ($subject) in managed .NET code..."
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        $subject,
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)

    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))

    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
            [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $false))

    $eku = [System.Security.Cryptography.OidCollection]::new()
    [void]$eku.Add([System.Security.Cryptography.Oid]::new("1.3.6.1.5.5.7.3.3"))
    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($eku, $false))

    $notBefore = [System.DateTimeOffset]::UtcNow.AddDays(-1)
    $notAfter = [System.DateTimeOffset]::UtcNow.AddYears(1)
    $cert = $req.CreateSelfSigned($notBefore, $notAfter)
}
finally {
    $rsa.Dispose()
}

try {
    $pfxBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $plainPassword)
    [System.IO.File]::WriteAllBytes($pfxPath, $pfxBytes)

    $signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) {
        throw "signtool.exe not found under C:\Program Files (x86)\Windows Kits\10\bin\*\x64. Install the Windows 10/11 SDK."
    }

    Write-Host "Signing $MsixPath..."
    # /f + /p specify the exact cert; no /a (which would enumerate every
    # registered provider and can pop an unrelated login prompt).
    & $signtool.FullName sign /fd SHA256 /f $pfxPath /p $plainPassword $MsixPath
    if ($LASTEXITCODE -ne 0) {
        throw "signtool exited with code $LASTEXITCODE"
    }
}
finally {
    if (Test-Path $pfxPath) { Remove-Item $pfxPath -Force }
}

Write-Host ""
Write-Host "Signed for upload. Verify with:"
Write-Host "    (Get-AuthenticodeSignature `"$MsixPath`").SignerCertificate.Subject"
