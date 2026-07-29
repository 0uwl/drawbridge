from datetime import datetime, timedelta, timezone

from drawbridge import queries
from drawbridge.models import DeviceLogEntry, KeaLogEntry, ProvisioningLog, ProvisioningSession, Setting


# add_device

def test_add_device_creates_a_new_device(session):
    queries.add_device(session, serial='SN1', mac='aa:bb', description='switch', added_by='admin')
    session.commit()

    device = queries.get_device(session, 'SN1')
    assert device is not None
    assert device.mac == 'aa:bb'
    assert [d.serial for d in queries.list_devices(session)] == ['SN1']


def test_add_device_is_idempotent_on_serial(session):
    queries.add_device(session, serial='SN1', mac='aa:bb', description='first')
    session.commit()

    queries.add_device(session, serial='SN1', mac='cc:dd', description='second')
    session.commit()

    devices = queries.list_devices(session)
    assert len(devices) == 1
    assert devices[0].mac == 'cc:dd'
    assert devices[0].description == 'second'


def test_add_device_stores_version_and_config_file(session):
    queries.add_device(session, serial='SN1', version='17.9.1', config_file='spine.cfg')
    session.commit()

    device = queries.get_device(session, 'SN1')
    assert device.version == '17.9.1'
    assert device.config_file == 'spine.cfg'


def test_add_device_uses_default_version_from_setting(session):
    session.add(Setting(key='default_version', value='17.9.1'))
    session.commit()

    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.get_device(session, 'SN1').version == '17.9.1'


def test_add_device_uses_default_config_file_from_setting(session):
    session.add(Setting(key='default_config_file', value='default.cfg'))
    session.commit()

    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.get_device(session, 'SN1').config_file == 'default.cfg'


def test_add_device_explicit_version_overrides_default(session):
    session.add(Setting(key='default_version', value='17.9.1'))
    session.commit()

    queries.add_device(session, serial='SN1', version='17.12.1')
    session.commit()

    assert queries.get_device(session, 'SN1').version == '17.12.1'


def test_add_device_reregistration_preserves_version_when_not_provided(session):
    queries.add_device(session, serial='SN1', version='17.9.1')
    session.commit()

    queries.add_device(session, serial='SN1', mac='aa:bb')
    session.commit()

    assert queries.get_device(session, 'SN1').version == '17.9.1'


def test_add_device_reregistration_updates_version_when_provided(session):
    queries.add_device(session, serial='SN1', version='17.9.1')
    session.commit()

    queries.add_device(session, serial='SN1', version='17.12.1')
    session.commit()

    assert queries.get_device(session, 'SN1').version == '17.12.1'


# update_device

def test_update_device_returns_none_when_not_found(session):
    assert queries.update_device(session, 'NOSUCHSERIAL', description='x') is None


def test_update_device_edits_existing_fields(session):
    queries.add_device(session, serial='SN1', mac='aa:bb', description='old', version='17.9.1', config_file='old.cfg')
    session.commit()

    updated = queries.update_device(
        session, 'SN1', mac='cc:dd', description='new', version='17.12.1', config_file='new.cfg',
    )
    session.commit()

    assert updated.mac == 'cc:dd'
    assert updated.description == 'new'
    assert updated.version == '17.12.1'
    assert updated.config_file == 'new.cfg'


def test_update_device_does_not_create_a_new_device(session):
    queries.update_device(session, 'SN1', description='x')
    session.commit()

    assert queries.get_device(session, 'SN1') is None


def test_delete_device(session):
    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.delete_device(session, 'SN1') is True
    session.commit()
    assert queries.get_device(session, 'SN1') is None
    assert queries.delete_device(session, 'SN1') is False


def test_get_device_or_wildcard_prefers_exact_match(session):
    queries.add_device(session, serial='SN1', version='17.9.1')
    queries.add_device(session, serial='*', version='17.12.1')
    session.commit()

    assert queries.get_device_or_wildcard(session, 'SN1').version == '17.9.1'


def test_get_device_or_wildcard_falls_back_to_wildcard(session):
    queries.add_device(session, serial='*', version='17.12.1')
    session.commit()

    device = queries.get_device_or_wildcard(session, 'UNKNOWN')
    assert device is not None
    assert device.serial == '*'
    assert device.version == '17.12.1'


def test_get_device_or_wildcard_returns_none_when_neither_exists(session):
    assert queries.get_device_or_wildcard(session, 'UNKNOWN') is None


def test_list_devices_by_version(session):
    queries.add_device(session, serial='SN1', version='17.9.1')
    queries.add_device(session, serial='SN2', version='17.9.1')
    queries.add_device(session, serial='SN3', version='17.12.1')
    session.commit()

    matches = queries.list_devices_by_version(session, '17.9.1')
    assert sorted(d.serial for d in matches) == ['SN1', 'SN2']


