#!/usr/bin/env bash
#
# Fix outbound source-address selection under WSL2 mirrored networking.
#
# WHY THIS EXISTS
# ---------------
# With networkingMode=mirrored, WSL mirrors the Windows interfaces. When one
# interface carries several IPv4 addresses on different subnets (common on a
# commissioning laptop: corporate LAN + relay LAN + hotspot), WSL creates the
# connected routes WITHOUT an `src` hint:
#
#     203.0.113.0/24 dev eth0 proto kernel scope link metric 291
#
# The kernel then falls back to the interface's *primary* address for every
# one of those subnets. Packets to a relay on 203.0.113.0/24 leave with a
# 192.168.2.x source, the relay has no route back, and every TCP connect()
# hangs until it times out. ICMP often still appears to work, which makes the
# failure look like "the app can't reach the network" rather than a routing
# bug -- so ping is NOT a valid test here.
#
# THE FIX
# -------
# For each connected route that lacks `src`, re-add it with the address that
# actually belongs to that subnet.
#
# WSL rebuilds its routes on every restart AND on every network transition
# (docking, VPN up/down, changing Wi-Fi, plugging into a different relay
# panel). So a one-shot run is not enough -- use --watch, or install the
# systemd unit via tools/wsl-install-src-route-fix.sh.
#
# Usage:
#     sudo ./tools/wsl-fix-src-routes.sh              # fix once, all interfaces
#     sudo ./tools/wsl-fix-src-routes.sh eth0         # fix once, one interface
#          ./tools/wsl-fix-src-routes.sh --dry-run    # show what would change
#     sudo ./tools/wsl-fix-src-routes.sh --watch      # fix, then re-fix forever

set -euo pipefail

DRY_RUN=0
WATCH=0
SETTLE=1.5          # seconds to let route churn settle before re-applying
IFACES=()

for arg in "$@"; do
    case "$arg" in
        --dry-run|-n) DRY_RUN=1 ;;
        --watch|-w)   WATCH=1 ;;
        -h|--help)    sed -n '2,40p' "$0"; exit 0 ;;
        -*)           echo "unknown option: $arg" >&2; exit 2 ;;
        *)            IFACES+=("$arg") ;;
    esac
done

if [[ $DRY_RUN -eq 1 && $WATCH -eq 1 ]]; then
    echo "error: --dry-run and --watch are mutually exclusive" >&2
    exit 2
fi

if [[ $DRY_RUN -eq 0 && $EUID -ne 0 ]]; then
    echo "error: needs root to modify routes (re-run with sudo, or pass --dry-run)" >&2
    exit 1
fi

# Log with a timestamp only in watch mode, where output goes to the journal.
say() {
    if [[ $WATCH -eq 1 ]]; then
        printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"
    else
        printf '%s\n' "$*"
    fi
}

# Is $1 (an address) inside $2 (a prefix)?
in_network() {
    python3 -c "
import ipaddress,sys
sys.exit(0 if ipaddress.ip_address('$1') in ipaddress.ip_network('$2') else 1)
"
}

# Walk every interface and repair any connected route missing the right src.
# Echoes progress; returns the number of routes changed via $APPLY_CHANGED.
APPLY_CHANGED=0
apply_all() {
    local ifc cidr addr subnet line prefix existing metric cmd
    local changed=0 checked=0
    local -a targets=()

    if [[ ${#IFACES[@]} -eq 0 ]]; then
        mapfile -t targets < <(ip -4 -o addr show scope global | awk '{print $2}' | sort -u)
    else
        targets=("${IFACES[@]}")
    fi

    for ifc in "${targets[@]}"; do
        while read -r cidr; do
            [[ -n "$cidr" ]] || continue
            addr="${cidr%%/*}"

            subnet="$(ip -4 route show dev "$ifc" proto kernel |
                      awk -v p="${cidr#*/}" '$1 ~ "/"p"$"')"

            while read -r line; do
                [[ -n "$line" ]] || continue
                prefix="$(awk '{print $1}' <<<"$line")"

                in_network "$addr" "$prefix" || continue
                checked=$((checked + 1))

                if grep -q ' src ' <<<"$line"; then
                    existing="$(sed -n 's/.* src \([0-9.]*\).*/\1/p' <<<"$line")"
                    if [[ "$existing" == "$addr" ]]; then
                        [[ $WATCH -eq 1 ]] || say "  ok      $(printf '%-20s' "$prefix") src $addr"
                        continue
                    fi
                fi

                metric="$(sed -n 's/.* metric \([0-9]*\).*/\1/p' <<<"$line")"
                cmd=(ip route replace "$prefix" dev "$ifc" proto kernel scope link src "$addr")
                [[ -n "$metric" ]] && cmd+=(metric "$metric")

                if [[ $DRY_RUN -eq 1 ]]; then
                    say "  WOULD   $(printf '%-20s' "$prefix") src $addr   (${cmd[*]})"
                else
                    "${cmd[@]}"
                    say "  FIXED   $(printf '%-20s' "$prefix") src $addr"
                fi
                changed=$((changed + 1))
            done <<<"$subnet"
        done < <(ip -4 -o addr show dev "$ifc" scope global | awk '{print $4}')
    done

    APPLY_CHANGED=$changed

    if [[ $WATCH -eq 0 ]]; then
        echo
        if [[ $DRY_RUN -eq 1 ]]; then
            echo "dry run: $changed of $checked connected route(s) would change."
        else
            echo "done: $changed of $checked connected route(s) updated."
        fi
    elif [[ $changed -gt 0 ]]; then
        say "repaired $changed of $checked connected route(s)"
    fi
}

if [[ $WATCH -eq 0 ]]; then
    apply_all
    exit 0
fi

# --- watch mode ---------------------------------------------------------
# Re-apply on every address/route change. `ip route replace` itself emits
# netlink events, so debounce: after waking, swallow further events for
# $SETTLE seconds before acting. The pass that follows a self-inflicted
# event finds everything already correct, changes nothing, emits nothing,
# and the loop goes quiet again.
say "watching for address/route changes (settle ${SETTLE}s)"
apply_all

# `read -t` returns >128 on timeout, which would trip `set -e`; guard with ||:.
ip monitor address route 2>/dev/null | while read -r _; do
    while read -r -t "$SETTLE" _; do :; done ||:
    apply_all
done
