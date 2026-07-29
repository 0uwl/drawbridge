from datetime import datetime, timedelta, timezone

from flask_login import UserMixin
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def utcnow_iso() -> str:
    """Fixed-width (timespec='microseconds') so timestamp columns remain
    lexicographically sortable — relied on by the log-retention purge query
    in queries.py, which compares ts strings directly rather than parsing
    them."""
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


class Device(Base):
    """Operator-managed allowlist entry. Exists from registration through the
    full ZTP lifecycle — not deleted on provisioning so a failed run can be
    retried without re-registration. Operator must explicitly DELETE to remove."""
    __tablename__ = 'devices'

    serial: Mapped[str] = mapped_column(primary_key=True)
    mac: Mapped[str | None]
    description: Mapped[str | None]
    # Desired software version, e.g. '17.9.1'. Resolved to an image filename
    # at /api/v1/provision-request time via a ZTPFile lookup (version ->
    # image is 1:1, enforced at upload) rather than storing a filename
    # directly here — see docs/decisions.md, "Version-based image mapping".
    version: Mapped[str | None]
    config_file: Mapped[str | None]
    added_at: Mapped[str] = mapped_column(default=utcnow_iso)
    added_by: Mapped[str | None]

    def as_dict(self) -> dict:
        return {
            'serial': self.serial,
            'mac': self.mac,
            'description': self.description,
            'version': self.version,
            'config_file': self.config_file,
            'added_at': self.added_at,
            'added_by': self.added_by,
        }


PROVISIONING_STATES = frozenset({
    'lease_approved', 'script_fetched', 'downloading',
    'updating_software', 'rebooting', 'configuring', 'error',
})

# A safety margin against a stuck session (device crashed/pulled mid-install),
# not an operator-tunable policy like log_retention_days — no per-site
# variability rationale for making this a Setting, so it stays a constant
# until something actually needs it configurable. See v0-3-0.md, "Addendum".
SESSION_STALE_AFTER_MINUTES = 60


class ProvisioningSession(Base):
    """Transient record of an in-progress ZTP run. Created when
    /api/v1/provision-request approves a serial; deleted when
    /api/v1/provision-complete fires (success or failure), or when an
    operator cancels a stale session (see is_stale() below). The Device
    allowlist row is not touched."""
    __tablename__ = 'provisioning_sessions'

    serial: Mapped[str] = mapped_column(primary_key=True)
    mac: Mapped[str | None]
    ip: Mapped[str | None]
    image: Mapped[str | None]        # copied from the Device row's assignment at approval time
    config_file: Mapped[str | None]  # copied from the Device row's assignment at approval time
    # Self-reported by the ZTP script via PUT /provision-request/facts, once
    # its own network I/O is available (unlike the initial GET
    # /provision-request, which is limited to query-string params - see
    # docs/decisions.md, "Facts-first provisioning"). Transient like the rest
    # of this row: never copied into ProvisioningLog, gone as soon as the
    # session is - not retained the way Device's allowlist fields are.
    model: Mapped[str | None]
    version: Mapped[str | None]
    state: Mapped[str]               # one of PROVISIONING_STATES above
    approved_at: Mapped[str] = mapped_column(default=utcnow_iso)
    # Distinct from approved_at (set once, at creation): bumped on every
    # repeat /provision-request call and every correlated /device-logs POST,
    # so a long-running-but-active install doesn't look stale just because
    # it was approved over an hour ago.
    last_seen_at: Mapped[str] = mapped_column(default=utcnow_iso)

    def is_stale(self) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=SESSION_STALE_AFTER_MINUTES)).isoformat(timespec='microseconds')
        return self.last_seen_at < cutoff

    def as_dict(self) -> dict:
        return {
            'serial': self.serial,
            'mac': self.mac,
            'ip': self.ip,
            'image': self.image,
            'config_file': self.config_file,
            'model': self.model,
            'version': self.version,
            'state': self.state,
            'approved_at': self.approved_at,
            'last_seen_at': self.last_seen_at,
            'stale': self.is_stale(),
        }


