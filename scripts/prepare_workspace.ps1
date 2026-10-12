[CmdletBinding()]
param(
    [string]$WorkspaceRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'workspace.layout.json'
$layout = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($layout.schemaVersion -ne 1) {
    throw 'Unsupported workspace layout version.'
}

$workspacePath = [IO.Path]::GetFullPath($WorkspaceRoot)
if (-not (Test-Path -LiteralPath $workspacePath -PathType Container)) {
    throw "Workspace directory does not exist: $workspacePath"
}

function Get-ContainedPath {
    param([string]$BasePath, [string]$RelativePath)

    if ([string]::IsNullOrWhiteSpace($RelativePath) -or [IO.Path]::IsPathRooted($RelativePath)) {
        throw "Expected a non-empty relative directory: $RelativePath"
    }
    $candidate = [IO.Path]::GetFullPath([IO.Path]::Combine($BasePath, $RelativePath))
    $prefix = $BasePath.TrimEnd([char[]]'\/') + [IO.Path]::DirectorySeparatorChar
    if (-not $candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Directory escapes its root: $RelativePath"
    }
    return $candidate
}

# Validate every target before creating any directory. Existing contents are preserved.
$targets = @()
foreach ($root in $layout.roots) {
    $rootPath = Get-ContainedPath -BasePath $workspacePath -RelativePath $root.name
    $targets += $rootPath
    foreach ($directory in $root.directories) {
        $targets += Get-ContainedPath -BasePath $rootPath -RelativePath $directory.path
    }
}

foreach ($target in $targets) {
    [void][IO.Directory]::CreateDirectory($target)
}
Write-Output "Prepared $($targets.Count) directory targets under $workspacePath."
