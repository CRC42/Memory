param(
    [string]$WorkspaceRoot
)

$ErrorActionPreference = "Stop"

function Convert-ToPosixPath([string]$PathValue) {
    return $PathValue -replace "\\", "/"
}

function Convert-ToHashtable($Value) {
    if ($null -eq $Value) {
        return $null
    }

    if ($Value -is [string] -or $Value -is [ValueType]) {
        return $Value
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $ht = @{}
        foreach ($key in $Value.Keys) {
            $ht[$key] = Convert-ToHashtable $Value[$key]
        }
        return $ht
    }

    if (($Value -is [System.Collections.IEnumerable]) -and !($Value -is [string])) {
        $arr = @()
        foreach ($item in $Value) {
            $arr += ,(Convert-ToHashtable $item)
        }
        return $arr
    }

    if ($Value.PSObject -and $Value.PSObject.Properties.Count -gt 0) {
        $ht = @{}
        foreach ($prop in $Value.PSObject.Properties) {
            $ht[$prop.Name] = Convert-ToHashtable $prop.Value
        }
        return $ht
    }

    return $Value
}

$memoryRoot = (Resolve-Path $PSScriptRoot).Path
if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = (Resolve-Path (Join-Path $memoryRoot "..\..")).Path
}
else {
    $WorkspaceRoot = (Resolve-Path $WorkspaceRoot).Path
}

$vscodeDir = Join-Path $WorkspaceRoot ".vscode"
$mcpJsonPath = Join-Path $vscodeDir "mcp.json"
$venvPython = Join-Path $memoryRoot ".venv\Scripts\python.exe"

if (!(Test-Path $venvPython)) {
    throw "Python venv not found: $venvPython. Run MCP/memory/deploy.ps1 first."
}

if (!(Test-Path $vscodeDir)) {
    New-Item -ItemType Directory -Path $vscodeDir -Force | Out-Null
}

$mcpConfig = @{}
if (Test-Path $mcpJsonPath) {
    $raw = Get-Content $mcpJsonPath -Raw -Encoding UTF8
    if (![string]::IsNullOrWhiteSpace($raw)) {
        $parsed = $raw | ConvertFrom-Json
        $mcpConfig = Convert-ToHashtable $parsed
    }
}

if ($null -eq $mcpConfig -or $mcpConfig.Count -eq 0) {
    $mcpConfig = @{}
}

if (!$mcpConfig.ContainsKey("servers") -or $null -eq $mcpConfig["servers"]) {
    $mcpConfig["servers"] = @{}
}

$servers = $mcpConfig["servers"]
$servers["project-memory-mcp"] = @{
    command = Convert-ToPosixPath $venvPython
    args = @(
        "-m",
        "servers.memory_server",
        "--root",
        (Convert-ToPosixPath $WorkspaceRoot)
    )
    env = @{
        PYTHONPATH = Convert-ToPosixPath $memoryRoot
        PYTHONUTF8 = "1"
    }
}

$compactJson = $mcpConfig | ConvertTo-Json -Depth 50 -Compress
$tempJsonPath = Join-Path $env:TEMP ("mcp_setup_" + [System.Guid]::NewGuid().ToString("N") + ".json")
[System.IO.File]::WriteAllText($tempJsonPath, $compactJson, [System.Text.UTF8Encoding]::new($false))

$prevEAP = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
$fmtOutput = & $venvPython -c "import json, pathlib, sys; src=pathlib.Path(sys.argv[1]); dst=pathlib.Path(sys.argv[2]); obj=json.loads(src.read_text(encoding='utf-8')); dst.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')" $tempJsonPath $mcpJsonPath 2>&1
$fmtExitCode = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
if ($fmtExitCode -ne 0) {
    Remove-Item $tempJsonPath -Force -ErrorAction SilentlyContinue
    $fmtOutput | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    throw "Failed to format mcp.json with Python."
}

Remove-Item $tempJsonPath -Force

Write-Host "Updated VS Code MCP config: $mcpJsonPath"
Write-Host "Registered server: project-memory-mcp"
