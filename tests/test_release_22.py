"""Revision 2.2: dual-language channels, compact activity, and local recovery."""
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

import records
import spellcheck
from conftest import login
from service.whatsapp import WhatsAppFlow
from test_integration import send
from test_spellcheck import make_docx, body_paragraphs

H = {'X-Exam-Client': 'web'}
TABLE = '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Newton</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
CASES = {
    'English': (
        ['The studnts will answer Q1.', 'Used by polce departments.', TABLE],
        ['The students will answer Q1.', 'Used by police departments.'],
        ['studnts -> students', 'polce -> police'],
    ),
    'Hindi': (
        ['विदयार्थी प्रश्न 1 का उत्तर दें।', 'न्यूटन का प्रथम गति निय किससे संबंधित है?', TABLE],
        ['विद्यार्थी प्रश्न 1 का उत्तर दें।', 'न्यूटन का प्रथम गति नियम किससे संबंधित है?'],
        ['विदयार्थी -> विद्यार्थी', 'निय -> नियम'],
    ),
}


def check_files(input_data, output_data, report_text, expected, corrections):
    assert body_paragraphs(output_data) == expected
    assert report_text.count(' -> ') == 2
    for item in corrections:
        assert item in report_text
    with ZipFile(BytesIO(input_data)) as original, ZipFile(BytesIO(output_data)) as updated:
        assert original.namelist() == updated.namelist()
        for name in original.namelist():
            if name != 'word/document.xml':
                assert original.read(name) == updated.read(name)
        assert updated.read('word/document.xml').count(b'<w:tbl>') == 1
        assert b'Newton' in updated.read('word/document.xml')


@pytest.mark.parametrize('language', ['Hindi', 'English'])
def test_whatsapp_two_typos_per_language(env, language):
    svc, _, meta = env
    blocks, expected, changes = CASES[language]
    data = make_docx(blocks)
    svc.engine = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('FORMAT/CHECK called'))
    flow = WhatsAppFlow(svc)
    if language == 'Hindi':
        send(flow, 'Hi')
        send(flow, 'main.spellcheck')
    else:
        send(flow, 'sPeLlChEcK')
    assert svc.db.session('15551230000', 30)['state'] == 'spellcheck_upload'
    meta.input = data
    send(flow, f'{language} errors.docx', 'document')
    assert svc.process_one()
    row = svc.db.one("SELECT * FROM jobs WHERE kind='spellcheck'")
    assert row['state'] == 'completed' and row['result_status'] == 'SPELLCHECK'
    assert row['subject'] == 'Spellcheck' and row['grade'] == '' and row['reference_id'] == ''
    assert (svc.s.data/'jobs'/row['id']/'teacher.docx').read_bytes() == data
    output = Path(row['output']).read_bytes()
    report = spellcheck.report_path_for(row['output']).read_text(encoding='utf-8')
    check_files(data, output, report, expected, changes)
    while svc.deliver_one():
        pass
    assert [name.rsplit('.', 1)[-1] for _, _, name, _ in meta.documents] == ['docx', 'txt']
    assert svc.get(row['id'])['delivery'] == 'sent'
    assert records.read_activity().splitlines()[-1].endswith('Spell/-- PASS')


