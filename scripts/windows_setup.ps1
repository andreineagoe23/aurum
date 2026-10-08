<#
.SYNOPSIS
  Aurum Windows VM setup (Windows 11 ARM64 host running x64 Python via emulation).
.PARAMETER VenvPath
  Where the virtual environment lives. Must NOT be on a shared folder.
.PARAMETER DataDir
  Where market data is stored (AURUM_DATA_DIR).
.PARAMETER SkipTests
  Skip running pytest.
#>
[CmdletBinding()]
param(
    [string]$VenvPath = 'C:\venvs\aurum',
    [string]$DataDir = 'C:\aurum-data',
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
# MetaTrader5 wheels are win_amd64 only, so force x64 Python even on ARM64 Windows.
$PyRequest = 'cpython-3.12-windows-x86_64-none'

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Native([string]$What) {
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed with exit code $LASTEXITCODE."
    }
}

function Set-AurumEnv([string]$Name, [string]$Value) {
    Set-Item -Path "Env:$Name" -Value $Value
    [Environment]::SetEnvironmentVariable($Name, $Value, 'User')
    Write-Host "  $Name = $Value (session + User scope)"
}

# UNC paths (\\vmware-host\...) are not valid working directories for cmd/native tools,
# but Push-Location on a PowerShell provider path works for Z: mapped drives.
Set-Location -LiteralPath $RepoRoot
Write-Host "Repo root: $RepoRoot"

Write-Step 'Ensure uv is installed'
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    Assert-Native 'uv installer'
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is still not on PATH. Open a new PowerShell window and re-run this script.'
}
uv --version
Assert-Native 'uv --version'

Write-Step "Install x64 Python ($PyRequest)"
uv python install $PyRequest
Assert-Native 'uv python install'

Write-Step 'Configure environment variables'
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-AurumEnv 'UV_PROJECT_ENVIRONMENT' $VenvPath
Set-AurumEnv 'UV_PYTHON' $PyRequest
Set-AurumEnv 'UV_LINK_MODE' 'copy'   # hardlinks fail across the shared-folder boundary
Set-AurumEnv 'AURUM_DATA_DIR' $DataDir
Write-Host "  Persisted for your user: open a NEW terminal for them to take effect elsewhere."

Write-Step "uv sync (venv at $VenvPath)"
uv sync --python $PyRequest
Assert-Native 'uv sync'

Write-Step 'Verify interpreter architecture'
$VenvPython = Join-Path $VenvPath 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    throw "Expected venv interpreter not found at $VenvPython."
}
& $VenvPython -c "import platform,sys; print(platform.machine(), sys.version)"
Assert-Native 'python version check'
$Machine = (& $VenvPython -c "import platform; print(platform.machine())").Trim()
if ($Machine -ne 'AMD64') {
    throw "Python reports '$Machine' but AMD64 (x64) is required for the MetaTrader5 wheel. Remove $VenvPath and re-run so uv picks $PyRequest."
}
Write-Host "  OK: $Machine"

Write-Step 'Verify MetaTrader5 import'
& $VenvPython -c "import MetaTrader5; print(MetaTrader5.__version__)"
Assert-Native 'import MetaTrader5'

Write-Step 'Check .env'
$EnvFile = Join-Path $RepoRoot '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $RepoRoot '.env.example') $EnvFile
    Write-Warning "Created .env from .env.example. Fill in MT5_LOGIN, MT5_PASSWORD and MT5_SERVER (demo account) before running the collector."
} else {
    Write-Host '  .env already exists; leaving it untouched.'
}

Write-Step 'aurum doctor'
uv run aurum doctor
Assert-Native 'aurum doctor'

if ($SkipTests) {
    Write-Step 'pytest (skipped by -SkipTests)'
} else {
    Write-Step 'pytest'
    uv run pytest
    Assert-Native 'pytest'
}

Write-Step 'aurum mt5 ping'
uv run aurum mt5 ping
if ($LASTEXITCODE -eq 2) {
    Write-Warning 'mt5 ping exited with code 2: placeholder until Phase 1 (not implemented yet). Not a failure.'
} else {
    Assert-Native 'aurum mt5 ping'
}

Write-Host ""
Write-Host 'Windows setup complete.' -ForegroundColor Green
Write-Host "Venv: $VenvPath   Data: $DataDir"
