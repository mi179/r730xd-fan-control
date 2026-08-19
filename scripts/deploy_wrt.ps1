[CmdletBinding()]
param(
    [string]$WrtHost = "192.168.5.2",
    [string]$WrtUser = "root",
    [switch]$Reconfigure,
    [switch]$RotateSessionKey,
    [switch]$NonInteractive,
    # Stage and pack only, touch no router. Lets the packaging half be checked
    # without a live WRT.
    [switch]$StageOnly
)

# Developer upgrade path for the maintainer's own router. It ships the webapp
# SOURCE (a couple of hundred KB), builds the image on the router, and installs
# straight from that image.
#
# The released offline bundle (build_openwrt_bundle.ps1 + Install-R730xdFan-Web.ps1)
# is unchanged and is still the only supported path for other people: it carries a
# prebuilt image and a SHA256 manifest because it travels over the internet. This
# script deliberately does neither - the source is already trusted here, and the
# 23 MB image used to cross the LAN twice for no reason (built on the router,
# saved, copied to Windows, bundled, copied back).

$ErrorActionPreference = "Stop"

if ($WrtHost -notmatch '^[A-Za-z0-9.-]+$') { throw "Invalid WRT host: $WrtHost" }
if ($WrtUser -notmatch '^[A-Za-z0-9_-]+$') { throw "Invalid WRT user: $WrtUser" }

$repoRoot = Split-Path -Parent $PSScriptRoot
$webappRoot = Join-Path $repoRoot "webapp"
$installerRoot = Join-Path $webappRoot "installer"

$version = (Get-Content -Raw -LiteralPath (Join-Path $installerRoot "VERSION")).Trim()
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid installer VERSION: $version" }