class ProvisioningLog(Base):
    """Archival record written when a device completes or fails provisioning
    and its ProvisioningSession row is deleted. Subject to the retention policy in
    Setting; purged once a row outlives it."""
    __tablename__ = 'provisioning_log'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    serial: Mapped[str]
    event: Mapped[str]               # 'provision_complete', 'provision_failed'
    image: Mapped[str | None]
    config_file: Mapped[str | None]
    ip: Mapped[str | None]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso, index=True)  # purge_expired_logs range-scans this
    detail: Mapped[str | None]

    def as_dict(self) -> dict:
        return {
            'id': self.id,
            'serial': self.serial,
            'event': self.event,
            'image': self.image,
            'config_file': self.config_file,
            'ip': self.ip,
            'timestamp': self.timestamp,
            'detail': self.detail,
        }


class DeviceLogEntry(Base):
    """Live log feed from ZTP devices during provisioning — both the
    script's own structured events (source='script') and raw syslog
    forwarded by the device (source='syslog'). Unrelated to ProvisioningLog
    (that's a provisioning-outcome audit trail); this is a raw log stream.
    Subject to the same retention policy as ProvisioningLog."""
    __tablename__ = 'device_logs'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    serial: Mapped[str | None]  # nullable — syslog may arrive before a serial is matched
    source: Mapped[str]         # 'script' | 'syslog'
    message: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso, index=True)  # purge_expired_device_logs range-scans this

    def as_dict(self) -> dict:
        return {
            'id': self.id,
            'serial': self.serial,
            'source': self.source,
            'message': self.message,
            'timestamp': self.timestamp,
        }


class KeaLogEntry(Base):
    """Kea's own DHCP server logs (lease grants, config errors — not
    device-authored content), forwarded via a second drawbridge-rsyslog
    listener (:10515, separate from device syslog's :10514 — see
    docs/logging.md). No serial/device correlation exists for these lines,
    unlike DeviceLogEntry — this is host-daemon operational log, not
    per-device content. Subject to the same retention policy as
    ProvisioningLog/DeviceLogEntry."""
    __tablename__ = 'kea_logs'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message: Mapped[str]
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso, index=True)  # purge_expired_kea_logs range-scans this

    def as_dict(self) -> dict:
        return {
            'id': self.id,
            'message': self.message,
            'timestamp': self.timestamp,
        }


class Setting(Base):
    """Small admin-configurable key/value store. First row of interest:
    key='log_retention_days', value='30' (or 'indefinite')."""
    __tablename__ = 'settings'

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str]
    updated_at: Mapped[str] = mapped_column(default=utcnow_iso)
    updated_by: Mapped[str | None]


class ZTPFile(Base):
    __tablename__ = 'ztp_files'

    file_type:   Mapped[str] = mapped_column(primary_key=True)  # 'image', 'config'
    filename:    Mapped[str] = mapped_column(primary_key=True)
    size_bytes:  Mapped[int]
    sha256:      Mapped[str]
    # Software version this image corresponds to (e.g. '17.9.1') - only ever
    # set for file_type='image', always None for 'config'. Parsed from the
    # filename at upload time, or supplied manually when parsing fails.
    # Enforced 1:1 with version at upload (see drawbridge/api/files.py) so a
    # Device.version can always be resolved to exactly one image.
    version:     Mapped[str | None]
    uploaded_at: Mapped[str] = mapped_column(default=utcnow_iso)
    uploaded_by: Mapped[str | None]

    def as_dict(self) -> dict:
        return {
            'file_type': self.file_type,
            'filename': self.filename,
            'size_bytes': self.size_bytes,
            'sha256': self.sha256,
            'version': self.version,
            'uploaded_at': self.uploaded_at,
            'uploaded_by': self.uploaded_by,
        }


class User(Base, UserMixin):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str | None]
    password_hash: Mapped[str | None]   # null for SAML-only operators
    claim_token: Mapped[str | None]     # single-use token gating POST /auth/claim, nulled once claimed
    role: Mapped[str]                   # 'admin' or 'operator'
    auth_source: Mapped[str]            # 'local' or 'saml'
    saml_issuer: Mapped[str | None]     # IdP entity ID, set once SAML lands
    saml_subject: Mapped[str | None]    # IdP NameID, set once SAML lands
    is_active: Mapped[bool] = mapped_column(default=True)
    must_reset_password: Mapped[bool] = mapped_column(default=False)  # see docs/authentication.md
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    last_login_at: Mapped[str | None]
