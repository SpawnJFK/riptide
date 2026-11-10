# Riptide launcher for Windows. First run sets everything up, later runs just start.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\start.ps1 [-Dev]
param([switch]$Dev)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Step($msg) { Write-Host "[riptide] $msg" }

# Python 3.13 venv (libtorrent ships Windows wheels for 3.9-3.13)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
  Step "creating Python 3.13 environment"
  # --seed: the install step below uses pip, which uv venvs do not include by default
  if (Get-Command uv -ErrorAction SilentlyContinue) { uv venv --seed --python 3.13 .venv }
  else { py -3.13 -m venv .venv }
}
$py = Join-Path $root ".venv\Scripts\python.exe"
$stamp = ".venv\.riptide-installed"
if (-not (Test-Path $stamp) -or ((Get-Item backend\pyproject.toml).LastWriteTime -gt (Get-Item $stamp).LastWriteTime)) {
  Step "installing backend"
  & $py -m pip install --disable-pip-version-check -q -e "backend[dev]"
  New-Item -ItemType File -Force $stamp | Out-Null
}

# Web UI build (only when sources changed)
Push-Location web
if (-not (Test-Path node_modules)) { Step "installing web dependencies"; npm ci --no-audit --no-fund }
$needBuild = -not (Test-Path dist\index.html)
if (-not $needBuild) {
  $built = (Get-Item dist\index.html).LastWriteTime
  $needBuild = (Get-ChildItem src -Recurse | Where-Object { $_.LastWriteTime -gt $built }).Count -gt 0
}
if ($Dev) { Pop-Location; Step "dev mode: backend on 8787, Vite on 5173"; Start-Process -NoNewWindow $py "-m riptide --no-browser"; Set-Location web; npm run dev; exit }
if ($needBuild) { Step "building web UI"; npm run build | Out-Null }
Pop-Location

# Normal mode runs windowless: riptide.launch starts pythonw.exe detached, this console closes, the
# browser opens and the tray icon is how you open or quit Riptide. Logs: %LOCALAPPDATA%\Riptide\data\logs.
Step "starting (no console window; use the tray icon to open or quit)"
& $py -m riptide.launch
