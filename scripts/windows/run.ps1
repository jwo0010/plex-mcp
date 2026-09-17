<#
.SYNOPSIS
    Runs the Plex MCP server in the foreground (Ctrl+C to stop). Handy for testing before installing the task.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\windows\run.ps1 -ReadOnly
#>
param([switch]$ReadOnly)

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$exe = Join-Path $Root ".venv\Scripts\plex-mcp.exe"
if (-not (Test-Path $exe)) { throw "Not installed yet. Run scripts\windows\install.ps1 first." }

$argsList = @("--env-file", (Join-Path $Root ".env"))
if ($ReadOnly) { $argsList += "--read-only" }

$task = Get-ScheduledTask -TaskName "Plex MCP Server" -ErrorAction SilentlyContinue
if ($task -and $task.State -eq "Running") {
    Write-Host "The 'Plex MCP Server' scheduled task is already running on this port. Stop it first:" -ForegroundColor Yellow
    Write-Host "  Stop-ScheduledTask -TaskName 'Plex MCP Server'"
    exit 1
}
& $exe @argsList
