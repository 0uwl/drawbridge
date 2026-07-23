#!/usr/bin/env bash
# Installs podman and Kea on an Ubuntu/Debian provisioning host (amd64 or
# arm64, e.g. Raspberry Pi OS) if not already present, installs Drawbridge's
# kea/*.conf into /etc/kea, installs the three Quadlet unit files (the pod
# plus its two member containers) for the invoking user, and pulls the two
# published container images. Does not start the Drawbridge pod itself —
# SECRET_KEY and the host data directories still need setting up by hand;
# see the "Done" message this script prints and docs/deployment.md.
#
# Run as your normal (non-root) user, NOT via sudo/as root - this script
# calls sudo itself for the handful of commands that actually need root
# (installing packages, writing /etc/kea, managing the two Kea system
# services). Everything else (podman pull, the Quadlet unit under
# ~/.config) runs as you, so it lands in the right place with the right
# ownership without any extra detection/chown work. One upfront `sudo -v`
# below gets the password prompt out of the way; individual `sudo` calls
# after that reuse the cached credential rather than re-prompting.
#
# Runnable standalone (curl -fsSL .../install.sh | bash) as well as from a
# repo checkout — when kea/*.conf isn't found next to this script (piped
# runs have no sibling files), it and the Quadlet unit files are fetched
# from RAW_BASE into a temp dir.
set -euo pipefail

# Overridable so a curl-piped run can target a specific branch (or, once
# releases exist, a tag) instead of main - e.g.
#   curl -fsSL https://raw.githubusercontent.com/0uwl/drawbridge/v.0.3.0/install.sh | DRAWBRIDGE_REF=v.0.3.0 bash
# DRAWBRIDGE_REF has to go on the bash side of the pipe, not the curl side -
# `VAR=val cmd1 | cmd2` only exports VAR into cmd1's environment (here,
# curl, which doesn't read it), not cmd2's (bash, which does; this is what
# actually reads and executes the piped install.sh). Has to be named in
# both the curl URL (to fetch install.sh itself from the right place) and
# here (so install.sh's own fetches of kea/*.conf and quadlet/* match, and
# so the image tag pulled below matches too) - a piped script can't
# introspect the URL it was downloaded from, so there's no way to specify
# it only once. See docs/decisions.md for the tradeoff this doesn't solve
# (main itself is still a moving target).
DRAWBRIDGE_REF="${DRAWBRIDGE_REF:-main}"
RAW_BASE="https://raw.githubusercontent.com/0uwl/drawbridge/$DRAWBRIDGE_REF"

# Same tag scheme .github/workflows/ci.yml's publish job uses: main -> latest
# (existing installs pulling :latest shouldn't change meaning), every other
# ref -> tagged with its own name (e.g. development -> :development). Keeps
# this in sync with DRAWBRIDGE_REF so a testing-branch install actually
# pulls that branch's image instead of always landing on :latest regardless
# of ref.
if [ "$DRAWBRIDGE_REF" = "main" ]; then
    IMAGE_TAG="latest"
else
    IMAGE_TAG="$DRAWBRIDGE_REF"
fi
IMAGE="ghcr.io/0uwl/drawbridge:$IMAGE_TAG"
RSYSLOG_IMAGE="ghcr.io/0uwl/drawbridge-rsyslog:$IMAGE_TAG"
# Pod unit first: drawbridge.container's Pod= reference means Quadlet needs
# it present at daemon-reload time, though install order here doesn't
# actually matter (all three land before the daemon-reload this script
# tells the operator to run at the end).
QUADLET_FILES=(drawbridge.pod drawbridge.container drawbridge-rsyslog.container)
KEA_DHCP4_SERVICE="kea-dhcp4-server"
KEA_CTRL_AGENT_SERVICE="kea-ctrl-agent"

script_source="${BASH_SOURCE[0]:-}"
if [ -n "$script_source" ] && script_dir="$(cd "$(dirname "$script_source")" >/dev/null 2>&1 && pwd)" && [ -f "$script_dir/kea/kea-dhcp4.conf" ]; then
    kea_conf_dir="$script_dir/kea"
    quadlet_src_dir="$script_dir/quadlet"
