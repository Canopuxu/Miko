$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$local = Join-Path (Split-Path -Parent $repo) 'MikoLocal'
try {
    $status = Invoke-RestMethod 'http://127.0.0.1:7871/_miko/health' -TimeoutSec 2
    Write-Output 'DSH workbench is already running.'; return
} catch {}
if (Get-NetTCPConnection -LocalPort 7871 -State Listen -ErrorAction SilentlyContinue) {
    Write-Output 'DSH workbench is starting.'; return
}
$node = (Get-Command node -ErrorAction Stop).Source
New-Item -ItemType Directory -Force -Path (Join-Path $local 'logs') | Out-Null
Start-Process -FilePath $node -ArgumentList @((Join-Path $PSScriptRoot 'workbench.mjs')) -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput (Join-Path $local 'logs/workbench.log') -RedirectStandardError (Join-Path $local 'logs/workbench-error.log') | Out-Null
Write-Output 'DSH workbench is starting independently.'
