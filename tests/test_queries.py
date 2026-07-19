from datetime import datetime, timedelta, timezone

from drawbridge import queries
from drawbridge.models import DeviceLogEntry, ProvisioningLog, ProvisioningSession, Setting


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


def test_add_device_stores_image_config_file_and_script(session):
    queries.add_device(session, serial='SN1', image='ios-xe-17.9.bin', config_file='spine.cfg', script='ztp-spine.py')
    session.commit()

    device = queries.get_device(session, 'SN1')
    assert device.image == 'ios-xe-17.9.bin'
    assert device.config_file == 'spine.cfg'
    assert device.script == 'ztp-spine.py'


def test_add_device_uses_default_image_from_setting(session):
    session.add(Setting(key='default_image', value='ios-xe-default.bin'))
    session.commit()

    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.get_device(session, 'SN1').image == 'ios-xe-default.bin'


def test_add_device_uses_default_config_file_from_setting(session):
    session.add(Setting(key='default_config_file', value='default.cfg'))
    session.commit()

    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.get_device(session, 'SN1').config_file == 'default.cfg'


def test_add_device_explicit_image_overrides_default(session):
    session.add(Setting(key='default_image', value='ios-xe-default.bin'))
    session.commit()

    queries.add_device(session, serial='SN1', image='ios-xe-custom.bin')
    session.commit()

    assert queries.get_device(session, 'SN1').image == 'ios-xe-custom.bin'


def test_add_device_uses_default_script_from_setting(session):
    session.add(Setting(key='default_script', value='ztp-base.py'))
    session.commit()

    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.get_device(session, 'SN1').script == 'ztp-base.py'


def test_add_device_explicit_script_overrides_default(session):
    session.add(Setting(key='default_script', value='ztp-base.py'))
    session.commit()

    queries.add_device(session, serial='SN1', script='ztp-spine.py')
    session.commit()

    assert queries.get_device(session, 'SN1').script == 'ztp-spine.py'


def test_add_device_reregistration_preserves_image_when_not_provided(session):
    queries.add_device(session, serial='SN1', image='ios-xe-17.9.bin')
    session.commit()

    queries.add_device(session, serial='SN1', mac='aa:bb')
    session.commit()

    assert queries.get_device(session, 'SN1').image == 'ios-xe-17.9.bin'


def test_add_device_reregistration_updates_image_when_provided(session):
    queries.add_device(session, serial='SN1', image='ios-xe-17.9.bin')
    session.commit()

    queries.add_device(session, serial='SN1', image='ios-xe-17.12.bin')
    session.commit()

    assert queries.get_device(session, 'SN1').image == 'ios-xe-17.12.bin'


def test_add_device_reregistration_preserves_script_when_not_provided(session):
    queries.add_device(session, serial='SN1', script='ztp-spine.py')
    session.commit()

    queries.add_device(session, serial='SN1', mac='aa:bb')
    session.commit()

    assert queries.get_device(session, 'SN1').script == 'ztp-spine.py'


def test_add_device_reregistration_updates_script_when_provided(session):
    queries.add_device(session, serial='SN1', script='ztp-spine.py')
    session.commit()

    queries.add_device(session, serial='SN1', script='ztp-leaf.py')
    session.commit()

    assert queries.get_device(session, 'SN1').script == 'ztp-leaf.py'


def test_delete_device(session):
    queries.add_device(session, serial='SN1')
    session.commit()

    assert queries.delete_device(session, 'SN1') is True
    session.commit()
    assert queries.get_device(session, 'SN1') is None
    assert queries.delete_device(session, 'SN1') is False


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


def test_create_provisioning_session_is_idempotent(session):
    queries.add_device(session, serial='SN1')
    queries.create_provisioning_session(session, serial='SN1', mac='aa:bb', ip='10.0.0.1')
    session.commit()

    queries.create_provisioning_session(session, serial='SN1', mac='cc:dd', ip='10.0.0.2')
    session.commit()

    sessions = session.query(ProvisioningSession).all()
    assert len(sessions) == 1
    assert sessions[0].mac == 'cc:dd'
    assert sessions[0].ip == '10.0.0.2'


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
