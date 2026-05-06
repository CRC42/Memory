#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Memory MCP one-shot bootstrap (P1-1 / v0.6.0 OOTB hardening).

.DESCRIPTION
    Single deploy entry. Idempotent. Performs:
        1. venv create + pip install
        2. prompt (or accept -UserName) for stable user id
        3. write memory-mcp.userName into .vscode/settings.json
        4. merge memory-mcp entry into .vscode/mcp.json
        5. final green-light health check (validate user + load config)

    All file mutations go through Python helpers in
    servers.memory_server.memory_bootstrap so the risky JSON merges are
    covered by pytest.

.PARAMETER UserName
    Stable user id for multi-user scoping. If omitted and the current
    .vscode/settings.json already has memory-mcp.userName, that value is
    kept; otherwise the script prompts.

.PARAMETER PythonExe
    Bootstrapping interpreter (only used to create the venv).

.EXAMPLE
    pwsh ./bootstrap.ps1
    pwsh ./bootstrap.ps1 -UserName alice
#>

param(
    [string]$UserName,
    [string]$PythonExe = "python",
    [string]$RepoRoot
)

$ErrorActionPreference = "Stop"

$mcpRoot   = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot "_Resolve-MemoryRoots.ps1")
$roots     = Resolve-MemoryRoots -MemoryRoot $mcpRoot -RepoRoot $RepoRoot
$repoRoot  = $roots.RepoRoot
$venvDir   = Join-Path $mcpRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

Write-Host "[bootstrap] repo root : $repoRoot"
Write-Host "[bootstrap] mcp  root : $mcpRoot"
if ($roots.MemoryRelToRepo) {
    Write-Host "[bootstrap] mcp  rel  : $($roots.MemoryRelToRepo)"
}

# ── Step 1: venv + deps ────────────────────────────────────────────────
if (-not (Test-Path $venvPython)) {
    Write-Host "[bootstrap] creating venv ..."
    & $PythonExe -m venv $venvDir
}
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $mcpRoot "requirements.txt") --quiet
Write-Host "[bootstrap] dependencies OK"

$env:PYTHONPATH = $mcpRoot

# ── Step 2: resolve user id ────────────────────────────────────────────
$settingsPath = Join-Path $repoRoot ".vscode\settings.json"
if (-not $UserName) {
    if (Test-Path $settingsPath) {
        try {
            $existing = (Get-Content $settingsPath -Raw | ConvertFrom-Json)
            $existingUser = $existing.'memory-mcp.userName'
            if ($existingUser) {
                $UserName = $existingUser
                Write-Host "[bootstrap] reusing existing memory-mcp.userName='$UserName'"
            }
        } catch {}
    }
}
if (-not $UserName) {
    $UserName = Read-Host "[bootstrap] Enter your stable user id (e.g. alice)"
}
if (-not $UserName) {
    throw "[bootstrap] user id is required for multi-user safety"
}

# ── Step 3-5: hand off to Python helpers ───────────────────────────────
$bootstrapScript = @"
import json, sys
from pathlib import Path

sys.path.insert(0, r'$mcpRoot')
from servers.memory_server.memory_bootstrap import (
    write_user_setting, merge_mcp_json, health_green_light,
)

repo_root = Path(r'$repoRoot')

r1 = write_user_setting(repo_root, '$UserName')
print('write_user_setting:', json.dumps(r1, ensure_ascii=False))

r2 = merge_mcp_json(
    repo_root,
    server_name='project-memory-mcp',
    python_exe=r'$venvPython',
    memory_root=r'$mcpRoot',
)
print('merge_mcp_json   :', json.dumps(r2, ensure_ascii=False))

r3 = health_green_light(repo_root)
print('health_green_light:', json.dumps(r3, ensure_ascii=False))

sys.exit(0 if r1.get('ok') and r2.get('ok') and r3.get('ok') else 1)
"@

$tmpScript = [System.IO.Path]::GetTempFileName() + ".py"
Set-Content -Path $tmpScript -Value $bootstrapScript -Encoding UTF8
try {
    & $venvPython $tmpScript
    if ($LASTEXITCODE -ne 0) {
        throw "[bootstrap] green-light health check failed; see output above"
    }
} finally {
    Remove-Item $tmpScript -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "[bootstrap] DONE ✔"
Write-Host "  user        : $UserName"
Write-Host "  venv python : $venvPython"
Write-Host "  settings.json updated, mcp.json updated, health green."
