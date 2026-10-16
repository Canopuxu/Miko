. (Join-Path $PSScriptRoot 'common.ps1')
$bridgeConfigPath = Join-Path $mikoLocalRoot 'config\qq-client.local.json'
if (-not (Test-Path -LiteralPath $bridgeConfigPath)) { return }
$replyConfigPath = Join-Path $mikoLocalRoot 'config\qq.local.json'
if (-not (Test-Path -LiteralPath $replyConfigPath)) { return }
$replyConfig = Get-Content -LiteralPath $replyConfigPath -Raw | ConvertFrom-Json
if ($replyConfig.enabled -eq $false) { return }
$bridgeConfig = Get-Content -LiteralPath $bridgeConfigPath -Raw | ConvertFrom-Json
$bridgeScript = Join-Path $PSScriptRoot 'run_qq_client_bridge.py'
$existingBridge = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains('run_qq_client_bridge.py') -and
    $_.CommandLine.Replace('/', '\').Contains($bridgeConfigPath.Replace('/', '\'))
}
if ($existingBridge) { return }
$bridgeArguments = @('-X', 'utf8', ('"' + $bridgeScript + '"'), '--config',
    ('"' + $bridgeConfigPath + '"'))
if ($bridgeConfig.frida_path) {
    $bridgeArguments += @('--frida-path', ('"' + $bridgeConfig.frida_path + '"'))
}
Start-Process -FilePath $mikoPython -ArgumentList $bridgeArguments -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $mikoLocalRoot 'logs\qq-bridge-out.log') `
    -RedirectStandardError (Join-Path $mikoLocalRoot 'logs\qq-bridge-error.log') | Out-Null
