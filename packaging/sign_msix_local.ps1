<#
.SYNOPSIS
    Self-signs dist\EasyPostDesktop.msix for local test-install only.

.DESCRIPTION
    Generates a throwaway self-signed certificate whose Subject matches the
    package's reserved Publisher identity, trusts it locally (Local Machine
    Trusted People - requires an elevated/admin PowerShell session), and
    signs the package with SignTool.

    RUN THIS IN POWERSHELL 7 (pwsh), elevated.

    The certificate is built entirely in managed .NET code via
    System.Security.Cryptography.X509Certificates.CertificateRequest, NOT
    via New-SelfSignedCertificate or certreq.exe. On some machines, both of
    those crash with an AccessViolationException (0xC0000005) inside
    Windows' certificate-enrollment engine (certenroll.dll) - reproducible
    under both Windows PowerShell 5.1 and PowerShell 7, so it is not a
    PowerShell-version issue. The likely cause is a third-party crypto
    provider registered on the machine (e.g. Certum SimplySign) corrupting
    that native enrollment path. CertificateRequest builds and signs the
    certificate in-process and never touches certenroll.dll, sidestepping
    the crash entirely.

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

$pfxPath = Join-Path $env:TEMP "easypostdesktop_local_signing.pfx"
$plainPassword = [System.Guid]::NewGuid().ToString("N")

Write-Host "Creating self-signed test certificate ($subject) in managed .NET code..."
$rsa = [System.Security.Cryptography.RSA]::Create(2048)
try {
    $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
        $subject,
        $rsa,
        [System.Security.Cryptography.HashAlgorithmName]::SHA256,
        [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)

    # Basic Constraints: end-entity, not a CA.
    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509BasicConstraintsExtension]::new($false, $false, 0, $true))

    # Key Usage: DigitalSignature.
    $req.CertificateExtensions.Add(
        [System.Security.Cryptography.X509Certificates.X509KeyUsageExtension]::new(
            [System.Security.Cryptography.X509Certificates.X509KeyUsageFlags]::DigitalSignature, $false))

    # Enhanced Key Usage: Code Signing (1.3.6.1.5.5.7.3.3).
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
    # Write the PFX (cert + private key) for signtool.
    $pfxBytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Pfx, $plainPassword)
    [System.IO.File]::WriteAllBytes($pfxPath, $pfxBytes)

    # Trust the public cert in LocalMachine\TrustedPeople so Add-AppxPackage
    # accepts the signature (requires admin).
    Write-Host "Trusting the certificate locally (requires admin - Cert:\LocalMachine\TrustedPeople)..."
    $publicCert = [System.Security.Cryptography.X509Certificates.X509Certificate2]::new(
        $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
    $store = [System.Security.Cryptography.X509Certificates.X509Store]::new("TrustedPeople", "LocalMachine")
    $store.Open("ReadWrite")
    try {
        # Drop stale certs from prior attempts with the same subject.
        foreach ($existing in @($store.Certificates)) {
            if ($existing.Subject -eq $subject) { $store.Remove($existing) }
        }
        $store.Add($publicCert)
    }
    finally {
        $store.Close()
    }

    $signtool = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe" |
        Sort-Object FullName -Descending | Select-Object -First 1
    if (-not $signtool) {
        throw "signtool.exe not found under C:\Program Files (x86)\Windows Kits\10\bin\*\x64. Install the Windows 10/11 SDK."
    }

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
}

Write-Host ""
Write-Host "Signed. To install locally:"
Write-Host "    Add-AppxPackage -Path `"$MsixPath`""
Write-Host "To remove the test install afterward:"
Write-Host "    Get-AppxPackage SFields.Easy-PostDesktop | Remove-AppxPackage"
Write-Host "To remove the trusted test certificate from this machine afterward:"
Write-Host "    Get-ChildItem Cert:\LocalMachine\TrustedPeople | Where-Object Subject -eq `"$subject`" | Remove-Item"
