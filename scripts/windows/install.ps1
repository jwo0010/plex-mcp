<#
.SYNOPSIS
    Installs the Plex MCP server on the Windows machine that runs Plex Media Server.

.DESCRIPTION
    1. Creates a Python virtual environment in .venv and installs the server into it.
    2. Creates .env (if missing), filling in your Plex token from the registry and a new MCP auth token.
    3. Tests the connection to Plex.
    4. Optionally opens the firewall port (Private networks only) and registers a scheduled task
       that starts the server at boot and restarts it if it crashes.

    Run from an elevated PowerShell (Run as Administrator) if you want the firewall rule and startup task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\install.ps1 -Port 8765 -SkipTask
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$TaskName = "Plex MCP Server",
    [switch]$SkipFirewall,
    [switch]$SkipTask
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $Root
Write-Host "Installing Plex MCP server in $Root" -ForegroundColor Cyan

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# --- 1. Python ---------------------------------------------------------------
$python = $null
foreach ($candidate in @("py -3", "python")) {
    try {
        $exe, $arg = $candidate.Split(" ", 2)
        $version = & $exe $arg -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$version -ge [version]"3.10") { $python = $candidate; break }
    } catch { }
}
if (-not $python) {
    throw "Python 3.10+ not found. Install it (e.g. 'winget install Python.Python.3.13') and re-run."
}
Write-Host "Using Python: $python ($version)"

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    $exe, $arg = $python.Split(" ", 2)
    & $exe $arg -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
}
Write-Host "Installing packages (this can take a minute)..."
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install --upgrade . --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed." }

# --- 2. .env -----------------------------------------------------------------
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $Root ".env.example") $envFile
    $content = Get-Content $envFile -Raw

    $plexToken = $null
    try {
        $plexToken = (Get-ItemProperty -Path "HKCU:\Software\Plex, Inc.\Plex Media Server" -Name PlexOnlineToken -ErrorAction Stop).PlexOnlineToken
    } catch { }
    if ($plexToken) {
        Write-Host "Found your Plex token in the registry." -ForegroundColor Green
    } else {
        Write-Host "Couldn't read the Plex token from the registry (Plex may run under a different Windows account)." -ForegroundColor Yellow
        $plexToken = Read-Host "Paste your X-Plex-Token (see README: 'Finding your Plex token')"
    }
    $authToken = & $venvPython -c "import secrets; print(secrets.token_urlsafe(32))"

    $content = $content -replace "(?m)^PLEX_TOKEN=.*$", "PLEX_TOKEN=$plexToken"
    $content = $content -replace "(?m)^PLEX_MCP_AUTH_TOKEN=.*$", "PLEX_MCP_AUTH_TOKEN=$authToken"
    $content = $content -replace "(?m)^PLEX_MCP_PORT=.*$", "PLEX_MCP_PORT=$Port"
    $content = $content -replace "(?m)^# PLEX_MCP_LOG_FILE=.*$", "PLEX_MCP_LOG_FILE=$Root\logs\plex-mcp.log"
    [System.IO.File]::WriteAllText($envFile, $content, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Wrote $envFile"
} else {
    Write-Host ".env already exists; leaving it unchanged."
}

# Keep the secrets file readable only by Administrators, SYSTEM, and you.
try {
    icacls $envFile /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" "$($env:USERNAME):F" | Out-Null
} catch { Write-Host "Could not tighten permissions on .env: $_" -ForegroundColor Yellow }

# --- 3. Connection test ------------------------------------------------------
Write-Host "Testing the connection to Plex..."
& (Join-Path $Root ".venv\Scripts\plex-mcp.exe") --env-file $envFile --check
if ($LASTEXITCODE -ne 0) { throw "Could not connect to Plex. Check PLEX_URL and PLEX_TOKEN in .env." }

# --- 4. Firewall + startup task ---------------------------------------------
$isAdmin = Test-Admin
if (-not $SkipFirewall) {
    if ($isAdmin) {
        $rule = "Plex MCP Server (TCP $Port)"
        if (-not (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue)) {
            New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port `
                -Action Allow -Profile Private | Out-Null
            Write-Host "Opened TCP $Port for Private networks." -ForegroundColor Green
        }
    } else {
        Write-Host "Not elevated: skipping firewall rule. Re-run as Administrator, or allow TCP $Port manually." -ForegroundColor Yellow
    }
}

if (-not $SkipTask) {
    if ($isAdmin) {
        $exePath = Join-Path $Root ".venv\Scripts\plex-mcp.exe"
        $action = New-ScheduledTaskAction -Execute $exePath -Argument "--env-file `"$envFile`"" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
            -StartWhenAvailable
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
            -Settings $settings -Description "MCP server for Plex Media Server" -Force | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 4
        try {
            $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 5
            Write-Host "Server is running (health: $($health.status))." -ForegroundColor Green
        } catch {
            Write-Host "Task registered, but the health check failed. See $Root\logs\plex-mcp.log" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Not elevated: skipping startup task. Use scripts\windows\run.ps1 to run in the foreground." -ForegroundColor Yellow
    }
}

$token = (Select-String -Path $envFile -Pattern "^PLEX_MCP_AUTH_TOKEN=(.*)$").Matches[0].Groups[1].Value
$hostName = [System.Net.Dns]::GetHostName()
Write-Host ""
Write-Host "Done. Connect agents to:" -ForegroundColor Cyan
Write-Host "  URL:    http://$($hostName.ToLower()):$Port/mcp"
Write-Host "  Header: Authorization: Bearer $token"
