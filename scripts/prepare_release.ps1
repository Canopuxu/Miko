$ErrorActionPreference = 'Stop'
$releaseRepo = Split-Path -Parent $PSScriptRoot
$releaseLocal = Join-Path (Split-Path -Parent $releaseRepo) 'MikoLocal'
$releaseRoot = Join-Path $releaseLocal ('releases\miko-core-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$releaseApp = Join-Path $releaseRoot 'Miko'
if (Test-Path -LiteralPath $releaseRoot) { throw 'Release output already exists.' }
New-Item -ItemType Directory -Path $releaseApp -Force | Out-Null

# Explicit source list: never copy the installed environment or private local data.
$releaseFiles = @('LICENSE','THIRD_PARTY.md','pyproject.toml','requirements.lock.txt',
    'config/python-dependencies.lock.json','config/web-dependencies.lock.json','config/upstream.lock.json',
    'workspace.layout.json','scripts/common.ps1','scripts/start.ps1','scripts/stop.ps1',
    'scripts/status.ps1','scripts/check.ps1','scripts/setup.ps1','scripts/prepare_workspace.ps1',
    'scripts/start_qq_bridge.ps1','scripts/run_qq_client_bridge.py',
    'scripts/load_qq_probe.py',
    'scripts/discover_qq.py','scripts/qq_clients.py')
$releaseDirs = @('apps/backend/src','apps/qq-bridge','config/defaults','config/examples',
    'licenses','packages/upstream/memu')
foreach ($relativeDir in $releaseDirs) {
    Get-ChildItem -LiteralPath (Join-Path $releaseRepo $relativeDir) -Recurse -File | Where-Object {
        $_.FullName -notmatch '[\\/](__pycache__|\.git|build|[^\\/]+\.egg-info)[\\/]' -and $_.Extension -notin @('.pyc','.pyo')
    } | ForEach-Object { $releaseFiles += [IO.Path]::GetRelativePath($releaseRepo, $_.FullName) }
}
foreach ($relativeFile in $releaseFiles) {
    $releaseDest = Join-Path $releaseApp $relativeFile
    New-Item -ItemType Directory -Path (Split-Path -Parent $releaseDest) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $releaseRepo $relativeFile) -Destination $releaseDest
}
# Keep both DSH Python distributions pinned; the independent npm workbench is excluded.
& pnpm --dir (Join-Path $releaseRepo 'apps/web') exec tsc --noEmit
if ($LASTEXITCODE -ne 0) { throw 'Type checking failed.' }
& pnpm --dir (Join-Path $releaseRepo 'apps/web') exec vite build --mode standalone --outDir (Join-Path $releaseApp 'apps/web/dist')
if ($LASTEXITCODE -ne 0) { throw 'Standalone UI build failed.' }
Write-Output "Release staging directory: $releaseRoot"
