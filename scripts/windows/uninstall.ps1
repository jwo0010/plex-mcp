<#
.SYNOPSIS
    Removes the startup task and firewall rule. Leaves the folder, .venv, and .env in place.
    Run as Administrator.
#>
param([int]$Port = 8765, [string]$TaskName = "Plex MCP Server")

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
}
$rule = "Plex MCP Server (TCP $Port)"
if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $rule
    Write-Host "Removed firewall rule '$rule'."
}
Write-Host "Done. You can now delete the plex-mcp folder if you no longer need it."
