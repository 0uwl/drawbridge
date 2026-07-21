#!/usr/bin/env bash
# Installs podman and Kea on an Ubuntu provisioning host if not already
# present, installs Drawbridge's kea/*.conf into /etc/kea, installs the
# Quadlet unit for the invoking (non-root) user, and pulls the published
# container image. Does not start the Drawbridge container itself — SECRET_KEY
# and the host data directories still need setting up by hand; see the "Done"
# message this script prints and docs/deployment.md.
#
# Runnable standalone (curl -fsSL .../install.sh | sudo bash) as well as from
# a repo checkout — when kea/*.conf isn't found next to this script (piped
# runs have no sibling files), it and quadlet/drawbridge.container are
# fetched from RAW_BASE into a temp dir.
set -euo pipefail

IMAGE="ghcr.io/0uwl/drawbridge:latest"
# Pinned to this branch because kea/*.conf isn't on main yet; repoint at
# main (and the README's curl one-liner) once this branch merges.
RAW_BASE="https://raw.githubusercontent.com/0uwl/drawbridge/main"
KEA_DHCP4_SERVICE="kea-dhcp4-server"
KEA_CTRL_AGENT_SERVICE="kea-ctrl-agent"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
if [ -f "$script_dir/kea/kea-dhcp4.conf" ]; then
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
    apt-get install -y kea-dhcp4-server kea-ctrl-agent
else
    echo "==> Kea already installed ($(kea-dhcp4 -V))"
fi

echo "==> Installing Kea configuration"
install -d -m 755 /etc/kea
for conf in kea-dhcp4.conf kea-ctrl-agent.conf; do
    if [ -f "/etc/kea/$conf" ] && ! cmp -s "$kea_conf_dir/$conf" "/etc/kea/$conf"; then
        cp "/etc/kea/$conf" "/etc/kea/$conf.bak.$(date +%Y%m%d%H%M%S)"
    fi
    install -m 644 "$kea_conf_dir/$conf" "/etc/kea/$conf"
done

interface=$(sed -n 's/.*"interfaces": \["\([^"]*\)"\].*/\1/p' "$kea_conf_dir/kea-dhcp4.conf" | head -n1)
if [ -n "$interface" ] && ! ip link show "$interface" >/dev/null 2>&1; then
    echo "==> WARNING: kea/kea-dhcp4.conf targets interface '$interface', which does not exist on this host — kea-dhcp4-server will fail to start until interfaces-config is updated" >&2
fi

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
