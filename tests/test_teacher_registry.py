"""Teacher Helper registration gates Exam Formatter web and WhatsApp operations."""
import json
import sqlite3
import pytest
from service.teacher_registry import TeacherRegistry, TeacherRegistryUnavailable
from service.config import Settings
import records
from service.whatsapp import WhatsAppFlow
from conftest import login

HEADERS = {'X-Exam-Client': 'web'}


def test_registered_teacher_can_sign_in(env):
    svc, client, _ = env
    response = login(client)
    assert response.status_code == 200
    assert client.get('/api/me').status_code == 200
    row = svc.db.one('SELECT * FROM auth WHERE role=?', ('teacher',))
    assert row['owner'].startswith('web:teacher:1:')


@pytest.mark.parametrize('phone', ['+15550000000', '123', 'abc', '', '000000000000000'])
def test_unknown_web_teacher_rejected(env, phone):
    _, client, _ = env
    response = client.post('/api/login', headers=HEADERS, json={
        'role': 'teacher', 'password': 'teacher-test', 'whatsapp_number': phone,
    })
    assert response.status_code == 401
    assert response.json()['detail'] == 'Not a valid user.'


def test_bad_code_and_admin_login_are_preserved(env):
    _, client, _ = env
    response = client.post('/api/login', headers=HEADERS, json={
        'role': 'teacher', 'password': 'wrong', 'whatsapp_number': '+15551230000',
    })
    assert response.status_code == 401
    assert login(client, 'admin').status_code == 200


def test_teacher_revocation_invalidates_existing_web_session(env):
    svc, client, _ = env
    assert login(client).status_code == 200
    db_path = svc.s.teacher_registry_database_url.removeprefix('sqlite:///')
    with sqlite3.connect(db_path) as db:
        db.execute('DELETE FROM teacher_profile WHERE id = 1')
    response = client.get('/api/jobs')
    assert response.status_code == 401
    assert response.json()['detail'] == 'Not a valid user.'


def test_whatsapp_registered_and_unknown(env):
    svc, _, _ = env
    flow = WhatsAppFlow(svc)
    flow.handle('15551230000', {'type': 'text', 'text': {'body': 'Hi'}}, 'registered')
    assert svc.db.one('SELECT * FROM outbox WHERE dedupe=?', ('registered:reply',))
    flow.handle('15551119999', {'type': 'text', 'text': {'body': 'Hi'}}, 'unknown')
    row = svc.db.one('SELECT * FROM outbox WHERE dedupe=?', ('unknown:invalid-user',))
    assert json.loads(row['payload']) == {'text': 'Not a valid user.'}
    assert not svc.db.one('SELECT * FROM sessions WHERE sender=?', ('15551119999',))


def test_registry_outage_does_not_grant_access(env):
    svc, client, _ = env
    svc.teacher_registry.database_url = 'sqlite:////this-path-does-not-exist.sqlite3'
    response = login(client)
    assert response.status_code == 503
    WhatsAppFlow(svc).handle('15551230000', {'type': 'text', 'text': {'body': 'Hi'}}, 'outage')
    row = svc.db.one('SELECT * FROM outbox WHERE dedupe=?', ('outage:registry-unavailable',))
    assert row and 'temporarily unavailable' in json.loads(row['payload'])['text']


def test_registry_number_normalization(env):
    svc, _, _ = env
    assert svc.teacher_registry.find_by_number(' +1 (555) 123-0000 ')['id'] == 1
    assert svc.teacher_registry.find_by_number('+15551239999')['id'] == 2
    assert svc.teacher_registry.find_by_number('unknown') is None


def test_database_url_environment_variable_is_used(monkeypatch, tmp_path):
    monkeypatch.setattr(records, 'load_env', lambda: None)
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(tmp_path / 'teachers.sqlite3'))
    monkeypatch.setenv('TEACHER_REGISTRY_DATABASE_URL', 'sqlite:////wrong.sqlite3')
    settings = Settings.load()
    assert settings.teacher_registry_database_url == 'sqlite:///' + str(tmp_path / 'teachers.sqlite3')


def test_missing_database_url_does_not_use_old_name(monkeypatch):
    monkeypatch.setattr(records, 'load_env', lambda: None)
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('TEACHER_REGISTRY_DATABASE_URL', 'sqlite:////wrong.sqlite3')
    assert Settings.load().teacher_registry_database_url == ''
