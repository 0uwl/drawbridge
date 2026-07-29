import hashlib
import io
import os

import pytest

from drawbridge.db import get_session
from drawbridge.models import Device, ProvisioningSession, ZTPFile

BASE = '/files'
ROUTE_TYPE = {'images': 'image', 'configs': 'config'}

_UNSET = object()


def upload(client, route, filename, content=b'test content', sha256=None, version=_UNSET, replace=None):
    data = {'file': (io.BytesIO(content), filename)}
    if sha256 is not None:
        data['sha256'] = sha256
    if route == 'images':
        # Most tests here don't care about version resolution at all — default
        # to the filename itself (an arbitrary but per-filename-unique string,
        # no X.X.X format enforced server-side) so unrelated uploads never
        # collide on the 1:1 version->image mapping. Tests that actually
        # exercise parsing/conflict behavior pass version=None (opt out
        # entirely) or an explicit version.
        if version is _UNSET:
            version = filename
        if version is not None:
            data['version'] = version
    if replace is not None:
        data['replace'] = replace
    return client.post(
        f'{BASE}/{route}',
        data=data,
        content_type='multipart/form-data',
    )


@pytest.fixture()
def active_session(app):
    """A bare ProvisioningSession matching the test client's default
    REMOTE_ADDR — files.py's serve gate only checks IP, so no Device row
    is needed."""
    with app.app_context():
        session = get_session()
        ps = ProvisioningSession(serial='FJC2517X0AB', ip='127.0.0.1', state='lease_approved')
        session.add(ps)
        session.commit()
    return ps


# --- GET /files/<type> — list ---

@pytest.mark.parametrize('route', ['images', 'configs'])
def test_list_returns_401_when_not_logged_in(client, route):
    response = client.get(f'{BASE}/{route}')
    assert response.status_code == 401


@pytest.mark.parametrize('route', ['images', 'configs'])
def test_list_returns_empty_list_when_no_files(logged_in_client, route):
    response = logged_in_client.get(f'{BASE}/{route}')
    assert response.status_code == 200
    assert response.get_json()['payload'] == []


def test_list_images_returns_uploaded_images(logged_in_client):
    upload(logged_in_client, 'images', 'ios-xe-17.9.bin')
    upload(logged_in_client, 'images', 'ios-xe-17.12.bin')
    response = logged_in_client.get(f'{BASE}/images')
    payload = response.get_json()['payload']
    assert len(payload) == 2
    assert {f['filename'] for f in payload} == {'ios-xe-17.9.bin', 'ios-xe-17.12.bin'}


def test_list_images_does_not_include_configs(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    upload(logged_in_client, 'configs', 'spine.cfg')
    response = logged_in_client.get(f'{BASE}/images')
    payload = response.get_json()['payload']
    assert len(payload) == 1
    assert payload[0]['file_type'] == 'image'


# --- POST /files/<type> — upload ---

@pytest.mark.parametrize('route', ['images', 'configs'])
def test_upload_returns_401_when_not_logged_in(client, route):
    response = upload(client, route, 'file.bin')
    assert response.status_code == 401


@pytest.mark.parametrize('route', ['images', 'configs'])
def test_upload_returns_422_when_no_file_in_request(logged_in_client, route):
    response = logged_in_client.post(
        f'{BASE}/{route}',
        data={},
        content_type='multipart/form-data',
    )
    assert response.status_code == 422
    assert response.get_json()['error'] == 'missing_file'


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_upload_returns_201_for_valid_file(logged_in_client, route, filename):
    response = upload(logged_in_client, route, filename)
    assert response.status_code == 201


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.cfg'),  # config extension rejected by image endpoint
    ('configs', 'config.bin'),    # image extension rejected by config endpoint
])
def test_upload_returns_422_for_wrong_extension(logged_in_client, route, filename):
    response = upload(logged_in_client, route, filename)
    assert response.status_code == 422
    assert response.get_json()['error'] == 'invalid_extension'


