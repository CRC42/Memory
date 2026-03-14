$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$venvPython = Join-Path $repoRoot "MCP/memory/.venv/Scripts/python.exe"
if (!(Test-Path $venvPython)) {
    throw "Memory MCP venv not found. Run MCP/memory/scripts/deploy_memory_mcp.ps1 -InstallDev first."
}
Push-Location $repoRoot
try {
    & $venvPython -m pytest MCP/memory/tests/memory_server/test_backup.py -q
}
finally {
    Pop-Location
}
