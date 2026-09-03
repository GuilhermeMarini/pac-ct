#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Remove the WSL2 dashboard portproxy + firewall rules created by
    wsl-portproxy-setup.ps1.

.DESCRIPTION
    Deletes the portproxy rule on 0.0.0.0:<Port> and the firewall rule
    "<RuleName> <Port>" if they exist. Quiet no-op if they don't, so it
    is always safe to run.

.PARAMETER Port
    TCP port that was forwarded. Defaults to 8765.

.PARAMETER RuleName
    Base name used for the firewall rule. Must match what was passed to
    wsl-portproxy-setup.ps1. Defaults to "WSL SEL Dashboard".

.EXAMPLE
    .\wsl-portproxy-remove.ps1
    .\wsl-portproxy-remove.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$RuleName = "WSL SEL Dashboard"
)

$ErrorActionPreference = "Continue"

$displayName = "$RuleName $Port"
$didSomething = $false

# --- Portproxy ----------------------------------------------------------
$existingProxy = netsh interface portproxy show v4tov4 |
    Select-String -Pattern "^\s*0\.0\.0\.0\s+$Port\b"
if ($existingProxy) {
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$Port | Out-Null
    Write-Host "Removed portproxy on 0.0.0.0:$Port"
    $didSomething = $true
} else {
    Write-Host "No portproxy rule on 0.0.0.0:$Port"
}

# --- Firewall -----------------------------------------------------------
$existingFw = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue
if ($existingFw) {
    Remove-NetFirewallRule -DisplayName $displayName
    Write-Host "Removed firewall rule '$displayName'"
    $didSomething = $true
} else {
    Write-Host "No firewall rule '$displayName'"
}

Write-Host ""
if ($didSomething) {
    Write-Host "Cleanup complete."
} else {
    Write-Host "Nothing to clean up."
}
