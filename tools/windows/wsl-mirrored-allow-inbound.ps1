#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Let other machines on the LAN reach the WSL2 dashboard, under WSL's
    *mirrored* networking mode.

.DESCRIPTION
    READ THIS FIRST -- this is NOT the same job as wsl-portproxy-setup.ps1.

    With networkingMode=mirrored (set in %USERPROFILE%\.wslconfig), WSL shares
    the Windows network interfaces directly. A server bound to 0.0.0.0 inside
    WSL is therefore ALREADY listening on the host's LAN addresses -- there is
    no separate WSL IP to forward to, so `netsh interface portproxy` is the
    wrong tool and does nothing useful here.

    What actually blocks the LAN is the *Hyper-V firewall*, a separate policy
    layer from the normal Windows Firewall that governs mirrored WSL traffic.
    Out of the box it is:

        DefaultInboundAction : Block

    ...with only ICMP rules present. That is why ping works, the dashboard
    answers on 127.0.0.1, and every other machine gets nothing.

    This script adds an inbound Allow rule to the Hyper-V firewall for the
    given TCP port(s), scoped to WSL's VM creator ID.

.PARAMETER Port
    TCP port(s) to open. Defaults to 8765 (the dashboard default). Accepts a
    list: -Port 8765,8585

.PARAMETER RemoteAddress
    Who may connect. Defaults to "Any". Narrow it on an untrusted or shared
    substation network, e.g. -RemoteAddress 203.0.113.0/24

.PARAMETER Remove
    Delete the rules instead of creating them.

.EXAMPLE
    .\wsl-mirrored-allow-inbound.ps1
    .\wsl-mirrored-allow-inbound.ps1 -Port 8765,8585
    .\wsl-mirrored-allow-inbound.ps1 -Port 8765 -RemoteAddress 203.0.113.0/24
    .\wsl-mirrored-allow-inbound.ps1 -Port 8765 -Remove
#>
[CmdletBinding()]
param(
    # String, not int: New-NetFirewallHyperVRule types -LocalPorts as String[],
    # and passing an [int] fails CIM marshalling with "The port is invalid."
    # Strings also let you pass ranges, e.g. -Port 8000-8010
    [string[]]$Port = @("8765"),
    [string[]]$RemoteAddress = @("Any"),
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# WSL's fixed VM creator GUID. Hyper-V firewall rules are scoped per creator,
# so this confines every rule below to WSL traffic only.
$WslCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"

if (-not (Get-Command New-NetFirewallHyperVRule -ErrorAction SilentlyContinue)) {
    throw ("New-NetFirewallHyperVRule is unavailable on this build. " +
           "It requires a recent Windows 11 / Server build. Either update, " +
           "or switch .wslconfig to networkingMode=nat and use " +
           "wsl-portproxy-setup.ps1 instead.")
}

# --- Sanity: are we actually in mirrored mode? --------------------------
$cfg = Join-Path $env:USERPROFILE ".wslconfig"
if (Test-Path $cfg) {
    if (-not (Select-String -Path $cfg -Pattern 'networkingMode\s*=\s*mirrored' -Quiet)) {
        Write-Warning ("networkingMode=mirrored not found in $cfg. If you are " +
                       "in the default NAT mode, use wsl-portproxy-setup.ps1 " +
                       "instead -- this script will not help there.")
    }
} else {
    Write-Warning "No .wslconfig found; assuming mirrored mode was set elsewhere."
}

$inbound = (Get-NetFirewallHyperVVMSetting -PolicyStore ActiveStore |
            Where-Object Name -eq $WslCreatorId).DefaultInboundAction
Write-Host "Hyper-V firewall default inbound : $inbound"
Write-Host ""

foreach ($p in $Port) {
    $name = "WSL-SEL-Dashboard-$p"

    # Always clear any prior rule so re-running is idempotent.
    $existing = Get-NetFirewallHyperVRule -Name $name -ErrorAction SilentlyContinue
    if ($existing) {
        Remove-NetFirewallHyperVRule -Name $name
        Write-Host "Removed existing rule $name"
    }

    if ($Remove) { continue }

    # -RemoteAddresses accepts ONLY addresses / subnets / ranges. The literal
    # string "Any" is NOT an accepted value -- Any is merely the default when
    # the parameter is omitted. Passing "Any" makes the CIM layer reject the
    # whole filter tuple, which it reports (misleadingly) as
    # "The port is invalid." So omit the parameter unless genuinely scoping.
    $params = @{
        Name        = $name
        DisplayName = "WSL SEL Dashboard $p"
        Direction   = "Inbound"
        VMCreatorId = $WslCreatorId
        Protocol    = "TCP"
        LocalPorts  = [string[]]@($p)
        Action      = "Allow"
    }

    $scoped = @($RemoteAddress | Where-Object { $_ -and $_ -ne "Any" })
    if ($scoped.Count -gt 0) { $params["RemoteAddresses"] = [string[]]$scoped }

    New-NetFirewallHyperVRule @params | Out-Null

    $from = if ($scoped.Count -gt 0) { $scoped -join ', ' } else { "any address" }
    Write-Host "Allowed inbound TCP/$p from $from"
}

if ($Remove) {
    Write-Host ""
    Write-Host "Rules removed."
    exit 0
}

# --- Print the URLs to hand out -----------------------------------------
Write-Host ""
Write-Host "Reachable at:" -ForegroundColor Green

$addrs = Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike "169.254*" -and $_.IPAddress -ne "127.0.0.1" } |
    Sort-Object InterfaceAlias

foreach ($p in $Port) {
    # A range like "8000-8010" has no single URL to print; just note it.
    if ($p -notmatch '^\d+$') {
        "  port range {0} opened (no single URL)" -f $p
        continue
    }
    foreach ($a in $addrs) {
        "  http://{0}:{1}/    ({2})" -f $a.IPAddress, $p, $a.InterfaceAlias
    }
}

Write-Host ""
Write-Host "Note: the server inside WSL must bind 0.0.0.0 (not 127.0.0.1)."
Write-Host "      app.py --web already does. Verify with:  ss -tlnp | grep <port>"
