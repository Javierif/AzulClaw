param(
    [switch]$SkipBackend,
    [switch]$SkipBuildRequirementsInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

if (-not $SkipBackend) {
    $installBuildRequirements = -not $SkipBuildRequirementsInstall
    & (Join-Path $repoRoot "scripts\package_backend.ps1") -InstallBuildRequirements:$installBuildRequirements
}

Push-Location (Join-Path $repoRoot "azul_desktop")
try {
    npm run tauri:build
} finally {
    Pop-Location
}
