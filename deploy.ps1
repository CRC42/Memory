param(
    [string]$PythonExe = "python",
    [switch]$InstallDeps,
    [switch]$InstallDevDeps,
    [switch]$ForceRecreate,
    [switch]$RegisterVSCode
)

$ErrorActionPreference = "Stop"

$memoryRoot = (Resolve-Path $PSScriptRoot).Path
$venvDir = Join-Path $memoryRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if ($ForceRecreate -and (Test-Path $venvDir)) {
    Remove-Item -Recurse -Force $venvDir
}

if (!(Test-Path $venvPython)) {
    $prevEAP0 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $PythonExe -m venv $venvDir 2>&1 | ForEach-Object { Write-Host "  $_" -ForegroundColor DarkGray }
    $ErrorActionPreference = $prevEAP0
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
    Write-Host "Created virtual environment at: $venvDir"
}
else {
    Write-Host "Virtual environment already exists: $venvDir"
}

if ($InstallDeps -or $InstallDevDeps) {
    $prevEAP2 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $venvPython -m pip install --upgrade pip 2>&1 | Out-Null
    $ErrorActionPreference = $prevEAP2

    # 辅助函数：运行 pip 并实时输出日志，带总超时保护
    function Invoke-PipInstall {
        param(
            [string]$PipExePath,
            [string[]]$Arguments,
            [int]$TimeoutSeconds = 600
        )
        $psi = [System.Diagnostics.ProcessStartInfo]::new()
        $psi.FileName  = $PipExePath
        $psi.Arguments = $Arguments -join ' '
        $psi.UseShellExecute = $false
        # 不重定向输出，让 pip 直接打印到控制台
        $psi.RedirectStandardOutput = $false
        $psi.RedirectStandardError  = $false

        $proc = [System.Diagnostics.Process]::Start($psi)

        $exited = $proc.WaitForExit($TimeoutSeconds * 1000)
        if (-not $exited) {
            try { $proc.Kill() } catch {}
            Write-Host "  ERROR: pip process timed out after $TimeoutSeconds seconds." -ForegroundColor Red
            return 124
        }

        return $proc.ExitCode
    }

    $pipExe = Join-Path $venvDir "Scripts\pip.exe"
    $runtimeReq = Join-Path $memoryRoot "requirements.txt"
    $vendorDir  = Join-Path $memoryRoot "vendor"
    if (Test-Path $runtimeReq) {
        $installed = $false

        # 优先离线 vendor 安装（稳定、快速、无网络依赖）
        if (Test-Path $vendorDir) {
            Write-Host "  Trying offline vendor wheels first..." -ForegroundColor DarkGray
            $offlineArgs = @('install', '-r', "`"$runtimeReq`"", '--no-index', '--find-links', "`"$vendorDir`"")
            $exitCode = Invoke-PipInstall -PipExePath $pipExe -Arguments $offlineArgs -TimeoutSeconds 300
            if ($exitCode -eq 0) {
                $installed = $true
            } else {
                Write-Host "  Offline vendor install failed (exit=$exitCode), will try online..." -ForegroundColor Yellow
            }
        }

        # 离线失败或无 vendor 目录 → 在线回退
        if (-not $installed) {
            Write-Host "  Trying online install (timeout per-socket=120s, process=600s)..." -ForegroundColor DarkGray
            $onlineArgs = @('install', '-r', "`"$runtimeReq`"", '--retries', '3', '--timeout', '120', '--progress-bar', 'on')
            $exitCode2 = Invoke-PipInstall -PipExePath $pipExe -Arguments $onlineArgs -TimeoutSeconds 600
            if ($exitCode2 -eq 0) {
                $installed = $true
            } else {
                Write-Host "  Online install also failed (exit=$exitCode2)." -ForegroundColor Yellow
            }
        }

        if (-not $installed) {
            Write-Host "ERROR: Failed to install runtime dependencies." -ForegroundColor Red
            exit 1
        }
    }

    if ($InstallDevDeps) {
        $devReq = Join-Path $memoryRoot "requirements-dev.txt"
        if (Test-Path $devReq) {
            $prevEAP3 = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
            & $venvPython -m pip install -r $devReq 2>&1 | Out-Null
            $ErrorActionPreference = $prevEAP3
        }
    }
}

if ($RegisterVSCode) {
    & (Join-Path $memoryRoot "setup_mcp.ps1")
}

Write-Host "Python: $venvPython"
Write-Host "Done."
