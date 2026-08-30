# ORCA — start the development stack.
#
#   .\scripts\dev.ps1              backend only (frontend does not exist yet)
#   .\scripts\dev.ps1 -Full        backend + frontend
#   .\scripts\dev.ps1 -NoInfra     force the SQLite / in-process path
#   .\scripts\dev.ps1 -Test        run the test suite and exit
#
# Optional infrastructure (Postgres + Redis) is started if Docker is running and
# skipped without complaint if it is not — the SQLite + in-process path is a
# supported configuration, not a failure mode.

[CmdletBinding()]
param(
  [switch]$Full,
  [switch]$NoInfra,
  [switch]$Test
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'

if (-not (Test-Path $python)) {
  Write-Host "No virtualenv at $python" -ForegroundColor Red
  Write-Host "  uv venv --python 3.12 .venv" -ForegroundColor DarkGray
  Write-Host "  uv pip install --python .venv\Scripts\python.exe -e '.\backend[dev]'" -ForegroundColor DarkGray
  exit 1
}

if (-not (Test-Path (Join-Path $repo '.env'))) {
  Write-Host "No .env — copy .env.example and fill it in." -ForegroundColor Red
  exit 1
}

# ----------------------------------------------------------------- test mode
if ($Test) {
  Push-Location (Join-Path $repo 'backend')
  try {
    & $python -m ruff check .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pytest -q
    exit $LASTEXITCODE
  } finally { Pop-Location }
}

# ------------------------------------------------------------ infrastructure
if ($NoInfra) {
  $env:ORCA_DB_DRIVER = 'sqlite'
  $env:REDIS_URL = ''
  Write-Host "-NoInfra: forcing SQLite + in-process cache and scheduler" -ForegroundColor Yellow
}
else {
  $dockerUp = $false
  try {
    docker version --format '{{.Server.Version}}' *> $null
    $dockerUp = ($LASTEXITCODE -eq 0)
  } catch { $dockerUp = $false }

  if ($dockerUp) {
    Write-Host "Docker is up — starting Postgres + Redis" -ForegroundColor Cyan
    Push-Location $repo
    try { docker compose up -d | Out-Null } finally { Pop-Location }
  }
  else {
    Write-Host "Docker is not running — ORCA will use SQLite + in-process fallbacks." -ForegroundColor DarkGray
    Write-Host "That is a supported configuration; nothing is broken." -ForegroundColor DarkGray
  }
}

# ------------------------------------------------------------------ backend
$jobs = @()
Write-Host "`nBackend  -> http://127.0.0.1:8000/docs" -ForegroundColor Green
$backend = Start-Process -PassThru -NoNewWindow -FilePath $python `
  -ArgumentList '-m', 'uvicorn', 'orca.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000' `
  -WorkingDirectory (Join-Path $repo 'backend')
$jobs += $backend

# ----------------------------------------------------------------- frontend
if ($Full) {
  $frontend = Join-Path $repo 'frontend'
  if (Test-Path (Join-Path $frontend 'package.json')) {
    Write-Host "Frontend -> http://127.0.0.1:5173" -ForegroundColor Green
    if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
      Push-Location $frontend; try { npm install } finally { Pop-Location }
    }
    $jobs += Start-Process -PassThru -NoNewWindow -FilePath 'npm' `
      -ArgumentList 'run', 'dev' -WorkingDirectory $frontend
  }
  else {
    Write-Host "-Full requested but frontend/package.json does not exist yet." -ForegroundColor Yellow
  }
}

Write-Host "`nCtrl+C to stop.`n" -ForegroundColor DarkGray
try {
  Wait-Process -Id ($jobs | ForEach-Object { $_.Id })
}
finally {
  foreach ($p in $jobs) {
    if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
  }
}
