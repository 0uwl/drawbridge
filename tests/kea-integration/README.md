# Kea container-based testing (design only, not built)

`tests/test_kea_config.py` covers everything about `kea/*.conf` that's
testable without a running Kea process: JSON validity, and the naming/host/
port contract between `kea-dhcp4.conf`'s `boot-file-name` option and
`scripts/ztp-base.py` / the app's serving port. That test runs in the
default `pytest` suite.

What it *can't* catch: whether Kea itself accepts the config as
semantically valid, and whether Kea's client-classification engine actually
applies the `cisco-devices`/`known-network-vendor` rules the way the config
intends. Both require a real `kea-dhcp4` process. Per `alpha.md`, a live
Kea instance is explicitly **not a gate for alpha sign-off** — these two
tiers are opt-in, manual, and not wired into `pytest` or CI. Build them in
a later phase once real hardware testing starts.

This is a much smaller scope than the superseded harness referenced in
`docs/kea-hook-plan.md` (multi-stage build compiling a custom native hook
against `isc-kea-dev`, plus a PostgreSQL hosts-database) — since
`docs/decisions.md` abandoned the native-hook approach, there's no hook to
compile and no Postgres dependency here. This only needs vanilla
`kea-dhcp4-server`.

## Tier 1 — config-syntax validation (one-shot, no persistent process)

Runs Kea's own config validator. Catches semantic errors (bad option
codes, malformed subnet/pool) that a plain JSON parse can't.

```bash
podman build -t localhost/kea-test -f tests/kea-integration/Containerfile.kea .
podman run --rm -v ./kea:/etc/kea:Z localhost/kea-test \
  kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
```

`Containerfile.kea` (not yet written) would just install
`kea-dhcp4-server` from ISC's package repo on a minimal Debian base — no
build stage needed.

## Tier 2 — live DHCP contract test (real Kea process + simulated client)

Proves Kea's classification engine actually hands out Option 67 for a
Cisco-presenting client — the one thing Tier 1 and the static tests can't
verify, since classification only runs during real DHCP packet processing.

1. `podman network create --subnet 192.168.100.0/24 kea-test-net`
2. `podman run -d --network kea-test-net --ip 192.168.100.1 --name kea-test -v ./kea:/etc/kea:Z localhost/kea-test kea-dhcp4 -c /etc/kea/kea-dhcp4.conf`
3. A small `scapy`-based script crafts a DHCPDISCOVER with Option 60 =
   `"Cisco IOS-XE"`, sends it on `kea-test-net`, captures the DHCPOFFER, and
   asserts Option 67 equals the `boot-file-name` string from
   `kea/kea-dhcp4.conf`.

`scapy` is a new dependency scoped only to this path (e.g. a `kea-test`
extras group in `pyproject.toml`) — not added to the core dependencies.

`kea-ctrl-agent` is deliberately left out of both tiers: it's
diagnostics-only and uninvolved in the DHCP path (see
`kea/kea-ctrl-agent.conf`), so including it would only add a second service
to a container that should stay single-purpose.
