#!/usr/bin/env bash
# Removes every trace of a Drawbridge install: stops and removes the pod
# and its containers, deletes the Quadlet unit files, deletes the data
# directory (SQLite DB, TLS cert/key, uploaded images/configs), and
# removes the Kea configuration install.sh deployed. Leaves podman, Kea's
# packages, and the four pulled container images alone — re-running
# install.sh afterwards is a genuinely fresh install with nothing carried
# over from whatever version was here before. Meant to be run before every
# reinstall when testing a new version, not just once.
#
# Run as your normal (non-root) user, NOT via sudo/as root - same posture
# as install.sh: this script calls sudo itself for the one thing that needs
# it (/etc/kea). Everything else is your own rootless podman/systemd --user
# state under $HOME, and lands/leaves correctly owned without extra
# detection or chown work.
#
# Usage: ./uninstall.sh [-y|--yes] [--remove-kea-config]
#   -y, --yes             skip the confirmation prompt for the
#                         Drawbridge-only parts (pod, Quadlet units, data/
#                         files dirs) - for scripted use, e.g. always
#                         running this right before install.sh in an
#                         upgrade-testing loop. Never implies
#                         --remove-kea-config (see below) - that one always
#                         needs its own explicit opt-in.
#   --remove-kea-config   also remove /etc/kea/{kea-dhcp4,kea-ctrl-agent}.conf
#                         and kea-api-password. Off by default and asked
#                         about separately even when -y is given: Kea is a
#                         general-purpose DHCP server, and unlike the
#                         Drawbridge-only paths above, this host's Kea
#                         config might not be exclusively Drawbridge's to
#                         remove. Without this flag (and without an
#                         interactive terminal to ask on), /etc/kea is left
#                         alone entirely.
set -euo pipefail

assume_yes=0
remove_kea_config=0
for arg in "$@"; do
    case "$arg" in
        -y|--yes) assume_yes=1 ;;
        --remove-kea-config) remove_kea_config=1 ;;
        *) echo "uninstall.sh: unknown argument '$arg'" >&2; exit 1 ;;
    esac
done

if [ "$(id -u)" -eq 0 ]; then
    echo "uninstall.sh should be run as your normal (non-root) user, not as root or via sudo - it calls sudo itself for the commands that need it. Re-run as: bash uninstall.sh" >&2
    exit 1
fi

quadlet_dir="$HOME/.config/containers/systemd"
quadlet_container_file="$quadlet_dir/drawbridge.container"
QUADLET_FILES=(drawbridge.pod drawbridge.container drawbridge-rsyslog.container drawbridge-bootstrap.container drawbridge-nginx.container)
KEA_CONF_FILES=(/etc/kea/kea-dhcp4.conf /etc/kea/kea-ctrl-agent.conf /etc/kea/kea-api-password /etc/rsyslog.d/49-drawbridge-kea.conf)

# Volume=%h/.local/share/drawbridge/data:/app/data:Z is editable - an
# operator can point /app/data and/or /app/files at wherever they want (see
# the comment above that line in quadlet/drawbridge.container). Reading the
# *installed* unit's actual Volume= lines, rather than assuming the stock
# default, is the only way to find the real host paths in that case. Falls
# back to the documented default only when the unit is already gone (e.g.
# a previous partial cleanup) or doesn't declare that mount at all.
quadlet_volume_host_path() {
    # $1: quadlet file to read, $2: container-side mount path to match.
    # Prints the corresponding host-side path (Quadlet's %h specifier
    # expanded to $HOME, same as Quadlet itself would - this file is read
    # directly here, not through the Quadlet generator, so nothing else
    # expands it) and returns 0; returns 1 if no matching Volume= line
    # exists in the file at all.
    local line rest host_path mount_path
    while IFS= read -r line; do
        case "$line" in
            Volume=*)
                line="${line#Volume=}"
                host_path="${line%%:*}"
                rest="${line#*:}"
                mount_path="${rest%%:*}"
                if [ "$mount_path" = "$2" ]; then
                    echo "${host_path//%h/$HOME}"
                    return 0
                fi
                ;;
        esac
    done < "$1"
    return 1
}

app_data_dir="$HOME/.local/share/drawbridge/data"
app_files_dir="$HOME/.local/share/drawbridge/files"
if [ -f "$quadlet_container_file" ]; then
    # Not `resolved=... && app_data_dir=...` - under set -e, a legitimate
    # "no matching Volume= line" (exit 1) would abort the whole script
    # right here instead of just falling back to the default above.
    if resolved="$(quadlet_volume_host_path "$quadlet_container_file" /app/data)"; then
        app_data_dir="$resolved"
    fi
    if resolved="$(quadlet_volume_host_path "$quadlet_container_file" /app/files)"; then
        app_files_dir="$resolved"
    fi
fi

kea_config_found=0
for f in "${KEA_CONF_FILES[@]}"; do
    if [ -f "$f" ]; then
        kea_config_found=1
    fi
done

