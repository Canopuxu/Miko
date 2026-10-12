[CmdletBinding()]
param([string]$Pnpm = 'pnpm')
. (Join-Path $PSScriptRoot 'common.ps1')

$userConfig = Join-Path $mikoLocalRoot 'config\npmrc'
$globalConfig = Join-Path $mikoLocalRoot 'config\npm-globalrc'
foreach ($path in @($userConfig, $globalConfig)) {
    if (-not (Test-Path -LiteralPath $path)) { [IO.File]::WriteAllText($path, '') }
}
$previous = @{}
foreach ($name in @('npm_config_userconfig','npm_config_globalconfig','npm_config_cache','npm_config_manage_package_manager_versions')) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:npm_config_userconfig = $userConfig
    $env:npm_config_globalconfig = $globalConfig
    $env:npm_config_cache = Join-Path $mikoLocalRoot 'cache\npm'
    $env:npm_config_manage_package_manager_versions = 'false'
    Push-Location $mikoRepoRoot
    try {
        & $Pnpm install --frozen-lockfile --store-dir (Join-Path $mikoLocalRoot 'cache\pnpm') --registry https://registry.npmjs.org
        if ($LASTEXITCODE -ne 0) { throw 'Web dependency installation failed.' }
        & $Pnpm build:web
        if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
    } finally { Pop-Location }
} finally { Restore-MikoEnvironment $previous }
