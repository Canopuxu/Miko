$ErrorActionPreference = 'Stop'
$mikoRepoRoot = Split-Path -Parent $PSScriptRoot
$mikoLocalRoot = Join-Path (Split-Path -Parent $mikoRepoRoot) 'MikoLocal'
$mikoPython = Join-Path $mikoLocalRoot 'envs\python\Scripts\python.exe'

function Restore-MikoEnvironment {
    param([hashtable]$Previous)
    foreach ($name in $Previous.Keys) {
        if ($null -eq $Previous[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            Set-Item -LiteralPath "Env:$name" -Value $Previous[$name]
        }
    }
}

function Invoke-Miko {
    param([string]$Action)
    if (-not (Test-Path -LiteralPath $mikoPython)) {
        throw 'Run scripts\setup.ps1 first.'
    }
    # Ignore inherited Python module paths; the editable package belongs to this environment.
    $previousPythonPath = $env:PYTHONPATH
    $previousPythonHome = $env:PYTHONHOME
    try {
        $env:PYTHONPATH = ''
        $env:PYTHONHOME = ''
        & $mikoPython -m miko.cli $Action --local-root $mikoLocalRoot
        if ($LASTEXITCODE -ne 0) { throw "Miko $Action failed with exit code $LASTEXITCODE" }
    } finally {
        Restore-MikoEnvironment @{PYTHONPATH=$previousPythonPath; PYTHONHOME=$previousPythonHome}
    }
}