else
    if ! command -v curl >/dev/null 2>&1; then
        echo "install.sh needs curl to fetch kea/*.conf and the quadlet/ unit files when run without a repo checkout" >&2
        exit 1
    fi
    kea_conf_dir="$(mktemp -d)"
    trap 'rm -rf "$kea_conf_dir"' EXIT
    echo "==> Fetching kea/*.conf and quadlet/ unit files from $RAW_BASE"
    curl -fsSL "$RAW_BASE/kea/kea-dhcp4.conf" -o "$kea_conf_dir/kea-dhcp4.conf"
    curl -fsSL "$RAW_BASE/kea/kea-ctrl-agent.conf" -o "$kea_conf_dir/kea-ctrl-agent.conf"
    for f in "${QUADLET_FILES[@]}"; do
        curl -fsSL "$RAW_BASE/quadlet/$f" -o "$kea_conf_dir/$f"
    done
    quadlet_src_dir="$kea_conf_dir"
fi

if [ "$(id -u)" -eq 0 ]; then
    echo "install.sh should be run as your normal (non-root) user, not as root or via sudo - it calls sudo itself for the commands that need it. Re-run as: bash install.sh (or curl ... | bash)" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "install.sh only supports Ubuntu/Debian hosts (needs apt-get)" >&2
    exit 1
fi

if ! sudo -v; then
    echo "install.sh needs sudo access to install packages and write system config (/etc/kea, systemd units)" >&2
    exit 1
fi

apt_updated=0
apt_update_once() {
    if [ "$apt_updated" -eq 0 ]; then
        sudo apt-get update
        apt_updated=1
    fi
}

if ! command -v podman >/dev/null 2>&1; then
    echo "==> Installing podman"
    apt_update_once
    sudo apt-get install -y podman
else
    echo "==> podman already installed ($(podman --version))"
fi

# quadlet/drawbridge.pod (the .pod unit itself, and the Pod= key that
# drawbridge.container/drawbridge-rsyslog.container use to join it) needs
# Podman 5.0+ — both landed together in that release. Checked unconditionally
# here, not just in the "already installed" branch above, since apt-get's
# default repo can still hand back something older than 5.0 (e.g. Ubuntu
# 24.04 LTS ships 4.9.3) even on a fresh install. See docs/deployment.md,
# "Container", for how to get a newer Podman where the default repo is behind.
podman_version="$(podman --version | awk '{print $NF}')"
podman_major="${podman_version%%.*}"
if [ "$podman_major" -lt 5 ]; then
    echo "install.sh: found podman $podman_version, but Drawbridge's pod (quadlet/drawbridge.pod) needs Podman 5.0+ — .pod Quadlet units and the Pod= key aren't supported before that. See docs/deployment.md, \"Container\", for how to get a newer version on a distro whose default repo is behind." >&2
    exit 1
fi

if ! command -v kea-dhcp4 >/dev/null 2>&1; then
    echo "==> Installing Kea"
    apt_update_once
    # kea-ctrl-agent/kea-ctrl-agent.conf ships no API auth on purpose - the
    # Control Agent is loopback-only (127.0.0.1:8081, see docs/kea.md) and
    # used solely for local kea-shell diagnostics, never called by
    # Drawbridge itself. "unconfigured" here matches that: skip the
    # package's own optional API password setup rather than silently
    # picking one. The package's systemd unit still requires a non-empty
    # /etc/kea/kea-api-password to start regardless (see below) - that's
    # handled separately from this debconf choice, on purpose, so it isn't
    # tied to package-version-specific debconf/postinst behavior.
    echo "kea-ctrl-agent kea-ctrl-agent/make_a_choice select unconfigured" | sudo debconf-set-selections
    # sudo resets the environment by default, so DEBIAN_FRONTEND has to be
    # passed on the sudo command line itself (not just exported above) to
    # actually reach apt-get - without it, apt-get would try to show the
    # debconf question as a dialog despite the preseed above, and hang/fail
    # with no terminal attached to answer it (a piped `curl | bash` run, or
    # any other unattended invocation).
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y kea-dhcp4-server kea-ctrl-agent
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
sudo install -d -m 755 /etc/kea
for conf in kea-dhcp4.conf kea-ctrl-agent.conf; do
    if [ -f "/etc/kea/$conf" ] && ! cmp -s "$kea_conf_dir/$conf" "/etc/kea/$conf"; then
        sudo cp "/etc/kea/$conf" "/etc/kea/$conf.bak.$(date +%Y%m%d%H%M%S)"
    fi
    sudo install -m 644 "$kea_conf_dir/$conf" "/etc/kea/$conf"