echo "This will permanently remove:"
echo "  - the running Drawbridge pod and its containers (not the pulled images)"
for f in "${QUADLET_FILES[@]}"; do
    if [ -f "$quadlet_dir/$f" ]; then
        echo "  - $quadlet_dir/$f"
    fi
done
if [ -d "$app_data_dir" ]; then
    echo "  - $app_data_dir (database, TLS cert/key)"
fi
if [ -d "$app_files_dir" ]; then
    echo "  - $app_files_dir (uploaded images/configs)"
fi
echo
if [ "$kea_config_found" -eq 1 ]; then
    echo "Kea configuration was also found under /etc/kea - you'll be asked"
    echo "about that separately below, since it might not be exclusively"
    echo "Drawbridge's to remove."
    echo
fi
echo "Left alone: podman, the Kea packages, and the pulled"
echo "ghcr.io/0uwl/drawbridge:latest / ghcr.io/0uwl/drawbridge-rsyslog:latest /"
echo "ghcr.io/0uwl/drawbridge-bootstrap:latest / ghcr.io/0uwl/drawbridge-nginx:latest images."
echo "Backup files (*.bak.*) from previous installs/upgrades are left alone too -"
echo "remove those by hand if you don't want them."
echo

if [ "$assume_yes" -ne 1 ]; then
    confirm="n"
    if [ -r /dev/tty ]; then
        read -r -p "Proceed? [y/N] " confirm < /dev/tty
    fi
    case "$confirm" in
        [yY]*) ;;
        *) echo "Aborted, nothing was removed."; exit 0 ;;
    esac
fi

echo "==> Stopping and removing the Drawbridge pod"
# Covers the normal case (pod started via the Quadlet-generated
# drawbridge-pod.service, per install.sh's own instructions). daemon-reload
# further down removes the generated units entirely once their source
# Quadlet files are gone, so disabling here is about a clean stop this
# session, not something that needs to survive that reload.
systemctl --user disable --now drawbridge-pod.service >/dev/null 2>&1 || true
systemctl --user reset-failed drawbridge-pod.service drawbridge.service drawbridge-rsyslog.service drawbridge-bootstrap.service drawbridge-nginx.service >/dev/null 2>&1 || true
# Belt-and-suspenders: clean up by name too, in case the pod was ever
# started by hand (podman pod create/run) instead of via the unit above.
# Only ever removes the pod/containers, never an image - no -i/--rmi flag
# anywhere in this script.
podman pod rm -f drawbridge >/dev/null 2>&1 || true
podman rm -f drawbridge drawbridge-rsyslog drawbridge-bootstrap drawbridge-nginx >/dev/null 2>&1 || true

echo "==> Removing Quadlet unit files"
for f in "${QUADLET_FILES[@]}"; do
    rm -f "$quadlet_dir/$f"
done
systemctl --user daemon-reload

echo "==> Removing $app_data_dir"
rm -rf "$app_data_dir"
echo "==> Removing $app_files_dir"
rm -rf "$app_files_dir"

kea_config_removed=0
if [ "$kea_config_found" -eq 1 ]; then
    if [ "$remove_kea_config" -ne 1 ] && [ "$assume_yes" -ne 1 ]; then
        echo
        echo "Found Kea configuration installed by install.sh:"
        for f in "${KEA_CONF_FILES[@]}"; do
            if [ -f "$f" ]; then
                echo "  - $f"
            fi
        done
        kea_confirm="n"
        if [ -r /dev/tty ]; then
            read -r -p "This may not be exclusively Drawbridge's - remove it too? [y/N] " kea_confirm < /dev/tty
        fi
        case "$kea_confirm" in
            [yY]*) remove_kea_config=1 ;;
        esac
    fi

    if [ "$remove_kea_config" -eq 1 ]; then
        echo "==> Removing Kea configuration installed by install.sh"
        # Stopped, not disabled - install.sh re-enables (systemctl enable
        # --now) on the next install regardless, and leaving enablement
        # alone keeps this script's blast radius limited to what it just
        # deleted, not systemd's separate enabled/disabled bookkeeping.
        sudo systemctl stop kea-dhcp4-server kea-ctrl-agent 2>/dev/null || true
        sudo rm -f "${KEA_CONF_FILES[@]}"
        sudo systemctl reload-or-restart rsyslog 2>/dev/null || true
        kea_config_removed=1
    else
        echo "==> Leaving /etc/kea alone (pass --remove-kea-config to also remove it non-interactively)"
    fi
fi

echo "==> Done. Removed the Drawbridge pod/containers, Quadlet units,"
echo "    $app_data_dir, and $app_files_dir."
if [ "$kea_config_removed" -eq 1 ]; then
    echo "    Also removed the Kea config under /etc/kea."
elif [ "$kea_config_found" -eq 1 ]; then
    echo "    Left /etc/kea alone, as requested."
fi
echo "    Left alone: podman, the Kea packages, the pulled images, and any"
echo "    *.bak.* backup files. Run install.sh for a fresh install."
