# Stop API (8000), STT (9001) and PostgreSQL.
$tools = Join-Path $env:USERPROFILE "perinatal-devtools"
foreach ($port in 8000, 9001) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -Confirm:$false }
}
& (Join-Path $tools "pgsql\bin\pg_ctl.exe") -D (Join-Path $tools "pgdata") stop -m fast
Write-Host "stopped"
