# Security FAQ: known attack vectors and how to protect against them

This document lists Drawbridge's known application-layer weaknesses in plain
terms — what an unauthenticated attacker on the provisioning network can
actually do, why some of these can't be fixed in code, and what an operator
should do about each. It's a companion to [decisions.md](decisions.md) (the
reasoning behind each tradeoff) and [beta.md](../beta.md) (which of these are
being worked on) — this doc is the "what does it mean for me" version.

**Read this before you upload a real config file.** Several of the answers
below come back to the same root cause: files served through Drawbridge for
device provisioning are deliberately unauthenticated, because a device has no
credentials to present before it's been provisioned. **Never upload a
config file containing anything you wouldn't want an anonymous party on the
provisioning network to read — SNMP community strings, RADIUS/TACACS+
shared secrets, routing-protocol keys, VPN pre-shared keys, local account
passwords — unless you are confident the provisioning network is physically
isolated and access-controlled** (see "How do I actually protect my
deployment?" below). Drawbridge cannot tell a sensitive config from an
innocuous one; that judgment call is the operator's.

## Can someone find out which of my devices are registered?

**Yes, and this can't be fixed in Drawbridge's code.** `GET
/api/v1/provision-request?serial=<X>` ([drawbridge/api/leases.py](../drawbridge/api/leases.py))
is intentionally open — a device calling it has no credential yet, only a
serial number, which isn't a secret (it's printed on the chassis). Anyone
who can reach this endpoint can try serials and read registered-vs-not from
the 200-vs-404 response, with no rate limit today.

Why not add a shared secret/token to close this? Because it would have to
cross the wire during the device's *first* contact with the network — the
same conversation an eavesdropper already watches to read the serial. There
is no way to tell "the legitimate device" apart from "anyone who can also
see or join this conversation" at that point, so a bolted-on token is
exactly as observable/replayable as the serial itself. The only mechanism
that actually breaks this requires a device secret that predates network
contact entirely (Cisco SUDI / IEEE 802.1AR IDevID, proven via RFC 8572's
Ownership Vouchers and MASA) — which Drawbridge deliberately doesn't use;
see [decisions.md](decisions.md) ("No sZTP").

**Mitigation:** network isolation (see below). This is a structural property 
of Classic ZTP which cannot be prevented at the application layer.

## Can someone download the actual config/image intended for a device?

**Yes, though it's no longer a cold, unauthenticated fetch for images/configs.**
`GET /files/images/<filename>` and `GET /files/configs/<filename>` in
[drawbridge/api/files.py](../drawbridge/api/files.py) now require the
caller's IP to match an active `ProvisioningSession` — a third party who has
never called `provision-request` gets a `403 no_active_session`. But
`provision-request` itself is still open (see the enumeration question
above), so an attacker who knows a serial can call it themselves from their
own IP, then immediately pull the image/config from that same IP — this
closes the "cold fetch by anyone, no session at all" gap and the
first-claim-wins race it made worse, but it doesn't close the race itself
(see [beta.md](../beta.md) §7). The ZTP script itself isn't served through
this Flask app at all — it's a single fixed file, bind-mounted into the
separate `drawbridge-bootstrap` container, served unauthenticated over
plain HTTP on `:8090` (see [deployment.md](deployment.md) and [decisions.md](decisions.md),
"HTTPS cert trust on C9200CX"). That fetch happens via DHCP Option 67 before
any serial is known, so gating it isn't possible without breaking the boot
bootstrap itself; this is fine since it carries nothing device- or
config-specific and is identical for every device.

This means the actual startup-config content is still exposed to whoever
wins that race, not just its existence. If a device relies on
`DEFAULT_CONFIG_FILE` (no device-specific config assigned), one successful
pull exposes the fleet-wide baseline config shared by every device using the
default — worse than a single device's leak.

**Mitigation:** treat every file uploaded through Drawbridge's config/image
management as reachable by anyone who can reach the network, full stop.
Don't put credentials, community strings, or keys into a config file at all
if you can avoid it (push those out-of-band, or rotate them post-provisioning
via a separate authenticated mechanism). If you can't avoid it, this is the
strongest argument in this whole document for network isolation being
non-negotiable in practice.

## Can someone fake a provisioning result or mess with the audit log?

