import hashlib
import io
import os

import pytest

from drawbridge.db import get_session
from drawbridge.models import ZTPFile

BASE = '/files'


def upload(client, route, filename, content=b'test content'):
    return client.post(
        f'{BASE}/{route}',
        data={'file': (io.BytesIO(content), filename)},
        content_type='multipart/form-data',
    )


# --- GET /files/<type> — list ---

@pytest.mark.parametrize('route', ['images', 'configs', 'scripts'])
def test_list_returns_401_when_not_logged_in(client, route):
    response = client.get(f'{BASE}/{route}')
    assert response.status_code == 401


@pytest.mark.parametrize('route', ['images', 'configs', 'scripts'])
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


def test_list_images_does_not_include_configs_or_scripts(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    upload(logged_in_client, 'configs', 'spine.cfg')
    upload(logged_in_client, 'scripts', 'ztp.py')
    response = logged_in_client.get(f'{BASE}/images')
    payload = response.get_json()['payload']
    assert len(payload) == 1
    assert payload[0]['file_type'] == 'image'


# --- POST /files/<type> — upload ---

@pytest.mark.parametrize('route', ['images', 'configs', 'scripts'])
def test_upload_returns_401_when_not_logged_in(client, route):
    response = upload(client, route, 'file.bin')
    assert response.status_code == 401


@pytest.mark.parametrize('route', ['images', 'configs', 'scripts'])
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
    ('scripts', 'ztp.py'),
])
def test_upload_returns_201_for_valid_file(logged_in_client, route, filename):
    response = upload(logged_in_client, route, filename)
    assert response.status_code == 201


@pytest.mark.parametrize('route,filename', [
    ('images',  'firmware.py'),   # script extension rejected by image endpoint
    ('configs', 'config.bin'),    # image extension rejected by config endpoint
    ('scripts', 'script.cfg'),    # config extension rejected by script endpoint
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


def test_upload_script_persists_db_record(app, logged_in_client):
    content = b'import cli\ncli.execute("show version")'
    upload(logged_in_client, 'scripts', 'ztp.py', content)
    with app.app_context():
        f = get_session().get(ZTPFile, ('script', 'ztp.py'))
        assert f is not None
        assert f.file_type == 'script'
        assert f.filename == 'ztp.py'
        assert f.size_bytes == len(content)
        assert f.sha256 == hashlib.sha256(content).hexdigest()


def test_upload_image_writes_to_images_subdir(app, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'images', 'firmware.bin'))


def test_upload_config_writes_to_configs_subdir(app, logged_in_client):
    upload(logged_in_client, 'configs', 'spine.cfg')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'configs', 'spine.cfg'))


def test_upload_script_writes_to_scripts_subdir(app, logged_in_client):
    upload(logged_in_client, 'scripts', 'ztp.py')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'scripts', 'ztp.py'))


def test_upload_records_uploaded_by(app, logged_in_client, user):
    upload(logged_in_client, 'images', 'firmware.bin')
    with app.app_context():
        f = get_session().get(ZTPFile, ('image', 'firmware.bin'))
        assert f.uploaded_by == user.username


def test_upload_image_does_not_appear_in_config_listing(logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    response = logged_in_client.get(f'{BASE}/configs')
    assert response.get_json()['payload'] == []


# --- GET /files/<type>/<filename> — serve ---

def test_serve_image_is_accessible_without_auth(app, client, logged_in_client):
    content = b'fake ios xe firmware bytes'
    upload(logged_in_client, 'images', 'firmware.bin', content)
    response = client.get(f'{BASE}/images/firmware.bin')
    assert response.status_code == 200
    assert response.data == content


def test_serve_config_is_accessible_without_auth(app, client, logged_in_client):
    content = b'hostname spine-1'
    upload(logged_in_client, 'configs', 'spine.cfg', content)
    response = client.get(f'{BASE}/configs/spine.cfg')
    assert response.status_code == 200
    assert response.data == content


def test_serve_script_is_accessible_without_auth(app, client, logged_in_client):
    content = b'import cli\ncli.execute("show version")'
    upload(logged_in_client, 'scripts', 'ztp.py', content)
    response = client.get(f'{BASE}/scripts/ztp.py')
    assert response.status_code == 200
    assert response.data == content


@pytest.mark.parametrize('route,filename', [
    ('images',  'nonexistent.bin'),
    ('configs', 'nonexistent.cfg'),
    ('scripts', 'nonexistent.py'),
])
def test_serve_returns_404_for_missing_file(client, route, filename):
    response = client.get(f'{BASE}/{route}/{filename}')
    assert response.status_code == 404
    assert response.get_json()['error'] == 'file_not_found'


@pytest.mark.parametrize('route', ['images', 'configs', 'scripts'])
@pytest.mark.parametrize('escaped_path', [
    '../../main.py',
    '..%2f..%2fmain.py',
    '%2e%2e%2f%2e%2e%2fmain.py',
])
def test_serve_rejects_path_traversal_attempts(client, route, escaped_path):
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
    ('scripts', 'ztp.py'),
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


def test_deleted_file_is_no_longer_served(client, logged_in_client):
    upload(logged_in_client, 'images', 'firmware.bin')
    logged_in_client.delete(f'{BASE}/images/firmware.bin')
    response = client.get(f'{BASE}/images/firmware.bin')
    assert response.status_code == 404


def test_delete_only_removes_file_of_matching_type(app, logged_in_client):
    """Deleting a config must not touch the images or scripts subdirectories."""
    upload(logged_in_client, 'images', 'firmware.bin')
    upload(logged_in_client, 'configs', 'spine.cfg')
    logged_in_client.delete(f'{BASE}/configs/spine.cfg')
    assert os.path.isfile(os.path.join(app.config['FILES_PATH'], 'images', 'firmware.bin'))
