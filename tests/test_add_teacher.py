"""Admin teacher registration is shared by web, WhatsApp, and Teacher Helper."""
import sqlite3
import time

import pytest

from conftest import login
from service.teacher_registry import (
    TeacherRegistry, TeacherRegistryUnavailable,
)
from service.whatsapp import WhatsAppFlow
from test_interactive_menus import SENDER, latest, tap
from test_integration import send

HEADERS = {'X-Exam-Client': 'web'}


def details(number='+919876543210'):
    return {'whatsapp_number': number, 'teacher_name': 'Asha Kumari',
            'default_grade': '10', 'default_subject': 'Mathematics'}


def registry_row(svc, phone):
    path = svc.s.teacher_registry_database_url.removeprefix('sqlite:///')
    with sqlite3.connect(path) as db:
        return db.execute('SELECT whatsapp_number, teacher_name, default_grade, default_subject, '
                          'school_name, preferred_language, created_at, updated_at '
                          'FROM teacher_profile WHERE whatsapp_number=?', (phone,)).fetchone()


def test_web_admin_add_teacher_then_teacher_can_log_in(env):
    svc, c, _ = env
    assert login(c, 'admin').status_code == 200
    response = c.post('/api/admin/teachers', headers=HEADERS, json=details(' +91 (98765) 43210 '))
    assert response.status_code == 201
    teacher = response.json()['teacher']
    assert teacher['id'] > 2
    assert teacher['whatsapp_number'] == '+919876543210'
    assert teacher['school_name'] == 'Parivaar School'
    assert teacher['preferred_language'] == 'Hinglish'
    saved = registry_row(svc, '+919876543210')
    assert saved[:6] == ('+919876543210', 'Asha Kumari', '10', 'Mathematics', 'Parivaar School', 'Hinglish')
    assert saved[6] and saved[7]
    assert c.post('/api/logout', headers=HEADERS).status_code == 200
    response = c.post('/api/login', headers=HEADERS, json={
        'role': 'teacher', 'password': 'teacher-test', 'whatsapp_number': '919876543210'})
    assert response.status_code == 200


def test_only_admin_can_create_teacher(env):
    svc, c, _ = env
    payload=details()
    assert c.post('/api/admin/teachers', headers=HEADERS, json=payload).status_code == 401
    assert login(c).status_code == 200
    assert c.post('/api/admin/teachers', headers=HEADERS, json=payload).status_code == 403
    assert registry_row(svc, payload['whatsapp_number']) is None
    assert login(c, 'admin').status_code == 200
    assert c.post('/api/admin/teachers', json=payload).status_code == 403


def test_duplicate_numbers_not_overwritten(env):
    svc, c, _ = env
    assert login(c, 'admin').status_code == 200
    original = registry_row(svc, '+15551230000')
    response = c.post('/api/admin/teachers', headers=HEADERS, json=details('15551230000'))
    assert response.status_code == 409
    assert 'already registered' in response.json()['detail']
    assert registry_row(svc, '+15551230000') == original
    assert c.post('/api/admin/teachers', headers=HEADERS, json=details()).status_code == 201
    assert c.post('/api/admin/teachers', headers=HEADERS, json=details('919876543210')).status_code == 409


@pytest.mark.parametrize('changes', [
    {'whatsapp_number': '123'}, {'whatsapp_number': '1111;DROP TABLE teacher_profile'},
    {'teacher_name': ''}, {'teacher_name': 'x\ny'}, {'teacher_name': 'x' * 256},
    {'default_grade': ''}, {'default_grade': 'x' * 101},
    {'default_subject': ''}, {'default_subject': 'x' * 101},
])
def test_invalid_teacher_fields_rejected(env, changes):
    _, c, _ = env
    assert login(c, 'admin').status_code == 200
    response = c.post('/api/admin/teachers', headers=HEADERS, json={**details(), **changes})
    assert response.status_code == 400


def test_registry_missing_does_not_create_teacher(env):
    svc, c, _ = env
    assert login(c, 'admin').status_code == 200
    svc.teacher_registry.database_url = 'sqlite:////no/such/teacher_registry.sqlite'
    response = c.post('/api/admin/teachers', headers=HEADERS, json=details())
    assert response.status_code == 503


def start_admin(env):
    svc, _, _ = env
    flow = WhatsAppFlow(svc)
    send(flow, 'Hi')
    tap(svc, flow, 'main.admin')
    send(flow, 'admin-test')
    assert svc.admin_authenticated(SENDER)
    return svc, flow


def whatsapp_registration(svc, flow):
    tap(svc, flow, 'admin.teacher.add')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_number'
    send(flow, ' +91 98765 43210 ')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_name'
    send(flow, 'Asha Kumari')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_grade'
    send(flow, '10')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_subject'
    send(flow, 'Mathematics')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_confirm'


def test_whatsapp_admin_add_teacher_then_teacher_can_use_whatsapp(env):
    svc, flow = start_admin(env)
    whatsapp_registration(svc, flow)
    text = latest(svc)['body']['text']
    assert 'Parivaar School' in text and 'Hinglish' in text
    assert registry_row(svc, '+919876543210') is None
    tap(svc, flow, 'admin.teacher.save')
    assert svc.db.session(SENDER,30)['state'] == 'admin_home'
    assert registry_row(svc, '+919876543210')[:6] == (
        '+919876543210', 'Asha Kumari', '10', 'Mathematics', 'Parivaar School', 'Hinglish')
    flow.handle('919876543210', {'type': 'text', 'text': {'body': 'Hi'}}, 'new-teacher-hi')
    assert svc.db.one('SELECT * FROM outbox WHERE dedupe=?', ('new-teacher-hi:reply',))


def test_whatsapp_duplicate_and_cancel_does_not_write(env):
    svc, flow = start_admin(env)
    tap(svc, flow, 'admin.teacher.add')
    send(flow, '+15551230000')
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_number'
    assert 'already registered' in latest(svc)['body']['text']
    send(flow, '+919876543210')
    tap(svc, flow, 'admin.home')
    assert registry_row(svc, '+919876543210') is None
    assert svc.db.session(SENDER,30)['state'] == 'admin_home'


def test_whatsapp_admin_expiry_blocks_create(env):
    svc, flow = start_admin(env)
    whatsapp_registration(svc, flow)
    svc.db.execute('UPDATE whatsapp_admin SET expires=0 WHERE sender=?', (SENDER,))
    tap(svc, flow, 'admin.teacher.save')
    assert registry_row(svc, '+919876543210') is None
    assert svc.db.session(SENDER,30)['state'] == 'admin_login'


def test_whatsapp_duplicate_after_confirmation(env):
    svc, flow = start_admin(env)
    whatsapp_registration(svc, flow)
    svc.teacher_registry.add_teacher(**details())
    tap(svc, flow, 'admin.teacher.save')
    assert 'already registered' in latest(svc)['body']['text']
    assert svc.db.session(SENDER,30)['state'] == 'admin_teacher_confirm'


def test_new_teacher_does_not_need_new_credentials(env):
    svc, c, _ = env
    teacher = svc.teacher_registry.add_teacher(**details())
    assert teacher['id'] > 0
    response = c.post('/api/login', headers=HEADERS, json={
        'role': 'teacher', 'password': 'teacher-test', 'whatsapp_number': '+919876543210'})
    assert response.status_code == 200