def test_upload_returns_409_when_file_already_exists(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    response = upload(logged_in_client, 'images', 'firmware.bin')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'file_exists'


def test_upload_image_persists_db_record(app, logged_in_client):
    content = b'fake ios xe firmware'
    upload(logged_in_client, 'images', 'ios-xe-17.9.bin', content)
    with app.app_context():
        f = get_session().get(ZTPFile, ('image', 'ios-xe-17.9.bin'))
        assert f is not None
        assert f.file_type == 'image'
        assert f.filename == 'ios-xe-17.9.bin'
        assert f.size_bytes == len(content)
        assert f.sha256 == hashlib.sha256(content).hexdigest()


def test_upload_config_persists_db_record(app, logged_in_client):
    content = b'hostname spine-1\ninterface GigabitEthernet0/0'
    upload(logged_in_client, 'configs', 'spine.cfg', content)
    with app.app_context():
        f = get_session().get(ZTPFile, ('config', 'spine.cfg'))
        assert f is not None
        assert f.file_type == 'config'
        assert f.filename == 'spine.cfg'
        assert f.size_bytes == len(content)
        assert f.sha256 == hashlib.sha256(content).hexdigest()


def test_upload_image_writes_to_images_subdir(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'images', 'firmware.bin'))


def test_upload_config_writes_to_configs_subdir(app, logged_in_client):
    upload(logged_in_client, 'configs', 'spine.cfg')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'configs', 'spine.cfg'))


def test_upload_records_uploaded_by(app, logged_in_client, user):
    upload(logged_in_client, 'images', 'firmware.bin')
    with app.app_context():
        f = get_session().get(ZTPFile, ('image', 'firmware.bin'))
        assert f.uploaded_by == user.username


def test_upload_image_does_not_appear_in_config_listing(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    response = logged_in_client.get(f'{BASE}/configs')
    assert response.get_json()['payload'] == []


# --- POST /files/<type> — optional sha256 verification on upload ---

@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_upload_with_correct_sha256_succeeds(app, logged_in_client, route, filename):
    content = b'test content'
    correct_hash = hashlib.sha256(content).hexdigest()
    response = upload(logged_in_client, route, filename, content, sha256=correct_hash)
    assert response.status_code == 201
    with app.app_context():
        f = get_session().get(ZTPFile, (ROUTE_TYPE[route], filename))
        assert f is not None
        assert f.sha256 == correct_hash


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_upload_with_wrong_sha256_returns_422(app, logged_in_client, route, filename):
    content = b'test content'
    wrong_hash = hashlib.sha256(b'not the real content').hexdigest()
    response = upload(logged_in_client, route, filename, content, sha256=wrong_hash)
    assert response.status_code == 422
    assert response.get_json()['error'] == 'hash_mismatch'
    with app.app_context():
        assert get_session().get(ZTPFile, (ROUTE_TYPE[route], filename)) is None
    assert not os.path.isfile(os.path.join(app.config['FILES_PATH'], route, filename))


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_upload_with_malformed_sha256_returns_422(logged_in_client, route, filename):
    response = upload(logged_in_client, route, filename, sha256='not-a-hash')
    assert response.status_code == 422
    assert response.get_json()['error'] == 'invalid_hash'


# --- POST /files/images — version parsing / 1:1 mapping ---

def test_upload_image_parses_version_from_filename(app, logged_in_client):
    response = upload(logged_in_client, 'images', 'cat9k_iosxe.17.9.1.SPA.bin', version=None)
    assert response.status_code == 201
    with app.app_context():
        f = get_session().get(ZTPFile, ('image', 'cat9k_iosxe.17.9.1.SPA.bin'))
        assert f.version == '17.9.1'


def test_upload_image_returns_422_when_version_unparseable_and_not_supplied(logged_in_client):
    response = upload(logged_in_client, 'images', 'firmware.bin', version=None)
    assert response.status_code == 422
    assert response.get_json()['error'] == 'version_required'


def test_upload_image_explicit_version_overrides_parsed_value(app, logged_in_client):
    response = upload(logged_in_client, 'images', 'cat9k_iosxe.17.9.1.SPA.bin', version='99.0.0')
    assert response.status_code == 201
    with app.app_context():
        f = get_session().get(ZTPFile, ('image', 'cat9k_iosxe.17.9.1.SPA.bin'))
        assert f.version == '99.0.0'


def test_upload_config_ignores_version_field(app, logged_in_client):
    """Configs have no version concept — the field is silently a no-op
    there, not an error, since the upload() helper only attaches it for
    the images route."""
    response = upload(logged_in_client, 'configs', 'spine.cfg')
    assert response.status_code == 201
    with app.app_context():
        f = get_session().get(ZTPFile, ('config', 'spine.cfg'))
        assert f.version is None


def test_upload_image_returns_409_on_version_conflict(logged_in_client):
    upload(logged_in_client, 'images', 'ios-xe-a.bin', version='17.9.1')
    response = upload(logged_in_client, 'images', 'ios-xe-b.bin', version='17.9.1')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'version_conflict'


def test_upload_image_version_conflict_leaves_existing_mapping_untouched(app, logged_in_client):
    upload(logged_in_client, 'images', 'ios-xe-a.bin', version='17.9.1')
    upload(logged_in_client, 'images', 'ios-xe-b.bin', version='17.9.1')
    with app.app_context():
        assert get_session().get(ZTPFile, ('image', 'ios-xe-a.bin')) is not None
        assert get_session().get(ZTPFile, ('image', 'ios-xe-b.bin')) is None


def test_upload_image_replace_true_supersedes_existing_mapping(app, logged_in_client):
    upload(logged_in_client, 'images', 'ios-xe-a.bin', version='17.9.1')
    response = upload(logged_in_client, 'images', 'ios-xe-b.bin', version='17.9.1', replace='true')
    assert response.status_code == 201

    with app.app_context():
        assert get_session().get(ZTPFile, ('image', 'ios-xe-a.bin')) is None
        f = get_session().get(ZTPFile, ('image', 'ios-xe-b.bin'))
        assert f is not None
        assert f.version == '17.9.1'
    assert not os.path.exists(os.path.join(app.config['FILES_PATH'], 'images', 'ios-xe-a.bin'))
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'images', 'ios-xe-b.bin'))


