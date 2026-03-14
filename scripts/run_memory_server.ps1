$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
$memoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$venvPython = Join-Path $memoryRoot ".venv\Scripts\python.exe"

if (!(Test-Path $venvPython)) {
    throw "Memory MCP venv not found. Run MCP/memory/scripts/deploy_memory_mcp.ps1 first."
}

Push-Location $repoRoot
try {
    $env:PYTHONPATH = $memoryRoot
    & $venvPython -m servers.memory_server --root $repoRoot
}
finally {
    Pop-Location
}
