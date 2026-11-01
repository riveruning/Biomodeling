param(
    [int]$Port = 8765,
    [string]$Store = "",
    [switch]$AllowProvider
)
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Run setup.ps1 first." }
if (-not $Store) { $Store = Join-Path $Root "runs\workbench" }
if ($Port -lt 1 -or $Port -gt 65535) { throw "Port must be between 1 and 65535." }
$Arguments = @("-m", "biomodel_agent", "workbench", "--store", $Store, "--port", "$Port")
if ($AllowProvider) { $Arguments += "--allow-provider" }
Write-Host "Open http://127.0.0.1:$Port/projects in your browser. Keep this window open."
& $Python @Arguments
exit $LASTEXITCODE
