$ErrorActionPreference = "Stop"

function Resolve-MemoryTestPython {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $candidates = @(
        (Join-Path $RepoRoot "MCP/memory/.venv/Scripts/python.exe"),
        (Join-Path $RepoRoot ".venv/Scripts/python.exe")
    )

    foreach ($candidate in $candidates) {
        if (!(Test-Path $candidate)) {
            continue
        }
        try {
            & $candidate -c "import pytest" *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    throw "No Python environment with pytest found. Run MCP/memory/scripts/deploy_memory_mcp.ps1 -InstallDev or install pytest in the repository .venv."
}