# Same single-source-of-truth assertion the bundle script makes (D-013): the
# payload copies must agree with VERSION, or the install would tag one version
# and verify another.
$consistencyChecks = @(
    @{ File = "install.sh"; Pattern = "(?m)^APP_VERSION=`"$version`"$" },
    @{ File = "verify.sh"; Pattern = "(?m)^EXPECTED_IMAGE=`"r730xd-fan-web:$version`"$" },
    @{ File = "compose.offline.yaml"; Pattern = "(?m)^\s+image: r730xd-fan-web:$version\s*$" },
    @{ File = "README.txt"; Pattern = "(?m)^R730xd Fan Web $version " }
)
foreach ($check in $consistencyChecks) {
    $payload = Get-Content -Raw -LiteralPath (Join-Path $installerRoot $check.File)
    if ($payload -notmatch $check.Pattern) {
        throw "Version drift: $($check.File) does not carry VERSION $version; align it before deploying."
    }
}

$image = "r730xd-fan-web:$version"
$target = "$WrtUser@$WrtHost"
$remoteRoot = "/tmp/r730xd-fan-src-$PID"

$installerFlags = @("--use-local-image")
if ($Reconfigure) { $installerFlags += "--reconfigure" }
if ($RotateSessionKey) { $installerFlags += "--rotate-session-key" }
if ($NonInteractive) { $installerFlags += "--non-interactive" }
$installerArgumentText = $installerFlags -join " "

# Stage a directory shaped like a bundle minus images/: install.sh reads its
# siblings (.env.example, compose.offline.yaml, verify.sh, rollback.sh) from
# whatever directory it is run out of.
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stageParent = [IO.Path]::GetFullPath((Join-Path $tempRoot ("r730xd-src-" + [guid]::NewGuid().ToString("N"))))
if (-not $stageParent.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to stage outside the temporary directory."
}
$packageName = "r730xd-fan-src"
$stageRoot = Join-Path $stageParent $packageName

try {
    # Mirror the repo layout: the Dockerfile addresses webapp/... and
    # r730xd_core/... relative to the build context.
    [IO.Directory]::CreateDirectory((Join-Path $stageRoot "build\webapp")) | Out-Null

    foreach ($name in @("install.sh", "verify.sh", "rollback.sh", "compose.offline.yaml", "VERSION", "README.txt")) {
        Copy-Item -LiteralPath (Join-Path $installerRoot $name) -Destination (Join-Path $stageRoot $name)
    }
    Copy-Item -LiteralPath (Join-Path $webappRoot ".env.example") -Destination (Join-Path $stageRoot ".env.example")

    # Docker build context: exactly what the Dockerfile COPYs, nothing else.
    # Never the real secrets/ or .env - they are not needed and must not travel.
    foreach ($name in @("Dockerfile", "requirements.txt", "app.py")) {
        Copy-Item -LiteralPath (Join-Path $webappRoot $name) -Destination (Join-Path $stageRoot "build\webapp\$name")
    }
    foreach ($name in @("templates", "static")) {
        Copy-Item -LiteralPath (Join-Path $webappRoot $name) -Destination (Join-Path $stageRoot "build\webapp\$name") -Recurse
    }
    Copy-Item -LiteralPath (Join-Path $repoRoot ".dockerignore") -Destination (Join-Path $stageRoot "build\.dockerignore")
    # r730xd_core lives at the repo root, outside webapp/, but the image needs
    # it (D-027). Without this the build fails on `import r730xd_core`.
    Copy-Item -LiteralPath (Join-Path $repoRoot "r730xd_core") -Destination (Join-Path $stageRoot "build\r730xd_core") -Recurse

    # Compiled bytecode is host-specific and would only be dead weight in the
    # upload; .dockerignore drops it at build time anyway.
    Get-ChildItem -LiteralPath $stageRoot -Recurse -Directory -Filter "__pycache__" |
        Remove-Item -Recurse -Force

    # Run tar with the staging directory as the working directory and relative
    # paths only: GNU tar reads "D:\..." as a host:path remote spec, so an
    # absolute Windows path breaks under MSYS/Git-Bash tar while working fine
    # under the bsdtar shipped in System32.
    $archiveName = "$packageName.tar.gz"
    Push-Location -LiteralPath $stageParent
    try {
        & tar.exe -czf $archiveName $packageName
        if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE" }
        $entries = @()
        if ($StageOnly) { $entries = & tar.exe -tzf $archiveName }
    } finally {
        Pop-Location
    }
    $archivePath = Join-Path $stageParent $archiveName
    $sizeKb = [math]::Round((Get-Item -LiteralPath $archivePath).Length / 1KB, 1)

    Write-Host "[R730XD] Source package: $sizeKb KB (no image inside)"

    if ($StageOnly) {
        $entries | Sort-Object | ForEach-Object { Write-Host "  $_" }
        Write-Host "[R730XD] -StageOnly: nothing was sent to a router."
        return
    }

    & ssh $target "mkdir -p '$remoteRoot'"
    if ($LASTEXITCODE -ne 0) { throw "Unable to create the remote temporary directory." }

    try {
        Write-Host "[R730XD] Uploading source to $target"
        & scp -O $archivePath "${target}:$remoteRoot/$packageName.tar.gz"
        if ($LASTEXITCODE -ne 0) { throw "Source upload failed." }

        Write-Host "[R730XD] Building $image on the router"
        & ssh $target "tar -xzf '$remoteRoot/$packageName.tar.gz' -C '$remoteRoot' && docker build -t '$image' -f '$remoteRoot/$packageName/build/webapp/Dockerfile' '$remoteRoot/$packageName/build'"
        if ($LASTEXITCODE -ne 0) { throw "Remote image build failed. Nothing was installed." }

        Write-Host "[R730XD] Installing (the installer verifies and rolls back on failure)"
        & ssh -t $target "sh '$remoteRoot/$packageName/install.sh' $installerArgumentText"
        if ($LASTEXITCODE -ne 0) { throw "Remote installation failed. The installer attempted an automatic rollback." }
    } finally {
        # /tmp is tmpfs on OpenWrt: leftovers cost RAM (E-035).
        & ssh $target "rm -rf '$remoteRoot'" 2>&1 | Out-Null
    }

    Write-Host ""
    Write-Host "[R730XD] Deployed $image. No fan-control command was sent."
} finally {
    if ([IO.Directory]::Exists($stageParent)) {
        $resolvedStage = [IO.Path]::GetFullPath($stageParent)
        if ($resolvedStage.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}
