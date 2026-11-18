$ErrorActionPreference = "Stop"
$tools = Join-Path $env:USERPROFILE "perinatal-devtools"
$backend = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $backend
$pgBin = Join-Path $tools "pgsql\bin"

# 1) PostgreSQL (listens on 127.0.0.1:5432 only)
& "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 5432 *> $null
if ($LASTEXITCODE -ne 0) {
    Start-Process -WindowStyle Hidden -FilePath "$pgBin\pg_ctl.exe" `
        -ArgumentList @("-D", (Join-Path $tools "pgdata"), "-l", (Join-Path $tools "postgres.log"), "start")
    for ($i = 0; $i -lt 30; $i++) { Start-Sleep 1; & "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 5432 *> $null; if ($LASTEXITCODE -eq 0) { break } }
}
Write-Host "PostgreSQL: ready"

# 2) Local STT on 127.0.0.1:9001
if (-not (Get-NetTCPConnection -LocalPort 9001 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -WindowStyle Hidden -WorkingDirectory $root -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "stt_server.server:app", "--host", "127.0.0.1", "--port", "9001") `
        -RedirectStandardOutput (Join-Path $tools "stt.log") -RedirectStandardError (Join-Path $tools "stt.err.log")
    Write-Host "STT: starting (model load can take ~10-60 s; check http://127.0.0.1:9001/health)"
} else { Write-Host "STT: already running" }

# 3) Migrations + API on 127.0.0.1:8000
Push-Location $backend
python -m alembic upgrade head
Pop-Location
if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) {
    Start-Process -WindowStyle Hidden -WorkingDirectory $backend -FilePath "python" `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") `
        -RedirectStandardOutput (Join-Path $tools "api.log") -RedirectStandardError (Join-Path $tools "api.err.log")
}
Write-Host "API: http://127.0.0.1:8000/api/docs"
