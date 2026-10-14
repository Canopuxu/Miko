. (Join-Path $PSScriptRoot 'common.ps1')
Invoke-Miko 'start'
try { & (Join-Path $PSScriptRoot 'start_workbench.ps1') }
catch { Write-Warning "Independent DSH workbench did not start: $_" }
