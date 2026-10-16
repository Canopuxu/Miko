. (Join-Path $PSScriptRoot 'common.ps1')
& (Join-Path $PSScriptRoot 'start_qq_bridge.ps1')
Invoke-Miko 'start'
try { if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'start_workbench.ps1')) { & (Join-Path $PSScriptRoot 'start_workbench.ps1') } }
catch { Write-Warning "Independent DSH workbench did not start: $_" }
