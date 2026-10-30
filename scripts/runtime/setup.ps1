param([string]$Python = "")
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$VenvPython = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $VenvPython)) {
    if ($Python) {
        & $Python -m venv $Venv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.12 -m venv $Venv
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m venv $Venv
    } else {
        throw "Install 64-bit Python 3.12, then run setup.ps1 again."
    }
    if ($LASTEXITCODE -ne 0) { throw "Could not create the local virtual environment." }
}
& $VenvPython -c "import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, '64-bit Python 3.12 is required'"
if ($LASTEXITCODE -ne 0) { throw "This environment requires 64-bit Python 3.12." }
& $VenvPython -m pip install -r (Join-Path $Root "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. Check the pip output and network connection." }
& $VenvPython -m pip install --no-deps --no-build-isolation $Root
if ($LASTEXITCODE -ne 0) { throw "Package installation failed." }
& $VenvPython -m biomodel_agent workspace-doctor
if ($LASTEXITCODE -ne 0) { throw "Dependency check failed; no model service was contacted." }
Write-Host "Ready. Run start.ps1 or double-click start.cmd."
