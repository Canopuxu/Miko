[CmdletBinding()]
param([string]$Python = 'E:\Software\Anaconda3\envs\tulpa\python.exe')
. (Join-Path $PSScriptRoot 'common.ps1')

& (Join-Path $PSScriptRoot 'prepare_workspace.ps1')
# Lowercase 'nul' is Python's os.devnull on Windows; it disables shared pip configuration.
$environmentNames = @('PIP_CONFIG_FILE','PIP_INDEX_URL','PIP_EXTRA_INDEX_URL','PIP_CACHE_DIR','PYTHONPATH','PYTHONHOME','TEMP','TMP')
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $env:PIP_CONFIG_FILE = 'nul'
    $env:PIP_INDEX_URL = 'https://pypi.org/simple'
    $env:PIP_EXTRA_INDEX_URL = ''
    $env:PIP_CACHE_DIR = Join-Path $mikoLocalRoot 'cache\python'
    $env:PYTHONPATH = ''
    $env:PYTHONHOME = ''
    $env:TEMP = Join-Path $mikoLocalRoot 'cache\tmp'
    $env:TMP = $env:TEMP
    if (-not (Test-Path -LiteralPath $mikoPython)) {
        & $Python -m venv (Join-Path $mikoLocalRoot 'envs\python')
        if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed.' }
    }
    & $mikoPython -m pip install --disable-pip-version-check -r (Join-Path $mikoRepoRoot 'requirements.lock.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    & $mikoPython -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e $mikoRepoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Miko package installation failed.' }
} finally {
    Restore-MikoEnvironment $previousEnvironment
}
$configPath = Join-Path $mikoLocalRoot 'config\miko.local.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    Copy-Item -LiteralPath (Join-Path $mikoRepoRoot 'config\examples\miko.local.json') -Destination $configPath
}
Invoke-Miko 'check'
