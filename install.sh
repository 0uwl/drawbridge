#!/usr/bin/env bash
# Installs podman and Kea on an Ubuntu/Debian provisioning host (amd64 or
# arm64, e.g. Raspberry Pi OS) if not already present, installs Drawbridge's
# kea/*.conf into /etc/kea, installs the Quadlet unit for the invoking
# (non-root) user, and pulls the published container image. Does not start
# the Drawbridge container itself — SECRET_KEY and the host data directories
# still need setting up by hand; see the "Done" message this script prints
# and docs/deployment.md.
#
# Runnable standalone (curl -fsSL .../install.sh | sudo bash) as well as from
# a repo checkout — when kea/*.conf isn't found next to this script (piped
# runs have no sibling files), it and quadlet/drawbridge.container are
# fetched from RAW_BASE into a temp dir.
set -euo pipefail

# kea-ctrl-agent's package asks a debconf question (API password) on install;
# without a noninteractive frontend, apt-get hangs/fails waiting on a
# terminal that isn't there for a piped `curl | sudo bash` run (or any other
# unattended invocation). DEBIAN_FRONTEND=noninteractive answers every
# debconf prompt with its declared default instead of showing a dialog.
export DEBIAN_FRONTEND=noninteractive

IMAGE="ghcr.io/0uwl/drawbridge:latest"
# Pinned to this branch because kea/*.conf isn't on main yet; repoint at
# main (and the README's curl one-liner) once this branch merges.
RAW_BASE="https://raw.githubusercontent.com/0uwl/drawbridge/main"
KEA_DHCP4_SERVICE="kea-dhcp4-server"
KEA_CTRL_AGENT_SERVICE="kea-ctrl-agent"

script_source="${BASH_SOURCE[0]:-}"
if [ -n "$script_source" ] && script_dir="$(cd "$(dirname "$script_source")" >/dev/null 2>&1 && pwd)" && [ -f "$script_dir/kea/kea-dhcp4.conf" ]; then
    kea_conf_dir="$script_dir/kea"
    quadlet_src="$script_dir/quadlet/drawbridge.container"
else
    if ! command -v curl >/dev/null 2>&1; then
        echo "install.sh needs curl to fetch kea/*.conf and quadlet/drawbridge.container when run without a repo checkout" >&2
        exit 1
    fi
    kea_conf_dir="$(mktemp -d)"
    trap 'rm -rf "$kea_conf_dir"' EXIT
    echo "==> Fetching kea/*.conf and quadlet/drawbridge.container from $RAW_BASE"
    curl -fsSL "$RAW_BASE/kea/kea-dhcp4.conf" -o "$kea_conf_dir/kea-dhcp4.conf"
    curl -fsSL "$RAW_BASE/kea/kea-ctrl-agent.conf" -o "$kea_conf_dir/kea-ctrl-agent.conf"
    curl -fsSL "$RAW_BASE/quadlet/drawbridge.container" -o "$kea_conf_dir/drawbridge.container"
    quadlet_src="$kea_conf_dir/drawbridge.container"
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "install.sh must be run as root (installs packages, writes /etc/kea)" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "install.sh only supports Ubuntu/Debian hosts (needs apt-get)" >&2
    exit 1
fi

apt_updated=0
apt_update_once() {
    if [ "$apt_updated" -eq 0 ]; then
        apt-get update
        apt_updated=1
    fi
}

if ! command -v podman >/dev/null 2>&1; then
    echo "==> Installing podman"
    apt_update_once
    apt-get install -y podman
else
    echo "==> podman already installed ($(podman --version))"
fi

if ! command -v kea-dhcp4 >/dev/null 2>&1; then
    echo "==> Installing Kea"
    apt_update_once
    # kea-ctrl-agent/kea-ctrl-agent.conf ships no API auth on purpose - the
    # Control Agent is loopback-only (127.0.0.1:8081, see docs/kea.md) and
    # used solely for local kea-shell diagnostics, never called by
    # Drawbridge itself. "unconfigured" here matches that: skip the
    # package's own optional API password setup rather than silently
    # picking one.
    echo "kea-ctrl-agent kea-ctrl-agent/make_a_choice select unconfigured" | debconf-set-selections
    apt-get install -y kea-dhcp4-server kea-ctrl-agent
else
    echo "==> Kea already installed ($(kea-dhcp4 -V))"
fi