# --- GET /files/<type>/<filename> — serve ---

def test_serve_image_is_accessible_with_active_session(app, client, logged_in_client, active_session):
    content = b'fake ios xe firmware bytes'
    upload(logged_in_client, 'images', 'firmware.bin', content)
    response = client.get(f'{BASE}/images/firmware.bin')
    assert response.status_code == 200
    assert response.data == content


def test_serve_config_is_accessible_with_active_session(app, client, logged_in_client, active_session):
    content = b'hostname spine-1'
    upload(logged_in_client, 'configs', 'spine.cfg', content)
    response = client.get(f'{BASE}/configs/spine.cfg')
    assert response.status_code == 200
    assert response.data == content


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_serve_returns_403_without_active_session(client, logged_in_client, route, filename):
    upload(logged_in_client, route, filename)
    response = client.get(f'{BASE}/{route}/{filename}')
    assert response.status_code == 403
    assert response.get_json()['error'] == 'no_active_session'


@pytest.mark.parametrize('route,filename', [
    ('images',  'nonexistent.bin'),
    ('configs', 'nonexistent.cfg'),
])
def test_serve_returns_404_for_missing_file_with_active_session(client, active_session, route, filename):
    response = client.get(f'{BASE}/{route}/{filename}')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'file_not_found'


@pytest.mark.parametrize('route', ['images', 'configs'])
@pytest.mark.parametrize('escaped_path', [
    '../../main.py',
    '..%2f..%2fmain.py',
    '%2e%2e%2f%2e%2e%2fmain.py',
])
def test_serve_rejects_path_traversal_attempts(client, active_session, route, escaped_path):
    """The <path:filename> route converter accepts '/', so a traversal
    attempt reaches this blueprint's own get_file() DB lookup rather than
    falling through route-matching to main.py's SPA catch-all (which would
    otherwise silently serve index.html with a 200 for any unmatched path —
    see docs/decisions.md). No stored ZTPFile row can contain '/' or '..'
    (filenames are sanitized via secure_filename() at upload time), so the
    lookup always misses and this 404s cleanly; send_from_directory's own
    safe_join is a second, independent layer even if that lookup somehow
    passed."""
    response = client.get(f'{BASE}/{route}/{escaped_path}')
    assert response.status_code == 404


# --- DELETE /files/<type>/<filename> ---