done

# kea-ctrl-agent's packaged systemd unit has
# ConditionFileNotEmpty=/etc/kea/kea-api-password and just silently skips
# starting (not even a failure exit) if that file is missing/empty -
# choosing "unconfigured" above means the package's own postinst never
# creates it. kea-ctrl-agent.conf has no "authentication" stanza, so Kea
# itself never actually reads or enforces this file as a real credential;
# it exists purely to satisfy the systemd condition. Generated once here,
# not tied to the apt-get install block above, so re-running install.sh
# against an already-installed Kea (which skips that block entirely) still
# fixes a host stuck in this state.
kea_api_password_file=/etc/kea/kea-api-password
if [ ! -s "$kea_api_password_file" ]; then
    echo "==> Generating $kea_api_password_file (required by kea-ctrl-agent's systemd unit)"
    # `sudo cmd > file` wouldn't work here - the redirection is set up by
    # this (non-root) shell before sudo ever runs, so it can't open a
    # root-owned path for writing. `sudo tee` does the write itself, as root.
    head -c 32 /dev/urandom | base64 | tr -d '\n' | sudo tee "$kea_api_password_file" >/dev/null
    sudo chmod 0640 "$kea_api_password_file"
    sudo chgrp _kea "$kea_api_password_file"
fi

kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
kea-ctrl-agent -t /etc/kea/kea-ctrl-agent.conf

echo "==> Enabling and restarting Kea services"
sudo systemctl enable --now "$KEA_DHCP4_SERVICE" "$KEA_CTRL_AGENT_SERVICE"
sudo systemctl restart "$KEA_DHCP4_SERVICE" "$KEA_CTRL_AGENT_SERVICE"

echo "==> Pulling $IMAGE and $RSYSLOG_IMAGE"
# Deliberately not sudo'd - the Quadlet units below run as rootless
# containers under your own systemd --user instance (see
# docs/deployment.md), reading from your own rootless podman storage
# (~/.local/share/containers/storage), not root's. A pull done as root
# would land in root's storage instead, invisible to that user instance -
# it'd just get re-pulled on first start anyway, making the root pull here
# pointless as well as wrong.
podman pull "$IMAGE"
podman pull "$RSYSLOG_IMAGE"

# The Quadlet units run as rootless containers under your own systemd
# --user instance (see docs/deployment.md), installed into your own $HOME.
# Since this script itself runs as you (not root - see the check near the
# top), $HOME and file ownership are correct here with no extra detection
# or chown step needed. Only written if missing so a previously-edited unit
# (SECRET_KEY, etc.) is never clobbered by a re-run.
quadlet_dir="$HOME/.config/containers/systemd"
mkdir -p "$quadlet_dir"

for f in "${QUADLET_FILES[@]}"; do
    quadlet_src="$quadlet_src_dir/$f"
    quadlet_dest="$quadlet_dir/$f"

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
        install -m 644 "$quadlet_src" "$quadlet_dest"
        echo "==> Installed Quadlet unit to $quadlet_dest — reapply any custom values (SECRET_KEY, etc.) from your backup"
    else
        echo "==> Installing Quadlet unit to $quadlet_dest"
        install -m 644 "$quadlet_src" "$quadlet_dest"
    fi
done

echo "==> Done. Before starting the pod:"
echo "      - edit SECRET_KEY (and ADMIN_PASSWORD or LoadCredential=) in $quadlet_dir/drawbridge.container"
echo "      - mkdir -p $HOME/.local/share/drawbridge/{data,files}"
echo "      - systemctl --user daemon-reload && systemctl --user start drawbridge-pod.service"
echo "    See docs/deployment.md for details."