**Yes.** `POST/PUT /api/v1/provision-complete` ([drawbridge/api/leases.py](../drawbridge/api/leases.py))
requires an active `ProvisioningSession` for the serial *and* a matching
caller IP (a `409 session_mismatch` otherwise) — but an attacker can satisfy
both by calling `provision-request` themselves first, from the same IP
they'll complete from. From there, `event`, `detail`, `image`, and
`config_file` are all attacker-supplied and written directly into the
permanent `ProvisioningLog`. An attacker can mark a device "successfully
provisioned" when it never was, or plant a misleading failure `detail` to
send operators chasing a phantom problem. `mac` is now pinned and compared
on repeat `provision-request` calls for the same serial (a mismatched claim
is rejected), but it's still self-reported by whoever calls the endpoint
first, so it's not a trustworthy forensic signal for *who* did this — don't
rely on it to identify an attacker after the fact.

This call also deletes the `ProvisioningSession` — so firing it while a real
device is mid-provisioning terminates that device's session early, and its
genuine completion report later gets a `404 device_not_active` (or, if the
attacker's IP doesn't match the real device's, a `409 session_mismatch`).

**Mitigation:** none available at the application layer today (see
[beta.md](../beta.md) for tracked improvements — rate limiting and
non-silent audit logging help detect this, not prevent it). Treat
`ProvisioningLog` entries as advisory, not as forensic proof, until this is
hardened. Network isolation is again the actual control.

## Is my password stored securely?

**Yes, on the backend.** Passwords are hashed with Werkzeug's
`generate_password_hash`/`check_password_hash`, pinned explicitly to
`method='scrypt'` (salted; see [authentication.md](authentication.md)) —
never stored or logged in plaintext. A minimum length of 8 characters is
enforced on claim/reset-password/change-password. The one place plaintext
transiently exists is the bootstrap admin password (env var or
generated-and-printed), and both of those sources force a password reset on
first login specifically to bound that exposure — see "Bootstrap admin
password sources" in authentication.md. Prefer a systemd credential
(`CREDENTIALS_DIRECTORY`) over `ADMIN_PASSWORD` where possible; see
[deployment.md](deployment.md).

Account-claiming is no longer a bare race either: `POST /api/auth/claim`
requires a single-use `claim_token` minted at account creation (or by an
admin-triggered reset), closing alpha's "first `POST` with a known username
wins" tradeoff. `login`/`claim`/`reset-password`/`change-password` are also
rate-limited per-IP, and every failed attempt logs a username + event line
(never the password) — see authentication.md for details.

On the frontend, the password lives only in page memory during login/claim/
reset/change-password calls — never written to `localStorage` or logged.

## Is my session cookie safe in transit?

**Yes.** Drawbridge terminates its own TLS by default (self-signed cert,
always on — see [deployment.md](deployment.md) "TLS"), and
`SESSION_COOKIE_SECURE`/`SESSION_COOKIE_SAMESITE` are set in
`drawbridge/main.py` accordingly, so the browser refuses to send the session
cookie over plain HTTP. The one exception is `TLS_DISABLED=1`, a local-dev-only
escape hatch that also relaxes the cookie flag to match — never set in a
deployed/Quadlet config (see deployment.md).

## Is Drawbridge as secure as Cisco Secure ZTP (RFC 8572)?

**No, and it isn't trying to be — that's the explicit tradeoff.** Secure ZTP
solves the phone-home trust problem properly (hardware-rooted device
identity, Ownership Vouchers, a MASA), but requires trusting Cisco's PKI/
cloud infrastructure as a third party, which is incompatible with a fully
airgapped deployment. Drawbridge hardens Classic ZTP instead: serial
allowlisting, HTTPS transport, and payload hash verification (the last one
still pending — see beta.md). All of it assumes the provisioning network
itself is the trust boundary. If your deployment can't guarantee that
boundary, Drawbridge is the wrong tool — Secure ZTP (accepting the Cisco PKI
dependency) or a fully manual provisioning process would be more appropriate.

## How do I actually protect my deployment?

Everything above comes back to the same answer: Drawbridge's guarantees
(allowlist, HTTPS, hash verification) protect **what** gets provisioned and
**to whom** it's addressed — none of them protect against a party that's
already on the provisioning network. That has to be closed outside the
application:

- No routing between the provisioning VLAN and any untrusted network.
- Port security / 802.1X on switch ports serving the VLAN.
- DHCP snooping and dynamic ARP inspection.
- No hubs, unmanaged switches, or mirrored/monitor ports on the segment.
- A TLS-terminating reverse proxy in front of Drawbridge if it's reachable
  from anywhere outside that isolated segment.
- Never upload a config file with real secrets in it unless you've verified
  the above — see the config/image question above.

See [deployment.md](deployment.md) ("Network isolation — strongly
recommended") for the full writeup. Drawbridge will run without any of this
in place — it just won't be protecting what you probably think it is.
