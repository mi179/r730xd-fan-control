[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ImageArchivePath,
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$installerRoot = Join-Path $repoRoot "webapp\installer"
$version = (Get-Content -Raw -LiteralPath (Join-Path $installerRoot "VERSION")).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid installer VERSION: $version" }

$imageSource = (Resolve-Path -LiteralPath $ImageArchivePath).Path
$expectedImageName = "r730xd-fan-web-$version-linux-amd64.tar.gz"
if ([IO.Path]::GetFileName($imageSource) -ne $expectedImageName) {
    throw "Image archive must be named $expectedImageName"
}

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot "dist\docker"
}
[IO.Directory]::CreateDirectory([IO.Path]::GetFullPath($OutputDirectory)) | Out-Null
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)

$packageName = "R730xdFan-Web-Docker-v$version"
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stageParent = [IO.Path]::GetFullPath((Join-Path $tempRoot ("r730xd-bundle-" + [guid]::NewGuid().ToString("N"))))
if (-not $stageParent.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to stage outside the temporary directory."
}
$stageRoot = Join-Path $stageParent $packageName

try {
    [IO.Directory]::CreateDirectory((Join-Path $stageRoot "images")) | Out-Null

    $files = @(
        @("install.sh", "install.sh"),
        @("verify.sh", "verify.sh"),
        @("rollback.sh", "rollback.sh"),
        @("compose.offline.yaml", "compose.offline.yaml"),
        @("VERSION", "VERSION"),
        @("README.txt", "README.txt"),
        @("Install-R730xdFan-Web.ps1", "Install-R730xdFan-Web.ps1")
    )
    foreach ($mapping in $files) {
        Copy-Item -LiteralPath (Join-Path $installerRoot $mapping[0]) -Destination (Join-Path $stageRoot $mapping[1])
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot "webapp\.env.example") -Destination (Join-Path $stageRoot ".env.example")
    Copy-Item -LiteralPath $imageSource -Destination (Join-Path $stageRoot "images\$expectedImageName")

    $manifestFiles = Get-ChildItem -LiteralPath $stageRoot -Recurse -File |
        Sort-Object FullName
    $manifestLines = foreach ($file in $manifestFiles) {
        $relative = $file.FullName.Substring($stageRoot.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
    $manifestText = ($manifestLines -join "`n") + "`n"
    [IO.File]::WriteAllText((Join-Path $stageRoot "SHA256SUMS"), $manifestText, [Text.UTF8Encoding]::new($false))

    $archivePath = Join-Path $outputRoot "$packageName.tar.gz"
    if ([IO.File]::Exists($archivePath)) { [IO.File]::Delete($archivePath) }
    & tar.exe -czf $archivePath -C $stageParent $packageName
    if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE" }

    Copy-Item -LiteralPath (Join-Path $installerRoot "Install-R730xdFan-Web.ps1") -Destination (Join-Path $outputRoot "Install-R730xdFan-Web.ps1") -Force
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$archivePath.sha256", "$archiveHash  $([IO.Path]::GetFileName($archivePath))`n", [Text.UTF8Encoding]::new($false))

    Write-Host "Bundle: $archivePath"
    Write-Host "SHA256: $archiveHash"
} finally {
    if ([IO.Directory]::Exists($stageParent)) {
        $resolvedStage = [IO.Path]::GetFullPath($stageParent)
        if ($resolvedStage.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}
