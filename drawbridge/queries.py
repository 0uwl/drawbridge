"""Query helpers for the models in drawbridge/models.py, called from the API
blueprints.

These functions only stage changes (add/delete) on the Session passed in —
they don't commit. Committing is the caller's responsibility, so a route
that needs several of these in one transaction (e.g. /api/provision-request
checking a device then creating a ProvisioningSession row) can do so
atomically.
"""
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from drawbridge.models import Device, DeviceLogEntry, KeaLogEntry, ProvisioningSession, ProvisioningLog, Setting, User, ZTPFile, utcnow_iso

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
    added_by: str | None = None,
) -> Device:
    """Idempotent on serial: re-registering an existing serial updates its
    mutable fields instead of raising on the primary-key collision.

    image and config_file fall back to their default_* Setting rows on
    creation only — re-registration leaves them unchanged if not explicitly
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
        return device

    if image is None:
        setting = get_setting(session, 'default_image')
        if setting is not None:
            image = setting.value
    if config_file is None:
        setting = get_setting(session, 'default_config_file')
        if setting is not None:
            config_file = setting.value

    device = Device(serial=serial, mac=mac, description=description, image=image, config_file=config_file, added_by=added_by)
    session.add(device)
    return device


def update_device(
    session: Session,
    serial: str,
    *,
    mac: str | None = None,
    description: str | None = None,
    image: str | None = None,
    config_file: str | None = None,
) -> Device | None:
    """Edits an existing allowlist entry in place. Returns None (does not
    create) when the serial doesn't already exist — unlike add_device's
    re-registration path, an admin editing a typo'd serial should 404, not
    silently create a new device."""
    device = session.get(Device, serial)
    if device is None:
        return None

    device.mac = mac
    device.description = description
    device.image = image
    device.config_file = config_file
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


def update_session_state(session: Session, *, serial: str, state: str) -> ProvisioningSession | None:
    ps = get_provisioning_session(session, serial)
    if ps is None:
        return None
    ps.state = state
    return ps


def touch_session(session: Session, *, serial: str) -> None:
    """Bumps last_seen_at to now for the session matching serial, a no-op
    if none exists. Called on every correlated /device-logs POST regardless
    of whether the message matches a device_events trigger — any log line
    at all is evidence the device is still alive, not just ones that
    happen to produce a state change (see docs/database.md, "Stale
    sessions")."""
    ps = get_provisioning_session(session, serial)
    if ps is not None:
        ps.last_seen_at = utcnow_iso()


def find_active_session_by_ip(session: Session, ip: str) -> ProvisioningSession | None:
    """Best-effort match of a syslog line's source IP to the ProvisioningSession
    that pinned it (see beta.md §7's pin-on-first-use model). The in-flight
    session table is small (a handful of concurrent ZTP runs at most), so a
    linear scan needs no new index."""
    return session.scalar(select(ProvisioningSession).where(ProvisioningSession.ip == ip))


def create_provisioning_session(
    session: Session,
    *,
    serial: str,
    mac: str | None = None,
    ip: str | None = None,
    image: str | None = None,
    config_file: str | None = None,
) -> ProvisioningSession | None:
    """Idempotent on serial: a device may hit /api/provision-request more
    than once per boot cycle. A repeat call for an existing serial is only
    honored if it doesn't contradict what's already pinned — mac/ip are
    only compared when both the stored value and the new value are present,
    so a call that hasn't learned a field yet can fill it in later, and a
    call that omits a field isn't treated as a claim to compare (see
    beta.md §7). Returns None when a repeat call conflicts with the pinned
    mac or ip; the caller should treat that as a rejection, not an
    overwrite. image/config_file are the device's assigned values (from its
    Device row) at approval time, not a report of what it actually applied
    — see ProvisioningSession's docstring — and are refreshed on every
    non-conflicting call regardless of mac/ip. A repeat call also counts as
    device activity, so it bumps last_seen_at (see docs/database.md,
    "Stale sessions")."""
    ps = session.get(ProvisioningSession, serial)
    if ps is not None:
        if ps.mac is not None and mac is not None and ps.mac != mac:
            return None
        if ps.ip is not None and ip is not None and ps.ip != ip:
            return None
        if mac is not None:
            ps.mac = mac
        if ip is not None:
            ps.ip = ip
        ps.image = image
        ps.config_file = config_file
        ps.last_seen_at = utcnow_iso()
        return ps
    ps = ProvisioningSession(
        serial=serial, mac=mac, ip=ip, image=image, config_file=config_file, state='lease_approved',
    )
    session.add(ps)
    return ps


def update_session_facts(session: Session, *, serial: str, model: str | None = None, version: str | None = None) -> ProvisioningSession | None:
    """Records the device-reported model/version onto an already-active
    session (see PUT /provision-request/facts in leases.py) - a session must
    already exist, this never creates one. A None field is left as-is rather
    than overwritten, same "don't treat an omitted field as a claim" posture
    as create_provisioning_session's mac/ip handling. Counts as device
    activity like touch_session above."""
    ps = get_provisioning_session(session, serial)
    if ps is None:
        return None
    if model is not None:
        ps.model = model
    if version is not None:
        ps.version = version
    ps.last_seen_at = utcnow_iso()
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
    until the user claims it via POST /api/auth/claim, using the claim_token
    generated here (see docs/authentication.md)."""
    user = User(username=username, role=role, auth_source='local', claim_token=secrets.token_urlsafe(32))
    session.add(user)
    return user


def get_or_create_saml_user(
    session: Session, *, issuer: str, subject: str, attributes: dict | None = None, role: str = 'operator',
) -> User:
    """Upserts a User keyed on (saml_issuer, saml_subject) — the IdP-issued
    identity, not username, since SAML accounts self-provision on first
    assertion instead of being admin-created like local ones. New accounts
    default to 'operator'; SAML carries no group-to-role mapping (out of
    scope — see beta.md section 4, "generic SP side only")."""
    user = session.scalar(select(User).where(User.saml_issuer == issuer, User.saml_subject == subject))
    if user is not None:
        return user

    email = None
    if attributes:
        values = attributes.get('email') or attributes.get('emailAddress')
        if values:
            email = values[0]

    user = User(
        username=f'saml:{subject}',
        email=email,
        role=role,
        auth_source='saml',
        saml_issuer=issuer,
        saml_subject=subject,
    )
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


def clear_user_password(session: Session, user: User) -> None:
    """Admin-triggered reset: nulls password_hash and issues a fresh
    claim_token so the account re-enters the same POST /auth/claim flow as a
    newly-created account. Does not call /auth/reset-password's logic —
    that route requires checking a current_password against password_hash,
    which is impossible once the hash is nulled."""
    user.password_hash = None
    user.claim_token = secrets.token_urlsafe(32)


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


def update_file_hash(session: Session, file_type: str, filename: str, sha256: str) -> ZTPFile | None:
    f = session.get(ZTPFile, (file_type, filename))
    if f is None:
        return None
    f.sha256 = sha256
    return f


def list_provisioning_log(session: Session) -> list[ProvisioningLog]:
    """Rows already reflect the retention window — add_log_entry purges
    expired rows lazily on insert (see docs/database.md), so this just
    reads what's currently present, most recent first."""
    return list(session.scalars(select(ProvisioningLog).order_by(ProvisioningLog.timestamp.desc())).all())


def purge_expired_logs(session: Session, retention_days: str) -> None:
    """Deletes ProvisioningLog rows older than retention_days. A no-op when
    retention is the literal string 'indefinite' (see docs/database.md,
    "Log Retention & Data Minimisation")."""
    if retention_days == 'indefinite':
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat(timespec='microseconds')
    session.execute(delete(ProvisioningLog).where(ProvisioningLog.timestamp < cutoff))


# DeviceLogEntry queries

def add_device_log_entry(
    session: Session,
    *,
    serial: str | None = None,
    source: str,
    message: str,
) -> DeviceLogEntry:
    """Writes one DeviceLogEntry row, purging expired rows first per the
    same lazy-retention policy add_log_entry uses (shared log_retention_days
    setting — see docs/database.md)."""
    retention = get_setting(session, 'log_retention_days')
    if retention is not None:
        purge_expired_device_logs(session, retention.value)

    entry = DeviceLogEntry(serial=serial, source=source, message=message)
    session.add(entry)
    return entry


def list_device_logs(session: Session, serial: str | None = None, after_id: int | None = None) -> list[DeviceLogEntry]:
    """after_id lets a poller fetch only rows newer than the last one it
    already has, instead of re-fetching (and the frontend re-rendering) the
    whole growing list on every poll — see docs/api.md."""
    stmt = select(DeviceLogEntry).order_by(DeviceLogEntry.timestamp.desc())
    if serial is not None:
        stmt = stmt.where(DeviceLogEntry.serial == serial)
    if after_id is not None:
        stmt = stmt.where(DeviceLogEntry.id > after_id)
    return list(session.scalars(stmt).all())


def purge_expired_device_logs(session: Session, retention_days: str) -> None:
    """Deletes DeviceLogEntry rows older than retention_days. A no-op when
    retention is the literal string 'indefinite' (see purge_expired_logs)."""
    if retention_days == 'indefinite':
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat(timespec='microseconds')
    session.execute(delete(DeviceLogEntry).where(DeviceLogEntry.timestamp < cutoff))


def delete_device_logs_by_serial(session: Session, serial: str) -> None:
    """Deletes all DeviceLogEntry rows for one serial, regardless of age —
    called on a successful provision_complete (see leases.py). A
    successfully-provisioned device's raw log stream was only ever useful
    for watching that run in progress; it doesn't need to wait out
    log_retention_days the way a failure's does (see docs/database.md)."""
    session.execute(delete(DeviceLogEntry).where(DeviceLogEntry.serial == serial))


# KeaLogEntry queries

def add_kea_log_entry(session: Session, *, message: str) -> KeaLogEntry:
    """Writes one KeaLogEntry row, purging expired rows first per the same
    lazy-retention policy add_device_log_entry uses (shared
    log_retention_days setting — see docs/database.md)."""
    retention = get_setting(session, 'log_retention_days')
    if retention is not None:
        purge_expired_kea_logs(session, retention.value)

    entry = KeaLogEntry(message=message)
    session.add(entry)
    return entry


def list_kea_logs(session: Session, after_id: int | None = None) -> list[KeaLogEntry]:
    """after_id lets a poller fetch only rows newer than the last one it
    already has — same pattern as list_device_logs (see docs/api.md)."""
    stmt = select(KeaLogEntry).order_by(KeaLogEntry.timestamp.desc())
    if after_id is not None:
        stmt = stmt.where(KeaLogEntry.id > after_id)
    return list(session.scalars(stmt).all())


def purge_expired_kea_logs(session: Session, retention_days: str) -> None:
    """Deletes KeaLogEntry rows older than retention_days. A no-op when
    retention is the literal string 'indefinite' (see purge_expired_logs)."""
    if retention_days == 'indefinite':
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat(timespec='microseconds')
    session.execute(delete(KeaLogEntry).where(KeaLogEntry.timestamp < cutoff))
