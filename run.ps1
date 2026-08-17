# Dev launcher for the fitness-app backend (PowerShell).
#
#   .\run.ps1              start on port 5057
#   .\run.ps1 -Port 5060   start on another port
#   .\run.ps1 -Stop        stop whatever is holding the port
#
# `set -a; . ./.env.dev` is bash syntax and does not work here — this script
# reads the same .env.dev file and exports each key into the process env.

param(
    [int]$Port = 5057,
    [switch]$Stop,
    [string]$EnvFile = ".env.dev"
)

$ErrorActionPreference = "Stop"

function Get-PortOwners([int]$p) {
    # netstat is more reliable than Get-NetTCPConnection across editions here.
    netstat -ano |
        Select-String -Pattern "LISTENING" |
        Select-String -Pattern ":$p\s" |
        ForEach-Object { ($_ -split '\s+')[-1] } |
        Sort-Object -Unique
}

function Stop-Port([int]$p) {
    $owners = Get-PortOwners $p
    if (-not $owners) { Write-Host "Nothing listening on $p"; return }
    foreach ($pid in $owners) {
        taskkill /F /PID $pid 2>&1 | Out-Null
        Write-Host "Stopped PID $pid on port $p"
    }
}

if ($Stop) { Stop-Port $Port; exit 0 }

# A stale server keeps answering with old code while the new one silently fails
# to bind — stop anything already on this port first.
$existing = Get-PortOwners $Port
if ($existing) {
    Write-Host "Port $Port already in use; stopping PID(s): $($existing -join ', ')"
    Stop-Port $Port
    Start-Sleep -Seconds 2
}

if (-not (Test-Path $EnvFile)) {
    throw "$EnvFile not found. Copy .env.example to $EnvFile and edit it."
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $i = $line.IndexOf("=")
        $name = $line.Substring(0, $i).Trim()
        $value = $line.Substring($i + 1).Trim()
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
        if ($name -match 'KEY|SECRET|PASSWORD') {
            Write-Host "  $name = ********"
        } else {
            Write-Host "  $name = $value"
        }
    }
}

Write-Host ""
Write-Host "Starting on http://127.0.0.1:$Port  (Ctrl+C to stop)"
Write-Host "Browsable: /  /healthz  /v1/app/config   - everything else is POST"
Write-Host ""

python -m flask --app wsgi run --port $Port