# ProvisioningSession

def test_create_provisioning_session(session):
    queries.add_device(session, serial='SN1')
    ps = queries.create_provisioning_session(
        session, serial='SN1', mac='aa:bb', ip='10.0.0.5', image='img.bin', config_file='base.cfg',
    )
    session.commit()

    assert queries.get_provisioning_session(session, 'SN1') is ps
    assert ps.state == 'lease_approved'
    assert ps.mac == 'aa:bb'
    assert ps.ip == '10.0.0.5'
    assert ps.image == 'img.bin'
    assert ps.config_file == 'base.cfg'


def test_create_provisioning_session_matching_repeat_call_is_idempotent(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', mac='aa:bb', ip='10.0.0.1')
    session.commit()

    ps = queries.create_provisioning_session(session, serial='SN1', mac='aa:bb', ip='10.0.0.1')
    session.commit()

    assert ps is not None
    sessions = session.query(ProvisioningSession).all()
    assert len(sessions) == 1
    assert sessions[0].mac == 'aa:bb'
    assert sessions[0].ip == '10.0.0.1'


def test_create_provisioning_session_rejects_mismatched_repeat_call(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', mac='aa:bb', ip='10.0.0.1')
    session.commit()

    result = queries.create_provisioning_session(session, serial='SN1', mac='cc:dd', ip='10.0.0.2')
    session.commit()

    assert result is None
    sessions = session.query(ProvisioningSession).all()
    assert len(sessions) == 1
    assert sessions[0].mac == 'aa:bb'
    assert sessions[0].ip == '10.0.0.1'


def test_create_provisioning_session_fills_in_previously_unknown_mac(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', ip='10.0.0.1')
    session.commit()

    ps = queries.create_provisioning_session(session, serial='SN1', mac='aa:bb', ip='10.0.0.1')
    session.commit()

    assert ps is not None
    assert ps.mac == 'aa:bb'


def test_create_provisioning_session_repeat_call_bumps_last_seen_at(session):
    queries.add_device(session, serial='SN1')
    ps = queries.create_provisioning_session(session, serial='SN1', ip='10.0.0.1')
    session.commit()
    ps.last_seen_at = '2000-01-01T00:00:00.000000+00:00'
    session.commit()

    queries.create_provisioning_session(session, serial='SN1', ip='10.0.0.1')
    session.commit()

    assert ps.last_seen_at > '2000-01-01T00:00:00.000000+00:00'


def test_delete_provisioning_session(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1')
    session.commit()

    assert queries.delete_provisioning_session(session, 'SN1') is True
    session.commit()
    assert queries.get_provisioning_session(session, 'SN1') is None
    assert queries.delete_provisioning_session(session, 'SN1') is False


def test_find_active_session_by_ip_matches(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', ip='10.0.0.5')
    session.commit()

    found = queries.find_active_session_by_ip(session, '10.0.0.5')
    assert found is not None
    assert found.serial == 'SN1'


def test_find_active_session_by_ip_returns_none_when_no_match(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', ip='10.0.0.5')
    session.commit()

    assert queries.find_active_session_by_ip(session, '10.0.0.99') is None


def test_touch_session_bumps_last_seen_at(session):
    queries.add_device(session, serial='SN1')
    ps = queries.create_provisioning_session(session, serial='SN1')
    session.commit()
    ps.last_seen_at = '2000-01-01T00:00:00.000000+00:00'
    session.commit()

    queries.touch_session(session, serial='SN1')
    session.commit()

    assert ps.last_seen_at > '2000-01-01T00:00:00.000000+00:00'


def test_touch_session_is_a_noop_when_no_session_exists(session):
    queries.touch_session(session, serial='UNKNOWN')  # must not raise
    session.commit()


def test_update_session_facts_sets_model_and_version(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1')
    session.commit()

    ps = queries.update_session_facts(session, serial='SN1', model='C9200CX-12P-2X2G', version='17.9.1')
    session.commit()

    assert ps.model == 'C9200CX-12P-2X2G'
    assert ps.version == '17.9.1'


def test_update_session_facts_returns_none_when_no_session_exists(session):
    assert queries.update_session_facts(session, serial='UNKNOWN', model='m', version='v') is None


def test_update_session_facts_leaves_unreported_field_unchanged(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1')
    session.commit()
    queries.update_session_facts(session, serial='SN1', model='C9200CX-12P-2X2G', version='17.9.1')
    session.commit()

    ps = queries.update_session_facts(session, serial='SN1', model='C9200CX-24P-4X', version=None)
    session.commit()

    assert ps.model == 'C9200CX-24P-4X'
    assert ps.version == '17.9.1'


def test_update_session_facts_bumps_last_seen_at(session):
    queries.add_device(session, serial='SN1')
    ps = queries.create_provisioning_session(session, serial='SN1')
    session.commit()
    ps.last_seen_at = '2000-01-01T00:00:00.000000+00:00'
    session.commit()

    queries.update_session_facts(session, serial='SN1', model='m', version='v')
    session.commit()

    assert ps.last_seen_at > '2000-01-01T00:00:00.000000+00:00'


# User queries

def test_get_user_by_username_and_id(session):
    admin = queries.get_user_by_username(session, 'admin')

    assert admin is not None
    assert queries.get_user_by_id(session, admin.id) is admin
    assert queries.get_user_by_username(session, 'nobody') is None


def test_create_user_has_no_password_set(session):
    user = queries.create_user(session, username='new-operator', role='operator')
    session.commit()

    assert user.password_hash is None
    assert user.auth_source == 'local'
    assert user.role == 'operator'


def test_count_admins_counts_only_admin_role(session):
    # the bootstrap 'admin' user already exists on a fresh test DB
    assert queries.count_admins(session) == 1

    queries.create_user(session, username='another-admin', role='admin')
    queries.create_user(session, username='an-operator', role='operator')
    session.commit()

    assert queries.count_admins(session) == 2


def test_delete_user_removes_and_returns_the_user(session):
    created = queries.create_user(session, username='new-operator', role='operator')
    session.commit()
    user_id = created.id

    deleted = queries.delete_user(session, user_id)
    session.commit()

    assert deleted is not None
    assert queries.get_user_by_id(session, user_id) is None


def test_delete_user_returns_none_when_not_found(session):
    assert queries.delete_user(session, 999999) is None


# Setting queries

def test_set_setting_creates_then_updates(session):
    created = queries.set_setting(session, 'log_retention_days', '45', updated_by='admin')
    session.commit()
    assert created.value == '45'
    first_updated_at = created.updated_at

    updated = queries.set_setting(session, 'log_retention_days', '60', updated_by='admin')
    session.commit()
    assert updated.value == '60'
    assert updated.updated_at >= first_updated_at


# ProvisioningLog queries

def test_add_log_entry_writes_a_row(session):
    entry = queries.add_log_entry(session, serial='SN1', event='provision_complete', ip='10.0.0.5')
    session.commit()

    assert entry.id is not None
    rows = session.query(ProvisioningLog).all()
    assert len(rows) == 1
    assert rows[0].event == 'provision_complete'
    assert rows[0].ip == '10.0.0.5'


def test_purge_expired_logs_removes_rows_older_than_retention(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec='microseconds')
    session.add(ProvisioningLog(serial='SN1', event='provision_complete', timestamp=old_ts))
    session.commit()

    queries.purge_expired_logs(session, '5')
    session.commit()

    assert session.query(ProvisioningLog).count() == 0


def test_purge_expired_logs_is_a_noop_when_retention_is_indefinite(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=9999)).isoformat(timespec='microseconds')
    session.add(ProvisioningLog(serial='SN1', event='provision_complete', timestamp=old_ts))
    session.commit()

    queries.purge_expired_logs(session, 'indefinite')
    session.commit()

    assert session.query(ProvisioningLog).count() == 1


def test_add_log_entry_purges_expired_rows_before_inserting(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec='microseconds')
    session.add(ProvisioningLog(serial='SN1', event='provision_complete', timestamp=old_ts))
    session.commit()
    # default retention seeded from LOG_RETENTION_DAYS config ('30')

    queries.add_log_entry(session, serial='SN1', event='provision_complete')
    session.commit()

    rows = session.query(ProvisioningLog).all()
    assert len(rows) == 1
    assert rows[0].event == 'provision_complete'


# DeviceLogEntry queries

def test_add_device_log_entry_writes_a_row(session):
    entry = queries.add_device_log_entry(session, serial='SN1', source='script', message='hello')
    session.commit()

    assert entry.id is not None
    rows = session.query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].serial == 'SN1'
    assert rows[0].source == 'script'
    assert rows[0].message == 'hello'


def test_add_device_log_entry_allows_null_serial(session):
    entry = queries.add_device_log_entry(session, source='syslog', message='raw line')
    session.commit()

    assert entry.serial is None


def test_list_device_logs_filters_by_serial(session):
    queries.add_device_log_entry(session, serial='SN1', source='script', message='a')
    queries.add_device_log_entry(session, serial='SN2', source='script', message='b')
    session.commit()

    all_entries = queries.list_device_logs(session)
    assert len(all_entries) == 2

    filtered = queries.list_device_logs(session, serial='SN1')
    assert len(filtered) == 1
    assert filtered[0].message == 'a'


def test_list_device_logs_after_id_returns_only_newer_rows(session):
    first = queries.add_device_log_entry(session, serial='SN1', source='script', message='a')
    session.commit()
    second = queries.add_device_log_entry(session, serial='SN1', source='script', message='b')
    session.commit()

    newer = queries.list_device_logs(session, after_id=first.id)
    assert [e.id for e in newer] == [second.id]


def test_list_device_logs_after_id_returns_empty_when_nothing_newer(session):
    entry = queries.add_device_log_entry(session, serial='SN1', source='script', message='a')
    session.commit()

    assert queries.list_device_logs(session, after_id=entry.id) == []


def test_purge_expired_device_logs_removes_rows_older_than_retention(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec='microseconds')
    session.add(DeviceLogEntry(serial='SN1', source='syslog', message='old', timestamp=old_ts))
    session.commit()

    queries.purge_expired_device_logs(session, '5')
    session.commit()

    assert session.query(DeviceLogEntry).count() == 0


def test_purge_expired_device_logs_is_a_noop_when_retention_is_indefinite(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=9999)).isoformat(timespec='microseconds')
    session.add(DeviceLogEntry(serial='SN1', source='syslog', message='old', timestamp=old_ts))
    session.commit()

    queries.purge_expired_device_logs(session, 'indefinite')
    session.commit()

    assert session.query(DeviceLogEntry).count() == 1


def test_add_device_log_entry_purges_expired_rows_before_inserting(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec='microseconds')
    session.add(DeviceLogEntry(serial='SN1', source='syslog', message='old', timestamp=old_ts))
    session.commit()
    # default retention seeded from LOG_RETENTION_DAYS config ('30')

    queries.add_device_log_entry(session, serial='SN1', source='syslog', message='new')
    session.commit()

    rows = session.query(DeviceLogEntry).all()
    assert len(rows) == 1
    assert rows[0].message == 'new'


def test_delete_device_logs_by_serial_removes_all_rows_regardless_of_age(session):
    fresh_ts = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    session.add(DeviceLogEntry(serial='SN1', source='syslog', message='new', timestamp=fresh_ts))
    session.commit()
    # No retention setting purge involved here — this deletes unconditionally,
    # regardless of log_retention_days, unlike purge_expired_device_logs above.

    queries.delete_device_logs_by_serial(session, 'SN1')
    session.commit()

    assert session.query(DeviceLogEntry).count() == 0


def test_delete_device_logs_by_serial_leaves_other_serials_alone(session):
    queries.add_device_log_entry(session, serial='SN1', source='syslog', message='a')
    queries.add_device_log_entry(session, serial='SN2', source='syslog', message='b')
    session.commit()

    queries.delete_device_logs_by_serial(session, 'SN1')
    session.commit()

    remaining = session.query(DeviceLogEntry).all()
    assert len(remaining) == 1
    assert remaining[0].serial == 'SN2'


# KeaLogEntry queries

def test_add_kea_log_entry_writes_a_row(session):
    entry = queries.add_kea_log_entry(session, message='DHCPDISCOVER received')
    session.commit()

    assert entry.id is not None
    rows = session.query(KeaLogEntry).all()
    assert len(rows) == 1
    assert rows[0].message == 'DHCPDISCOVER received'


def test_list_kea_logs_after_id_returns_only_newer_rows(session):
    first = queries.add_kea_log_entry(session, message='a')
    session.commit()
    second = queries.add_kea_log_entry(session, message='b')
    session.commit()

    newer = queries.list_kea_logs(session, after_id=first.id)
    assert [e.id for e in newer] == [second.id]


def test_list_kea_logs_after_id_returns_empty_when_nothing_newer(session):
    entry = queries.add_kea_log_entry(session, message='a')
    session.commit()

    assert queries.list_kea_logs(session, after_id=entry.id) == []


def test_purge_expired_kea_logs_removes_rows_older_than_retention(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(timespec='microseconds')
    session.add(KeaLogEntry(message='old', timestamp=old_ts))
    session.commit()

    queries.purge_expired_kea_logs(session, '5')
    session.commit()

    assert session.query(KeaLogEntry).count() == 0


def test_purge_expired_kea_logs_is_a_noop_when_retention_is_indefinite(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=9999)).isoformat(timespec='microseconds')
    session.add(KeaLogEntry(message='old', timestamp=old_ts))
    session.commit()

    queries.purge_expired_kea_logs(session, 'indefinite')
    session.commit()

    assert session.query(KeaLogEntry).count() == 1


def test_add_kea_log_entry_purges_expired_rows_before_inserting(session):
    old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(timespec='microseconds')
    session.add(KeaLogEntry(message='old', timestamp=old_ts))
    session.commit()
    # default retention seeded from LOG_RETENTION_DAYS config ('30')

    queries.add_kea_log_entry(session, message='new')
    session.commit()

    rows = session.query(KeaLogEntry).all()
    assert len(rows) == 1
    assert rows[0].message == 'new'
