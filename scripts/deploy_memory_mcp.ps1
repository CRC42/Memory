param(
    [string]$PythonExe = "python",
    [switch]$InstallDev
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$mcpRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvDir = Join-Path $mcpRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

Push-Location $repoRoot
try {
    if (!(Test-Path $venvPython)) {
        & $PythonExe -m venv $venvDir
    }

    & $venvPython -m pip install --upgrade pip
    & $venvPython -m pip install -r (Join-Path $mcpRoot "requirements.txt")

    if ($InstallDev) {
        & $venvPython -m pip install -r (Join-Path $mcpRoot "requirements-dev.txt")
    }

    $env:PYTHONPATH = $mcpRoot
    & $venvPython -m servers.memory_server --help | Out-Null
    Write-Host "Memory MCP deploy completed."
    Write-Host "Python: $venvPython"
    Write-Host "PYTHONPATH should be set to: $mcpRoot"
}
finally {
    Pop-Location
}
