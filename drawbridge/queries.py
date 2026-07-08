"""Query helpers for the models in drawbridge/models.py, called from the API
blueprints.

These functions only stage changes (add/delete) on the Session passed in —
they don't commit. Committing is the caller's responsibility, so a route
that needs several of these in one transaction (e.g. /api/provision-request
checking a device then creating a ProvisioningSession row) can do so
atomically.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from drawbridge.models import Device, ProvisioningSession, ProvisioningLog, Setting, User, ZTPFile, utcnow_iso

# Device queries

def get_device(session: Session, serial: str) -> Device | None:
    return session.get(Device, serial)


def list_devices(session: Session) -> list[Device]:
    return list(session.scalars(select(Device).order_by(Device.added_at)).all())


def add_device(
    session: Session,
    *,
    serial: str,
    mac: str | None = None,
    description: str | None = None,
    image: str | None = None,
    config_file: str | None = None,
    script: str | None = None,
    added_by: str | None = None,
) -> Device:
    """Idempotent on serial: re-registering an existing serial updates its
    mutable fields instead of raising on the primary-key collision.

    image, config_file, and script fall back to their default_* Setting rows
    on creation only — re-registration leaves them unchanged if not explicitly
    provided."""
    device = session.get(Device, serial)
    if device is not None:
        device.mac = mac
        device.description = description
        device.added_by = added_by
        if image is not None:
            device.image = image
        if config_file is not None:
            device.config_file = config_file
        if script is not None:
            device.script = script
        return device

    if image is None:
        setting = get_setting(session, 'default_image')
        if setting is not None:
            image = setting.value
    if config_file is None:
        setting = get_setting(session, 'default_config_file')
        if setting is not None:
            config_file = setting.value
    if script is None:
        setting = get_setting(session, 'default_script')
        if setting is not None:
            script = setting.value

    device = Device(serial=serial, mac=mac, description=description, image=image, config_file=config_file, script=script, added_by=added_by)
    session.add(device)
    return device


def delete_device(session: Session, serial: str) -> bool:
    device = session.get(Device, serial)
    if device is None:
        return False
    session.delete(device)
    return True


# ProvisioningSession queries

def list_sessions(session: Session) -> list[ProvisioningSession]:
    return list(session.scalars(select(ProvisioningSession).order_by(ProvisioningSession.approved_at)).all())


def get_provisioning_session(session: Session, serial: str) -> ProvisioningSession | None:
    return session.get(ProvisioningSession, serial)


def create_provisioning_session(
    session: Session,
    *,
    serial: str,
    mac: str | None = None,
    ip: str | None = None,
) -> ProvisioningSession:
    """Idempotent on serial: a device may hit /api/provision-request more
    than once per boot cycle. Re-approving updates mac/ip rather than
    raising on the primary-key collision."""
    ps = session.get(ProvisioningSession, serial)
    if ps is not None:
        ps.mac = mac
        ps.ip = ip
        return ps
    ps = ProvisioningSession(serial=serial, mac=mac, ip=ip, state='lease_approved')
    session.add(ps)
    return ps


def delete_provisioning_session(session: Session, serial: str) -> bool:
    ps = session.get(ProvisioningSession, serial)
    if ps is None:
        return False
    session.delete(ps)
    return True


# User queries

def get_user_by_username(session: Session, username: str) -> User | None:
    return session.scalar(select(User).where(User.username == username))


def get_user_by_id(session: Session, user_id: int) -> User | None:
    return session.get(User, user_id)


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.username)).all())


def count_admins(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(User).where(User.role == 'admin'))


def create_user(session: Session, *, username: str, role: str) -> User:
    """Admin-created account: no password set yet — password_hash stays NULL
    until the user claims it via POST /api/auth/claim (see
    docs/authentication.md)."""
    user = User(username=username, role=role, auth_source='local')
    session.add(user)
    return user


def delete_user(session: Session, user_id: int) -> User | None:
    """Returns the deleted User (detached, caller's job to check role/admin
    guards before calling) or None if not found."""
    user = session.get(User, user_id)
    if user is None:
        return None
    session.delete(user)
    return user


# Setting queries

def get_setting(session: Session, key: str) -> Setting | None:
    return session.get(Setting, key)


def set_setting(session: Session, key: str, value: str, updated_by: str | None = None) -> Setting:
    setting = session.get(Setting, key)
    if setting is None:
        setting = Setting(key=key, value=value, updated_by=updated_by)
        session.add(setting)
    else:
        setting.value = value
        setting.updated_by = updated_by
        setting.updated_at = utcnow_iso()
    return setting


# ProvisioningLog queries

def add_log_entry(
    session: Session,
    *,
    serial: str,
    event: str,
    ip: str | None = None,
    image: str | None = None,
    config_file: str | None = None,
    detail: str | None = None,
) -> ProvisioningLog:
    """Writes one ProvisioningLog row, purging expired rows first per the
    lazy-retention policy in docs/database.md."""
    retention = get_setting(session, 'log_retention_days')
    if retention is not None:
        purge_expired_logs(session, retention.value)

    entry = ProvisioningLog(
        serial=serial,
        event=event,
        ip=ip,
        image=image,
        config_file=config_file,
        detail=detail,
    )
    session.add(entry)
    return entry


# ZTPFile queries

def get_file(session: Session, file_type: str, filename: str) -> ZTPFile | None:
    return session.get(ZTPFile, (file_type, filename))


def list_files(session: Session, file_type: str) -> list[ZTPFile]:
    return list(session.scalars(
        select(ZTPFile)
        .where(ZTPFile.file_type == file_type)
        .order_by(ZTPFile.uploaded_at)
    ).all())


def add_file(
    session: Session,
    *,
    file_type: str,
    filename: str,
    size_bytes: int,
    sha256: str,
    uploaded_by: str | None = None,
) -> ZTPFile:
    f = ZTPFile(file_type=file_type, filename=filename, size_bytes=size_bytes, sha256=sha256, uploaded_by=uploaded_by)
    session.add(f)
    return f


def delete_file(session: Session, file_type: str, filename: str) -> bool:
    f = session.get(ZTPFile, (file_type, filename))
    if f is None:
        return False
    session.delete(f)
    return True


def purge_expired_logs(session: Session, retention_days: str) -> None:
    """Deletes ProvisioningLog rows older than retention_days. A no-op when
    retention is the literal string 'indefinite' (see docs/database.md,
    "Log Retention & Data Minimisation")."""
    if retention_days == 'indefinite':
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat(timespec='microseconds')
    session.execute(delete(ProvisioningLog).where(ProvisioningLog.timestamp < cutoff))