@pytest.mark.parametrize('language', ['Hindi', 'English'])
def test_web_two_typos_per_language(env, language):
    svc, client, _ = env
    blocks, expected, changes = CASES[language]
    data = make_docx(blocks)
    svc.engine = lambda *a, **kw: (_ for _ in ()).throw(AssertionError('FORMAT/CHECK called'))
    assert login(client).status_code == 200
    response = client.post('/api/spellcheck', headers=H, data={'submission_id': '2-errors'},
        files={'file': (f'{language}.docx', data, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
    assert response.status_code == 200, response.text
    jid = response.json()['id']
    assert svc.process_one()
    output = client.get(f'/api/jobs/{jid}/download')
    report = client.get(f'/api/jobs/{jid}/spelling-report')
    assert output.status_code == 200 and report.status_code == 200
    check_files(data, output.content, report.text, expected, changes)
    assert svc.get(jid)['state'] == 'completed'
    assert svc.get(jid)['reference_id'] == ''
    assert records.read_activity().splitlines()[-1].endswith('Spell/-- PASS')
    assert login(client, 'admin').status_code == 200
    activity = client.get('/api/admin/overview').json()['activity'].splitlines()
    assert activity[-1].endswith('Spell/-- PASS')
    assert all(len(entry) < 40 for entry in activity)
    assert 'Revision 2.2' in client.get('/').text


def test_unregistered_cannot_use_direct_spellcheck(env):
    svc, _, _ = env
    WhatsAppFlow(svc).handle('15556667777', {'type':'text','text':{'body':'SPELLCHECK'}}, 'no-access')
    assert svc.db.one("SELECT id FROM jobs WHERE kind='spellcheck'") is None
    assert svc.db.session('15556667777', 30)['state'] == 'main'
    assert any('Not a valid user' in json.loads(row['payload']).get('text', '')
               for row in svc.db.all("SELECT * FROM outbox WHERE sender='15556667777'"))


def test_legacy_and_new_activity_are_phone_sized(env):
    svc, client, _ = env
    for subject, grade, action, result in [
        ('Mathematics and Applied Linguistics', '12345678901234567890', 'CHECK', 'FORMATTING'),
        ('Spellcheck', '', 'SPELLCHECK', 'NONE'),
        ('Physics', '8', 'FORMAT', 'SYSTEM ERROR'),
        ('Science', '12', 'REF', 'ADDED'),
    ]:
        records.log(subject, grade, action, result)
    lines = records.read_activity().splitlines()
    assert len(lines) == 4
    assert all(len(line) < 40 for line in lines)
    assert lines[0].endswith('PASS') and lines[1].endswith('Spell/-- PASS')
    assert lines[2].endswith('Physics/8 FAIL')
    assert lines[3].endswith('Science/12 PASS')
    from service.whatsapp_admin import WhatsAppAdmin
    display = WhatsAppAdmin(WhatsAppFlow(svc)).activity_chunks()
    assert all(len(line) < 40 for page in display for line in page.splitlines())
    assert login(client, 'admin').status_code == 200
    assert client.get('/api/admin/overview').json()['activity'].splitlines() == lines
    assert '| CHECK |' in records.read_log()  # original audit records still intact


def _quiet_restart(svc):
    svc.stop.set()
    svc.start()
    svc.close()


def test_running_spellcheck_restart_reuses_job_id_without_format(env):
    svc, _, _ = env
    data=make_docx(CASES['English'][0])
    row=svc.submit_spellcheck('web:owner', 'web', 'English.docx', data, 'restart')
    svc.update(row['id'],state='running')
    svc.engine=lambda *a,**kw: (_ for _ in ()).throw(AssertionError('FORMAT invoked'))
    _quiet_restart(svc)
    assert svc.get(row['id'])['state']=='queued'
    assert svc.process_one()
    assert svc.get(row['id'])['state']=='completed'
    assert [r['id'] for r in svc.db.all('SELECT id FROM jobs')]==[row['id']]


def test_missing_input_during_spellcheck_recovery_is_failed_and_logged(env):
    svc, _, _=env
    row=svc.submit_spellcheck('web:owner','web','English.docx',make_docx(['The studnts win.']),'lost')
    (svc.s.data/'jobs'/row['id']/'teacher.docx').unlink()
    svc.update(row['id'],state='running')
    _quiet_restart(svc)
    assert svc.process_one()
    failed=svc.get(row['id'])
    assert failed['state']=='failed'
    assert json.loads(failed['diagnostic'])['stage']=='spellcheck'
    assert records.read_activity().splitlines()[-1].endswith('Spell/-- FAIL')


def test_restart_with_saved_results_does_not_repeat_processing(env):
    svc, _, _=env
    row=svc.submit_spellcheck('web:owner','web','English.docx',make_docx(['The studnts win.']),'done')
    assert svc.process_one()
    _quiet_restart(svc)
    assert svc.get(row['id'])['state']=='completed'
    assert not svc.process_one()


def test_restart_during_send_requires_review_not_automatic_resend(env):
    svc, _, meta=env
    row=svc.submit_spellcheck('wa:15551230000','whatsapp','English.docx',make_docx(['The studnts win.']),'send')
    assert svc.process_one()
    outbox=svc.db.all("SELECT id FROM outbox WHERE job_id=? AND kind='document' ORDER BY id",(row['id'],))
    assert len(outbox)==2
    svc.db.execute("UPDATE outbox SET state='sending' WHERE id=?",(outbox[0]['id'],))
    _quiet_restart(svc)
    states=[r['state'] for r in svc.db.all("SELECT * FROM outbox WHERE job_id=? AND kind='document' ORDER BY id",(row['id'],))]
    assert states==['failed','queued']
    assert svc.get(row['id'])['delivery']=='failed'
    while svc.deliver_one():
        pass
    assert len(meta.documents)==1
    assert svc.get(row['id'])['delivery']=='failed'
    assert svc.retry_delivery(row['id'],row['owner'])
    while svc.deliver_one():
        pass
    assert len(meta.documents)==2
    assert svc.get(row['id'])['delivery']=='sent'


def test_restart_after_both_meta_acceptances_repairs_status(env):
    svc, _, meta=env
    row=svc.submit_spellcheck('wa:15551230000','whatsapp','English.docx',
        make_docx(['The studnts win.']),'last-ack')
    assert svc.process_one()
    while svc.deliver_one():
        pass
    assert svc.get(row['id'])['delivery']=='sent'
    assert len(meta.documents)==2
    svc.db.execute("UPDATE jobs SET delivery='queued' WHERE id=?",(row['id'],))
    _quiet_restart(svc)
    assert svc.get(row['id'])['delivery']=='sent'
    assert not svc.deliver_one()
    assert len(meta.documents)==2


def test_zero_corrections_counts_as_pass(env):
    svc, _, _=env
    row=svc.submit_spellcheck('web:owner','web','Clean.docx',make_docx(['The students are ready.']),'zero')
    assert svc.process_one()
    assert svc.get(row['id'])['state']=='completed'
    assert records.read_activity().splitlines()[-1].endswith('Spell/-- PASS')


def test_corrupted_saved_input_fails_safely_after_restart(env):
    svc, _, _=env
    row=svc.submit_spellcheck('web:owner','web','English.docx',
        make_docx(['The studnts win.']),'corrupt')
    (svc.s.data/'jobs'/row['id']/'teacher.docx').write_bytes(b'PK-corrupted')
    svc.update(row['id'],state='running')
    _quiet_restart(svc)
    assert svc.process_one()
    assert svc.get(row['id'])['state']=='failed'
    assert records.read_activity().splitlines()[-1].endswith('Spell/-- FAIL')


def test_missing_report_after_restart_recovers_from_original(env):
    svc, _, _=env
    row=svc.submit_spellcheck('web:owner','web','English.docx',
        make_docx(['The studnts win.']),'restore-report')
    assert svc.process_one()
    report=spellcheck.report_path_for(svc.get(row['id'])['output'])
    report.unlink()
    _quiet_restart(svc)
    assert svc.get(row['id'])['state']=='queued'
    assert svc.process_one()
    assert svc.get(row['id'])['state']=='completed'
    assert 'studnts -> students' in report.read_text(encoding='utf-8')