@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_delete_returns_401_when_not_logged_in(client, route, filename):
    response = client.delete(f'{BASE}/{route}/{filename}')
    assert response.status_code == 401


def test_delete_returns_404_when_file_not_found(logged_in_client):
    response = logged_in_client.delete(f'{BASE}/images/nonexistent.bin')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'file_not_found'


def test_delete_returns_200_on_success(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    response = logged_in_client.delete(f'{BASE}/images/firmware.bin')
    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_delete_removes_db_record(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    logged_in_client.delete(f'{BASE}/images/firmware.bin')
    with app.app_context():
        assert get_session().get(ZTPFile, ('image', 'firmware.bin')) is None


def test_delete_removes_file_from_disk(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    logged_in_client.delete(f'{BASE}/images/firmware.bin')
    assert not os.path.exists(os.path.join(app.config['FILES_PATH'], 'images', 'firmware.bin'))


def test_deleted_file_is_no_longer_served(client, logged_in_client, active_session):
    upload(logged_in_client, 'images', 'firmware.bin')
    logged_in_client.delete(f'{BASE}/images/firmware.bin')
    response = client.get(f'{BASE}/images/firmware.bin')
    assert response.status_code == 404


def test_delete_only_removes_file_of_matching_type(app, logged_in_client):
    """Deleting a config must not touch the images subdirectory."""
    upload(logged_in_client, 'images', 'firmware.bin')
    upload(logged_in_client, 'configs', 'spine.cfg')
    logged_in_client.delete(f'{BASE}/configs/spine.cfg')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'images', 'firmware.bin'))


# --- DELETE /files/images/<filename> — in-use warning (docs/decisions.md) ---

def test_delete_image_in_use_by_allowlist_returns_409_without_confirm(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin', version='17.9.1')
    with app.app_context():
        session = get_session()
        session.add(Device(serial='SN1', version='17.9.1', added_by='operator'))
        session.commit()

    response = logged_in_client.delete(f'{BASE}/images/firmware.bin')
    assert response.status_code == 409
    assert response.get_json()['error'] == 'image_in_use'

    with app.app_context():
        assert get_session().get(ZTPFile, ('image', 'firmware.bin')) is not None


def test_delete_image_in_use_with_confirm_true_succeeds(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin', version='17.9.1')
    with app.app_context():
        session = get_session()
        session.add(Device(serial='SN1', version='17.9.1', added_by='operator'))
        session.commit()

    response = logged_in_client.delete(f'{BASE}/images/firmware.bin?confirm=true')
    assert response.status_code == 200

    with app.app_context():
        assert get_session().get(ZTPFile, ('image', 'firmware.bin')) is None


def test_delete_image_not_in_use_succeeds_without_confirm(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin', version='17.9.1')
    response = logged_in_client.delete(f'{BASE}/images/firmware.bin')
    assert response.status_code == 200


# --- PUT /files/<type>/<filename> — edit stored hash ---

VALID_HASH = 'a' * 64

@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_put_hash_returns_401_when_not_logged_in(client, route, filename):
    response = client.put(f'{BASE}/{route}/{filename}', json={'sha256': VALID_HASH})
    assert response.status_code == 401


def test_put_hash_returns_404_when_file_not_found(logged_in_client):
    response = logged_in_client.put(f'{BASE}/images/nonexistent.bin', json={'sha256': VALID_HASH})
    assert response.status_code == 404
    assert response.get_json()['error'] == 'file_not_found'


def test_put_hash_returns_422_for_malformed_hash(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    response = logged_in_client.put(f'{BASE}/images/firmware.bin', json={'sha256': 'not-a-hash'})
    assert response.status_code == 422
    assert response.get_json()['error'] == 'invalid_hash'


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.bin'),
    ('configs', 'spine.cfg'),
])
def test_put_hash_updates_db_record(app, logged_in_client, route, filename):
    upload(logged_in_client, route, filename)
    response = logged_in_client.put(f'{BASE}/{route}/{filename}', json={'sha256': VALID_HASH})
    assert response.status_code == 200
    assert response.get_json()['payload']['sha256'] == VALID_HASH
    with app.app_context():
        f = get_session().get(ZTPFile, (ROUTE_TYPE[route], filename))
        assert f.sha256 == VALID_HASH
