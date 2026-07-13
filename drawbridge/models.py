from datetime import datetime, timezone

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
    image: Mapped[str | None]
    config_file: Mapped[str | None]
    script: Mapped[str | None]
    added_at: Mapped[str] = mapped_column(default=utcnow_iso)
    added_by: Mapped[str | None]

    def as_dict(self) -> dict:
        return {
            'serial': self.serial,
            'mac': self.mac,
            'description': self.description,
            'image': self.image,
            'config_file': self.config_file,
            'script': self.script,
            'added_at': self.added_at,
            'added_by': self.added_by,
        }


class ProvisioningSession(Base):
    """Transient record of an in-progress ZTP run. Created when
    /api/v1/provision-request approves a serial; deleted when
    /api/v1/provision-complete fires (success or failure). The Device
    allowlist row is not touched."""
    __tablename__ = 'provisioning_sessions'

    serial: Mapped[str] = mapped_column(primary_key=True)
    mac: Mapped[str | None]
    ip: Mapped[str | None]
    image: Mapped[str | None]        # set when device reports at provision-complete
    config_file: Mapped[str | None]  # set when device reports at provision-complete
    state: Mapped[str]               # 'lease_approved', 'script_fetched', 'downloading',
                                     # 'updating_software', 'rebooting', 'configuring'
    approved_at: Mapped[str] = mapped_column(default=utcnow_iso)

    def as_dict(self) -> dict:
        return {
            'serial': self.serial,
            'mac': self.mac,
            'ip': self.ip,
            'image': self.image,
            'config_file': self.config_file,
            'state': self.state,
            'approved_at': self.approved_at,
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
    timestamp: Mapped[str] = mapped_column(default=utcnow_iso)
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

    file_type:   Mapped[str] = mapped_column(primary_key=True)  # 'image', 'config', 'script'
    filename:    Mapped[str] = mapped_column(primary_key=True)
    size_bytes:  Mapped[int]
    sha256:      Mapped[str]
    uploaded_at: Mapped[str] = mapped_column(default=utcnow_iso)
    uploaded_by: Mapped[str | None]

    def as_dict(self) -> dict:
        return {
            'file_type': self.file_type,
            'filename': self.filename,
            'size_bytes': self.size_bytes,
            'sha256': self.sha256,
            'uploaded_at': self.uploaded_at,
            'uploaded_by': self.uploaded_by,
        }


class User(Base, UserMixin):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(unique=True)
    email: Mapped[str | None]
    password_hash: Mapped[str | None]   # null for SAML-only operators
    role: Mapped[str]                   # 'admin' or 'operator'
    auth_source: Mapped[str]            # 'local' or 'saml'
    saml_issuer: Mapped[str | None]     # IdP entity ID, set once SAML lands
    saml_subject: Mapped[str | None]    # IdP NameID, set once SAML lands
    is_active: Mapped[bool] = mapped_column(default=True)
    must_reset_password: Mapped[bool] = mapped_column(default=False)  # see docs/authentication.md
    created_at: Mapped[str] = mapped_column(default=utcnow_iso)
    last_login_at: Mapped[str | None]
