param(
    [string]$Python = "",
    [switch]$InstallBuildRequirements
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$backendResourceDir = Join-Path $repoRoot "azul_desktop\resources\backend"
$pyinstallerWorkDir = Join-Path $repoRoot "build\pyinstaller"
$backendEntry = Join-Path $repoRoot "scripts\packaging\azul_backend_entry.py"
$handsEntry = Join-Path $repoRoot "scripts\packaging\azul_hands_mcp_entry.py"

if (-not $Python) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $Python = $venvPython
    } else {
        $Python = "python"
    }
}

if ($InstallBuildRequirements) {
    & $Python -m pip install -r (Join-Path $repoRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install build requirements."
    }
}

& $Python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not available. Re-run with -InstallBuildRequirements."
}

New-Item -ItemType Directory -Force -Path $backendResourceDir | Out-Null
New-Item -ItemType Directory -Force -Path $pyinstallerWorkDir | Out-Null

$generatedDirs = @(
    (Join-Path $backendResourceDir "azul-backend"),
    (Join-Path $backendResourceDir "azul-hands-mcp")
)

foreach ($dir in $generatedDirs) {
    $resolved = [System.IO.Path]::GetFullPath($dir)
    $allowedRoot = [System.IO.Path]::GetFullPath($backendResourceDir)
    if (-not $resolved.StartsWith($allowedRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete unexpected path: $resolved"
    }
    if (Test-Path -LiteralPath $resolved) {
        Remove-Item -Recurse -Force -LiteralPath $resolved
    }
}

$commonArgs = @(
    "--noconfirm",
    "--clean",
    "--onedir",
    "--distpath", $backendResourceDir,
    "--workpath", $pyinstallerWorkDir,
    "--specpath", $pyinstallerWorkDir,
    "--hidden-import", "mcp",
    "--hidden-import", "mcp.client.stdio",
    "--hidden-import", "mcp.server.stdio"
)

$backendArgs = @(
    "--collect-all", "sqlite_vec",
    "--collect-all", "agent_framework",
    "--hidden-import", "azure.servicebus"
)

Push-Location $repoRoot
try {
    & $Python -m PyInstaller @commonArgs --name "azul-hands-mcp" $handsEntry
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to package azul-hands-mcp."
    }
    & $Python -m PyInstaller @commonArgs @backendArgs --name "azul-backend" $backendEntry
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to package azul-backend."
    }
} finally {
    Pop-Location
}

Write-Host "Packaged backend resources in $backendResourceDir"
