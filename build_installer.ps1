# Builds dist\installer\CyclingTrainer-Setup-<version>.exe
#   1. runs the tests, 2. bundles the app with PyInstaller, 3. wraps it with Inno Setup.
# Needs: .venv with requirements.txt + pyinstaller, and Inno Setup 6 (winget install JRSoftware.InnoSetup).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$python = ".venv\Scripts\python.exe"

$version = & $python -c "import trainer; print(trainer.__version__)"
Write-Host "Building Cycling Trainer $version"

& $python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

& $python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\cycling_trainer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup" }

& $iscc /Q "/DAppVersion=$version" packaging\installer.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Get-Item "dist\installer\CyclingTrainer-Setup-$version.exe" | Select-Object FullName, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
