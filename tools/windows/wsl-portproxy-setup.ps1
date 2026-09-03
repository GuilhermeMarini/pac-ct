#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Expose the WSL2 SEL Commissioning dashboard to the LAN.

.DESCRIPTION
    1. Reads the current WSL2 IP (it changes on every WSL restart).
    2. Removes any prior portproxy rule on the same listen port and any
       firewall rule with the same DisplayName -- so re-running after a
       restart never leaves stale entries behind.
    3. Adds a fresh portproxy + inbound firewall allow for $Port/TCP.
    4. Prints the LAN URLs other devices can use.

    Safe to re-run anytime the WSL2 IP changes. To remove the rules
    completely, use wsl-portproxy-remove.ps1.

.PARAMETER Port
    TCP port to forward. Defaults to 8765 (the dashboard default).

.PARAMETER RuleName
    Base name for the firewall rule. The actual DisplayName is
    "<RuleName> <Port>" so multiple ports do not collide.

.EXAMPLE
    .\wsl-portproxy-setup.ps1
    .\wsl-portproxy-setup.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [string]$RuleName = "WSL SEL Dashboard"
)

$ErrorActionPreference = "Stop"

# --- 0. This script is for NAT mode ONLY --------------------------------
# Under networkingMode=mirrored, WSL shares the host's interfaces: a server
# bound to 0.0.0.0 in WSL is already on the LAN addresses, and `wsl hostname
# -I` returns the HOST's own IPs. Forwarding 0.0.0.0:$Port to one of those is
# self-referential and does nothing. There, the LAN is blocked by the Hyper-V
# firewall instead -- use wsl-mirrored-allow-inbound.ps1.
$cfg = Join-Path $env:USERPROFILE ".wslconfig"
if ((Test-Path $cfg) -and
    (Select-String -Path $cfg -Pattern 'networkingMode\s*=\s*mirrored' -Quiet)) {
    throw ("WSL is in mirrored networking mode -- portproxy is the wrong tool " +
           "and would create a self-referential rule. Use " +
           "tools\windows\wsl-mirrored-allow-inbound.ps1 instead.")
}

# --- 1. Resolve current WSL2 IP -----------------------------------------
# `wsl hostname -I` returns one or more space-separated IPv4s. Extract the
# first via regex so any BOM/whitespace/newline from wsl.exe is ignored.
$raw = wsl hostname -I 2>$null
$ipMatch = [regex]::Match("$raw", '(\d{1,3}(?:\.\d{1,3}){3})')
if (-not $ipMatch.Success) {
    throw "Could not resolve WSL2 IP. Is WSL running? Try 'wsl -l -v'."
}
$wslIp = $ipMatch.Value
$displayName = "$RuleName $Port"

Write-Host "WSL2 IP : $wslIp"
Write-Host "Port    : $Port"
Write-Host ""

# --- 2. Remove any stale portproxy rule on this listen port -------------
$existingProxy = netsh interface portproxy show v4tov4 |
    Select-String -Pattern "^\s*0\.0\.0\.0\s+$Port\b"
if ($existingProxy) {
    netsh interface portproxy delete v4tov4 listenaddress=0.0.0.0 listenport=$Port | Out-Null
    Write-Host "Removed stale portproxy on 0.0.0.0:$Port"
}

# --- 3. Remove any prior firewall rule with our DisplayName -------------
$existingFw = Get-NetFirewallRule -DisplayName $displayName -ErrorAction SilentlyContinue
if ($existingFw) {
    Remove-NetFirewallRule -DisplayName $displayName
    Write-Host "Removed stale firewall rule '$displayName'"
}

# --- 4. Add fresh portproxy ---------------------------------------------
netsh interface portproxy add v4tov4 `
    listenaddress=0.0.0.0 listenport=$Port `
    connectaddress=$wslIp connectport=$Port | Out-Null
Write-Host "Added portproxy : 0.0.0.0:$Port -> ${wslIp}:$Port"

# --- 5. Add fresh firewall rule -----------------------------------------
New-NetFirewallRule `
    -DisplayName $displayName `
    -Direction Inbound `
    -LocalPort $Port `
    -Protocol TCP `
    -Action Allow | Out-Null
Write-Host "Added firewall rule '$displayName' (TCP $Port inbound)"

# --- 6. Show URLs other devices can use ---------------------------------
# Exclude loopback, the WSL vEthernet bridge, and link-local addresses.
$lanIps = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object {
        $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and
        $_.InterfaceAlias -notmatch 'Loopback|vEthernet \(WSL'
    } |
    Select-Object -ExpandProperty IPAddress -Unique

Write-Host ""
Write-Host "Reach the dashboard from the LAN at:"
foreach ($ip in $lanIps) {
    Write-Host "  http://${ip}:$Port"
}
Write-Host ""
Write-Host "Make sure 'python3 app.py --web' is running inside WSL."
