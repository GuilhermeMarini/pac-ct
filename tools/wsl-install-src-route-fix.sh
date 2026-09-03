#!/usr/bin/env bash
#
# Install the WSL2 source-route fix as a permanent systemd service.
#
# Copies wsl-fix-src-routes.sh to /usr/local/sbin/ and enables a unit that
# runs it in --watch mode: it repairs the routes at boot and re-repairs them
# every time WSL rebuilds the network (docking, VPN, Wi-Fi change, plugging
# into a different relay panel).
#
# Requires systemd, which WSL only runs when /etc/wsl.conf has:
#     [boot]
#     systemd=true
#
# Usage:
#     sudo ./tools/wsl-install-src-route-fix.sh
#     sudo ./tools/wsl-install-src-route-fix.sh --uninstall

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_SRC="$SRC_DIR/wsl-fix-src-routes.sh"
UNIT_SRC="$SRC_DIR/wsl-src-route-fix.service"
SCRIPT_DST="/usr/local/sbin/wsl-fix-src-routes.sh"
UNIT_DST="/etc/systemd/system/wsl-src-route-fix.service"
UNIT_NAME="wsl-src-route-fix.service"

if [[ $EUID -ne 0 ]]; then
    echo "error: must run as root (use sudo)" >&2
    exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_DST" "$SCRIPT_DST"
    systemctl daemon-reload
    echo "uninstalled. Note: routes keep whatever src they currently have"
    echo "until the next WSL restart."
    exit 0
fi

# /run/systemd/system exists iff systemd is the running init -- the canonical
# test. (`pidof systemd` is unreliable: it does not match PID 1 on WSL.)
if [[ ! -d /run/systemd/system ]]; then
    cat >&2 <<'EOF'
error: systemd is not running under WSL.

Add this to /etc/wsl.conf, then run `wsl --shutdown` from Windows:

    [boot]
    systemd=true

Alternatively, skip systemd entirely and use a boot command instead:

    [boot]
    systemd=true
    command="/usr/local/sbin/wsl-fix-src-routes.sh"

(the boot command runs once at start, so it will NOT survive later
network changes -- the systemd watcher is the durable option.)
EOF
    exit 1
fi

for f in "$SCRIPT_SRC" "$UNIT_SRC"; do
    [[ -f "$f" ]] || { echo "error: missing $f" >&2; exit 1; }
done

install -m 0755 "$SCRIPT_SRC" "$SCRIPT_DST"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
echo "installed $SCRIPT_DST"
echo "installed $UNIT_DST"

systemctl daemon-reload
systemctl enable --now "$UNIT_NAME"

echo
systemctl --no-pager --lines=0 status "$UNIT_NAME" || true
echo
echo "Enabled. It now survives reboots and network changes."
echo "  logs:      journalctl -u $UNIT_NAME -f"
echo "  uninstall: sudo $0 --uninstall"
