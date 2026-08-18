[CmdletBinding()]
param(
    [string]$WrtHost = "192.168.5.2",
    [string]$WrtUser = "root",
    [string]$BundlePath = "",
    [switch]$NonInteractive,
    [switch]$RotateSessionKey
)

$ErrorActionPreference = "Stop"

if ($WrtHost -notmatch '^[A-Za-z0-9.-]+$') {
    throw "Invalid WRT host: $WrtHost"
}
if ($WrtUser -notmatch '^[A-Za-z0-9_-]+$') {
    throw "Invalid WRT user: $WrtUser"
}

if ([string]::IsNullOrWhiteSpace($BundlePath)) {
    $candidates = @(Get-ChildItem -LiteralPath $PSScriptRoot -Filter 'R730xdFan-Web-Docker-v*.tar.gz' -File)
    if ($candidates.Count -ne 1) {
        throw "Place exactly one R730xdFan-Web-Docker-v*.tar.gz beside this script, or pass -BundlePath."
    }
    $bundle = $candidates[0]
} else {
    $bundle = Get-Item -LiteralPath (Resolve-Path -LiteralPath $BundlePath)
}

$packageDirectory = $bundle.Name -replace '\.tar\.gz$', ''
if ($packageDirectory -notmatch '^R730xdFan-Web-Docker-v\d+\.\d+\.\d+$') {
    throw "Unexpected bundle filename: $($bundle.Name)"
}
$checksumPath = "$($bundle.FullName).sha256"
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Missing checksum file: $checksumPath"
}
$expectedHash = ((Get-Content -Raw -LiteralPath $checksumPath).Trim() -split '\s+')[0]
if ($expectedHash -notmatch '^[0-9A-Fa-f]{64}$') {
    throw "Invalid checksum file: $checksumPath"
}
$actualHash = (Get-FileHash -LiteralPath $bundle.FullName -Algorithm SHA256).Hash
if ($actualHash -ine $expectedHash) {
    throw "Bundle SHA256 verification failed."
}

$remoteRoot = "/tmp/r730xd-fan-installer-$PID"
$remoteArchive = "$remoteRoot/$($bundle.Name)"
$target = "$WrtUser@$WrtHost"
$installerFlags = @()
if ($NonInteractive) { $installerFlags += "--non-interactive" }
if ($RotateSessionKey) { $installerFlags += "--rotate-session-key" }
$installerArgumentText = $installerFlags -join " "

Write-Host "[R730XD] Creating temporary directory on $target"
& ssh $target "mkdir -p '$remoteRoot'"
if ($LASTEXITCODE -ne 0) { throw "Unable to create the remote temporary directory." }

Write-Host "[R730XD] Uploading $($bundle.Name)"
& scp -O $bundle.FullName "${target}:$remoteArchive"
if ($LASTEXITCODE -ne 0) { throw "Bundle upload failed." }

Write-Host "[R730XD] Archive SHA256 verified; starting the remote installer"
& ssh -t $target "tar -xzf '$remoteArchive' -C '$remoteRoot' && sh '$remoteRoot/$packageDirectory/install.sh' $installerArgumentText"
if ($LASTEXITCODE -ne 0) { throw "Remote installation failed. The installer attempted an automatic rollback." }

# /tmp on OpenWrt is tmpfs, so a leftover bundle costs RAM, not disk. Eight of
# them had accumulated to 205 MB before this cleanup existed (E-035).
Write-Host "[R730XD] Removing the uploaded package from the router tmpfs"
& ssh $target "rm -rf '$remoteRoot'"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Could not remove $remoteRoot on $target; it will clear on reboot."
}

Write-Host ""
Write-Host "[R730XD] Installation finished. Use the Web URL printed by the remote installer above."
Write-Host "[R730XD] Post-install verification ran on the router as part of the installer."
