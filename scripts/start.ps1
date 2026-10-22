. (Join-Path $PSScriptRoot 'common.ps1')
$workbench = $null
try {
    try {
        if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'start_workbench.ps1')) {
            $workbench = & (Join-Path $PSScriptRoot 'start_workbench.ps1') -PassThru
        }
    } catch { Write-Warning "DSH workbench did not start: $_" }
    $runArguments = @()
    if ($workbench) { $runArguments = @('--workbench-pid', [string]$workbench.Id) }
    Invoke-Miko 'run' -Arguments $runArguments
} finally {
    if ($workbench -and -not $workbench.HasExited) {
        & taskkill.exe /PID $workbench.Id /T /F | Out-Null
    }
}
