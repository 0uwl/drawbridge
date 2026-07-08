# Kea Configuration

Drawbridge does not include an installation of Kea DHCP server. It assumes
you have an Ubuntu Server with Kea installed locally. This repository
includes example configurations for the DHCP server so that you can get the
Drawbridge service up and running as quickly as possible.

Kea's config here is intentionally vanilla — no custom hooks, no client
classification, no host reservations. Every device gets a normal lease and
the same Option 67 pointer at the ZTP script; the allow/deny decision
happens in the script itself, which phones home to Drawbridge with its own
serial before doing anything real. See [decisions.md](decisions.md) for why
(a native Kea hook was built and found broken — see
[kea-hook-findings.md](kea-hook-findings.md) — and dragged in a hard
PostgreSQL requirement for Kea's own hosts-database that this design avoids
entirely).

**DHCPv4** (`kea/kea-dhcp4.conf`):
- Provisioning subnet: `192.168.100.0/24`, pool `192.168.100.10–200`
- Option 67 (`boot-file-name`) set unconditionally to
  `http://<host-ip>:8080/scripts/ztp-base.py` for every client
- No `hooks-libraries`, no `host_cmds`, no `hosts-database` — Kea never
  talks to Drawbridge or vice versa

**Control Agent** (`kea/kea-ctrl-agent.conf`):
- Listens on `127.0.0.1:8081` — loopback only, not exposed externally
- Kept purely for operator diagnostics (`kea-shell`, `lease4-get-all`,
  `config-get`, etc.) — Drawbridge itself never calls it; nothing in the
  provisioning flow depends on it being up
