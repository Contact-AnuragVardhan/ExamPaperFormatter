"""Results reach Web and WhatsApp with the same user-facing semantics as Windows."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import job
from service.whatsapp import WhatsAppFlow
from test_integration import submit

ROOT = Path(__file__).resolve().parents[1]
SENDER = '15551230000'
READY = 'Your exam is ready. You can proceed.'
FORMATTING_MESSAGE = 'Some formatting needs attention. You can proceed.'


def engine_result(status, word, detail, *, with_output=True):
    def run(*args, **kwargs):
        path = None
        if with_output:
            path = kwargs['output_dir'] / 'output.docx'
            path.write_bytes(word)
        return job.Result(status, detail, path)
    return run


def setup_whatsapp(svc, word, kind, engine_status, detail='', with_output=True):
    row = submit(svc, word, kind=kind, owner='wa:' + SENDER, channel='whatsapp')
    svc.db.save_session(SENDER, {'state': 'processing', 'job_id': row['id']})
    svc.engine = engine_result(engine_status, word, detail, with_output=with_output)
    assert svc.process_one()
    return svc.get(row['id'])


@pytest.mark.parametrize('engine_status,kind,detail,expected_status,expected_state', [
    ('PASS', 'format', '', 'ALL GOOD', 'completed'),
    ('FORMAT ERROR', 'check', 'The font does not match the reference.', 'FORMATTING', 'completed'),
    ('STRUCTURAL ERROR', 'format', 'The answer key is missing.', 'STRUCTURAL', 'needs_changes'),
])
def test_whatsapp_document_result_matches_windows(env, word, engine_status, kind, detail, expected_status, expected_state):
    svc, _, meta = env
    row = setup_whatsapp(svc, word, kind, engine_status, detail)
    assert row['state'] == expected_state
    assert row['result_status'] == expected_status
    assert row['message'] == (READY if expected_status == 'ALL GOOD' else FORMATTING_MESSAGE if expected_status == 'FORMATTING' else 'Please fix these before continuing:\n- The answer key is missing.\n\nUpload the corrected exam.')
    outgoing = svc.db.one("SELECT * FROM outbox WHERE job_id=? AND kind='document'", (row['id'],))
    caption = json.loads(outgoing['payload'])['caption']
    if expected_status == 'ALL GOOD':
        assert caption == READY
        assert Path(row['output']).name.startswith('Exam_Formatted_')
    else:
        assert caption.startswith('Issues report (not a formatted exam).\n')
        assert row['message'] in caption
        assert Path(row['output']).name.startswith('Exam_Issues_')

    assert svc.deliver_one()
    assert meta.documents[0][-1] == caption
    menu_rows = svc.db.all("SELECT payload FROM outbox WHERE job_id=? AND kind='interactive'", (row['id'],))
    assert menu_rows
    result_menu = json.loads(menu_rows[-1]['payload'])
    assert result_menu['header']['text'].startswith(expected_status)
    assert result_menu['body']['text'] == row['message']
    assert svc.get(row['id'])['state'] == expected_state


def test_whatsapp_clean_check_without_file_sends_result_and_menu(env, word):
    svc, _, meta = env
    row = setup_whatsapp(svc, word, 'check', 'OK', with_output=False)
    assert row['state'] == 'completed'
    assert row['result_status'] == 'ALL GOOD'
    assert row['output'] is None
    assert row['delivery'] == 'not_required'
    outgoing = svc.db.all('SELECT kind, payload FROM outbox ORDER BY id')
    assert [item['kind'] for item in outgoing] == ['text', 'interactive']
    assert json.loads(outgoing[0]['payload'])['text'] == READY
    menu = json.loads(outgoing[1]['payload'])
    assert menu['header']['text'] == 'ALL GOOD'
    assert svc.db.session(SENDER, 30)['state'] == 'result'
    assert svc.deliver_one()
    assert svc.deliver_one()
    assert meta.texts[0][1] == READY
    assert meta.interactives[0][1]['header']['text'] == 'ALL GOOD'
    assert meta.documents == []


def test_whatsapp_system_error_without_file_is_reported(env, word):
    svc, _, meta = env
    row = setup_whatsapp(svc, word, 'format', 'SYSTEM ERROR', 'provider failed', with_output=False)
    assert row['state'] == 'failed'
    assert row['result_status'] == 'SYSTEM ERROR'
    assert svc.db.session(SENDER, 30)['state'] == 'error'
    outbound = svc.db.all('SELECT kind, payload FROM outbox ORDER BY id')
    assert [item['kind'] for item in outbound] == ['text', 'interactive']
    assert json.loads(outbound[0]['payload'])['text'] == row['message']
    assert svc.deliver_one()
    assert meta.texts[0][1] == row['message']


def test_whatsapp_long_structural_caption_is_bounded():
    row = {'result_status': 'STRUCTURAL', 'message': 'Please fix these before continuing:\n' + 'Missing answer key.\n' * 90}
    caption = WhatsAppFlow.document_caption(row)
    assert len(caption) <= 1024
    assert 'Issues report (not a formatted exam)' in caption
    assert 'attached Word report' in caption


def test_web_result_badges_and_download_labels():
    """Exercise the actual JavaScript row renderer, without opening a browser."""
    if not shutil.which('node'):
        pytest.skip('Node.js is unavailable in this test environment')
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const items = {};
const fake = id => (items[id] ||= {addEventListener() {}, classList: {add() {}, remove() {}, toggle() {}}, files: []});
const context = {
  document: {getElementById: fake, querySelectorAll() {return []; }},
  fetch() {return Promise.reject(new Error('No network in this test'));},
  clearTimeout() {},
  setTimeout() {},
};
context.globalThis = context;
vm.runInNewContext(fs.readFileSync('web/app.js','utf8') + '\n;globalThis.renderJob=rowHtml;', context);
const base = {id:'job',filename:'teacher.docx',subject:'Math',grade:'10',kind:'check',created:1,has_output:true,message:'Some formatting needs attention. You can proceed.'};
const formatting = context.renderJob({...base, state:'completed', result_status:'FORMATTING'});
assert(formatting.includes('badge completed result-formatting'));
assert(formatting.includes('Download issues report'));
assert(!formatting.includes('Download formatted exam'));
const good = context.renderJob({...base, kind:'format', state:'completed', result_status:'ALL GOOD',message:'Your exam is ready. You can proceed.'});
assert(good.includes('badge completed result-all-good'));
assert(good.includes('Download formatted exam'));
const structural = context.renderJob({...base,state:'needs_changes',result_status:'STRUCTURAL'});
assert(structural.includes('badge needs_changes result-structural'));
assert(structural.includes('Download issues report'));
const noFile = context.renderJob({...base, state:'completed', result_status:'ALL GOOD',has_output:false});
assert(!noFile.includes('Download issues report'));
assert(!noFile.includes('Download formatted exam'));
const css = fs.readFileSync('web/styles.css','utf8');
for(const s of ['.badge.result-formatting','.badge.result-structural','.badge.result-all-good'])assert(css.includes(s));
console.log('Web result states render correctly');
'''
    p = subprocess.run(['node', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + '\n' + p.stderr
