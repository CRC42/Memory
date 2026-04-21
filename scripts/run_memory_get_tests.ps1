$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
. (Join-Path $PSScriptRoot "Resolve-MemoryTestPython.ps1")
$venvPython = Resolve-MemoryTestPython -RepoRoot $repoRoot
$memoryRoot = (Resolve-Path (Join-Path $repoRoot "MCP/Memory")).Path
Push-Location $memoryRoot
try {
    & $venvPython -m pytest tests/memory_server/test_security_and_get.py -q
}
finally {
    Pop-Location
}