# kea-dhcp4.conf ships with a placeholder interface name (eth1) that won't
# match every host - e.g. Raspberry Pi OS names its wired port eth0. Detect
# and fix this in the local copy *before* it's installed to /etc/kea, rather
# than installing a config nobody edited and letting kea-dhcp4-server fail
# to start. KEA_INTERFACE overrides detection entirely, for scripted/
# non-interactive deployments that already know the right name.
interface=$(sed -n 's/.*"interfaces": \["\([^"]*\)"\].*/\1/p' "$kea_conf_dir/kea-dhcp4.conf" | head -n1)
if [ -n "$interface" ] && ! ip link show "$interface" >/dev/null 2>&1; then
    candidates=()
    for iface_path in /sys/class/net/*; do
        candidate="$(basename "$iface_path")"
        # Skip loopback, wireless (ZTP provisioning is wired-only - see
        # docs/architecture.md), and common virtual/container interfaces
        # that also report as type 1 (ARPHRD_ETHER) but are never the
        # intended provisioning-VLAN port.
        [ "$candidate" = "lo" ] && continue
        [ -e "$iface_path/wireless" ] && continue
        [ -e "$iface_path/phy80211" ] && continue
        [ "$(cat "$iface_path/type" 2>/dev/null)" = "1" ] || continue
        case "$candidate" in
            veth*|docker*|br-*|virbr*|podman*|cni*|tap*|tun*|wg*|dummy*|bond*) continue ;;
        esac
        candidates+=("$candidate")
    done

    if [ -n "${KEA_INTERFACE:-}" ]; then
        resolved_interface="$KEA_INTERFACE"
    elif [ "${#candidates[@]}" -eq 1 ]; then
        resolved_interface="${candidates[0]}"
        echo "==> kea-dhcp4.conf targets interface '$interface', which doesn't exist on this host - using the only Ethernet interface found: '$resolved_interface'"
    elif [ "${#candidates[@]}" -gt 1 ] && [ -r /dev/tty ]; then
        echo "==> kea-dhcp4.conf targets interface '$interface', which doesn't exist on this host."
        echo "    Available Ethernet interfaces:"
        i=1
        for candidate in "${candidates[@]}"; do
            echo "      $i) $candidate"
            i=$((i + 1))
        done
        choice_num=""
        read -r -p "    Which interface should kea-dhcp4-server listen on? [1-${#candidates[@]}] " choice_num < /dev/tty
        case "$choice_num" in
            ''|*[!0-9]*) echo "install.sh: '$choice_num' is not a valid choice" >&2; exit 1 ;;
        esac
        if [ "$choice_num" -lt 1 ] || [ "$choice_num" -gt "${#candidates[@]}" ]; then
            echo "install.sh: '$choice_num' is not a valid choice" >&2
            exit 1
        fi
        resolved_interface="${candidates[$((choice_num - 1))]}"
    else
        echo "==> ERROR: kea-dhcp4.conf targets interface '$interface', which doesn't exist on this host, and the right interface can't be determined automatically." >&2
        if [ "${#candidates[@]}" -eq 0 ]; then
            echo "    No Ethernet interfaces were found on this host." >&2
        else
            echo "    Candidates found: ${candidates[*]}" >&2
        fi
        echo "    Re-run install.sh interactively to choose one, or re-run with KEA_INTERFACE=<name> set." >&2
        exit 1
    fi

    if ! ip link show "$resolved_interface" >/dev/null 2>&1; then
        echo "install.sh: interface '$resolved_interface' does not exist on this host" >&2
        exit 1
    fi

    echo "==> Updating kea-dhcp4.conf: interface '$interface' -> '$resolved_interface'"
    sed -i "s/\"interfaces\": \[\"$interface\"\]/\"interfaces\": [\"$resolved_interface\"]/" "$kea_conf_dir/kea-dhcp4.conf"
fi

echo "==> Installing Kea configuration"
install -d -m 755 /etc/kea
for conf in kea-dhcp4.conf kea-ctrl-agent.conf; do
    if [ -f "/etc/kea/$conf" ] && ! cmp -s "$kea_conf_dir/$conf" "/etc/kea/$conf"; then
        cp "/etc/kea/$conf" "/etc/kea/$conf.bak.$(date +%Y%m%d%H%M%S)"
    fi
    install -m 644 "$kea_conf_dir/$conf" "/etc/kea/$conf"
done

kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
kea-ctrl-agent -t /etc/kea/kea-ctrl-agent.conf

echo "==> Enabling and restarting Kea services"
systemctl enable --now "$KEA_DHCP4_SERVICE" "$KEA_CTRL_AGENT_SERVICE"
systemctl restart "$KEA_DHCP4_SERVICE" "$KEA_CTRL_AGENT_SERVICE"

echo "==> Pulling $IMAGE"
podman pull "$IMAGE"

# The Quadlet unit runs as a rootless container under the invoking user's
# own systemd --user instance (see docs/deployment.md), not root — installed
# into that user's $HOME, not root's, even though this script itself runs as
# root. Only written if missing so a previously-edited unit (SECRET_KEY, etc.)
# is never clobbered by a re-run.
target_user="${SUDO_USER:-$(id -un)}"
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
quadlet_dir="$target_home/.config/containers/systemd"
quadlet_dest="$quadlet_dir/drawbridge.container"

if [ -f "$quadlet_dest" ] && cmp -s "$quadlet_src" "$quadlet_dest"; then
    echo "==> Quadlet unit at $quadlet_dest already matches, nothing to do"
elif [ -f "$quadlet_dest" ]; then
    echo "==> Quadlet unit already exists at $quadlet_dest and differs from the version being installed (possibly a new required variable — see docs/deployment.md)"
    backup_answer="y"
    if [ -r /dev/tty ]; then
        read -r -p "    Back up the existing unit before overwriting? [Y/n] " backup_answer < /dev/tty || backup_answer="y"
    fi
    case "$backup_answer" in
        [nN]*) ;;
        *)
            backup="$quadlet_dest.bak.$(date +%Y%m%d%H%M%S)"
            cp -p "$quadlet_dest" "$backup"
            echo "    Backed up existing unit to $backup"
            ;;
    esac
    install -m 644 -o "$target_user" -g "$target_user" "$quadlet_src" "$quadlet_dest"
    echo "==> Installed Quadlet unit to $quadlet_dest — reapply any custom values (SECRET_KEY, etc.) from your backup"
else
    echo "==> Installing Quadlet unit to $quadlet_dest"
    install -d -m 755 -o "$target_user" -g "$target_user" "$quadlet_dir"
    install -m 644 -o "$target_user" -g "$target_user" "$quadlet_src" "$quadlet_dest"
fi

echo "==> Done. Before starting the container as $target_user:"
echo "      - edit SECRET_KEY (and ADMIN_PASSWORD or LoadCredential=) in $quadlet_dest"
echo "      - mkdir -p $target_home/.local/share/drawbridge/{data,files}"
echo "      - systemctl --user daemon-reload && systemctl --user start drawbridge"
echo "    See docs/deployment.md for details."
