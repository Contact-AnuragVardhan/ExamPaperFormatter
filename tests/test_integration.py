import hashlib
import hmac
import json
import time
from pathlib import Path
import pytest
import job
from service.files import UserError,validate_docx
from service.whatsapp import WhatsAppFlow
from conftest import login

H={'X-Exam-Client':'web'}

def test_failure_diagnostics_admin_only_redacted(env,word,monkeypatch):
    svc,c,_=env;login(c)
    monkeypatch.setenv('OPENAI_API_KEY','sk-private-test-key')
    ref=svc.references()[0]
    response=c.post('/api/jobs',headers=H,data={'kind':'format','reference_id':ref['id'],
        'reference_version':ref['version'],'submission_id':'diag'},files={'file':('Teacher.docx',word)})
    jid=response.json()['id']
    svc.engine=lambda *a,**kw:job.Result('SYSTEM ERROR','ignored detail',error_code='configuration',
        diagnostic={'stage':'submit_response','http_status':400,'error_type':'BadRequestError',
                    'message':'Unsupported model; sk-private-test-key admin-test Bearer opaque-key'})
    svc.process_one()
    teacher=c.get(f'/api/jobs/{jid}').json()
    assert teacher['state']=='failed' and not teacher['retryable']
    assert 'diagnostic' not in teacher and 'Unsupported model' not in json.dumps(teacher)
    assert c.get('/api/admin/overview').status_code==403
    login(c,'admin')
    admin=next(r for r in c.get('/api/admin/overview').json()['jobs'] if r['id']==jid)
    assert admin['diagnostic']['http_status']==400
    assert admin['diagnostic']['stage']=='submit_response'
    assert 'Unsupported model' in admin['diagnostic']['message']
    serialized=json.dumps(admin)
    assert all(s not in serialized for s in ('sk-private-test-key','admin-test','opaque-key'))

def test_old_sqlite_schema_migrates_without_erasing_requests(env,word):
    from service.storage import Store
    svc,_,_=env
    row=submit(svc,word)
    svc.update(row['id'],state='uncertain',result_status='SYSTEM ERROR')
    svc.db.execute('ALTER TABLE jobs DROP COLUMN diagnostic')
    store=Store(svc.s.data)
    recovered=store.one('SELECT * FROM jobs WHERE id=?',(row['id'],))
    assert recovered['state']=='uncertain' and recovered['diagnostic'] is None
    assert recovered['filename']==row['filename'] and recovered['owner']==row['owner']
    assert 'older request' in svc.admin_public(recovered)['diagnostic']['message']

def test_unknown_response_reconciliation_requires_confirmation(env,word):
    svc,c,_=env
    row=submit(svc,word)
    svc.update(row['id'],state='uncertain',remote=json.dumps({'submitting':True}))
    login(c,'admin')
    path=f'/api/admin/jobs/{row["id"]}/resolve'
    assert c.post(path,headers=H,json={}).status_code==400
    assert svc.get(row['id'])['state']=='uncertain'
    assert c.post(path,headers=H,json={'confirmed_no_remote_request':True}).status_code==200
    assert svc.get(row['id'])['state']=='failed'

def test_engine_crash_diagnostic_retained_without_unsafe_retry(env,word):
    svc,_,_=env;row=submit(svc,word)
    def fail(*a,**kw): raise RuntimeError('worker unexpected failure')
    svc.engine=fail;svc.process_one()
    row=svc.get(row['id'])
    assert row['state']=='uncertain' and row['result_status']=='SYSTEM ERROR' and not row['retryable']
    assert json.loads(row['diagnostic'])['message']=='worker unexpected failure'

def send(flow,body='',typ='text',mid=None):
    if typ=='text' and (body.startswith(('main.','menu.','confirm.','help.','upload.','processing.','error.','subject.'))):
        state=flow.db.session('15551230000',30)
        action=body
        if body.startswith('subject.') and body.split('.',1)[1] in ('math','chemistry','english'):
            action='subject.'+next(r['id'] for r in flow.v.references() if r['subject']==body.split('.',1)[1])
        selected=next(k for k,v in state['menu_actions'].items() if v==action)
        msg={'type':'interactive','interactive':{'list_reply':{'id':selected}}}
    else:
        msg={'type':typ,'text':{'body':body}} if typ=='text' else {'type':'document','document':{'id':'media','filename':body}}
    flow.handle('15551230000',msg,mid or str(time.time_ns()))

def submit(svc,word,kind='format',idem='one',rid=None,owner='web:test',channel='web'):
    rid=rid or next(r['id'] for r in svc.references() if r['subject']=='math')
    return svc.submit(owner,channel,kind,rid,'Teacher.docx',word,idem)

def mock_engine(status,word=None):
    def engine(*args,**kw):
        path=None
        if word:
            path=kw['output_dir']/'returned.docx';path.write_bytes(word)
        return job.Result(status,'some detail',path)
    return engine

def test_references_english_unavailable(env):
    svc,c,_=env
    assert len(svc.references())==3
    assert [r['subject'] for r in svc.references() if r['enabled']]==['math','chemistry']
    login(c,'admin');r=next(r for r in svc.references() if r['subject']=='english')
    assert c.post(f'/api/admin/references/{r["id"]}/enabled',headers=H,json={'enabled':True}).status_code==200
    assert svc.resolve(r['id'])[0]['subject']=='english'

@pytest.mark.parametrize('name,payload',[('exam.pdf',b'pdf'),('exam.docx',b'PKbad'),('exam.docx',b'')])
def test_invalid_files(name,payload):
    with pytest.raises(UserError): validate_docx(payload,name,1024)

def test_limit(word):
    with pytest.raises(UserError):validate_docx(word,'exam.docx',10)

def test_isolation_and_idempotency(env,word):
    svc,_,_=env
    first=submit(svc,word)
    assert submit(svc,word)['id']==first['id']
    with pytest.raises(UserError):submit(svc,word,idem='two')
    other=submit(svc,word,owner='web:other',rid=next(r['id'] for r in svc.references() if r['subject']=='chemistry'))
    assert first['id']!=other['id']
    folder=svc.s.data/'jobs'/first['id']
    pinned=(folder/'reference.docx').read_bytes()
    (svc.s.data/'references'/(first['reference_id']+'.docx')).write_bytes(word+b'changed')
    assert (folder/'reference.docx').read_bytes()==pinned
    assert other['subject']=='chemistry'

@pytest.mark.parametrize('status,kind,output,state',[('PASS','format',True,'completed'),('OK','check',False,'completed'),('FORMAT ERROR','check',True,'needs_changes'),('STRUCTURAL ERROR','format',True,'needs_changes'),('SYSTEM ERROR','format',False,'failed'),('PASS','format',False,'failed')])
def test_engine_outcomes(env,word,status,kind,output,state):
    svc,_,_=env;svc.engine=mock_engine(status,word if output else None)
    row=submit(svc,word,kind)
    svc.process_one();result=svc.get(row['id']);assert result['state']==state
    if result['output']:assert Path(result['output']).read_bytes()==word
    if state=='needs_changes':assert 'Issues' in result['output']

def test_invalid_output_not_success(env,word):
    svc,_,_=env;svc.engine=mock_engine('PASS',b'garbage');row=submit(svc,word)
    svc.process_one();result=svc.get(row['id']);assert result['state']!='completed';assert not result['output']

def test_cancel_only_queued(env,word):
    svc,_,_=env;row=submit(svc,word)
    assert svc.cancel(row['id'],row['owner']);assert not svc.process_one()
    row=submit(svc,word,idem='second');svc.update(row['id'],state='running')
    assert not svc.cancel(row['id'],row['owner'])

def test_hi_from_every_state(env):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    for state in ('main','subject','upload','confirm','processing','result','error','help'):
        svc.db.save_session('15551230000',{'state':state,'job_id':'old'})
        send(flow,'Hi')
        assert svc.db.session('15551230000',30)['state']=='main'
        last=json.loads(svc.db.all('SELECT * FROM outbox ORDER BY id DESC LIMIT 1')[0]['payload'])
        assert 'Welcome to *Exam Formatter*' in last['body']['text'] and 'Main Menu' in last['header']['text']

def test_whatsapp_happy_path_double_confirm_and_hi(env,word):
    svc,_,meta=env;meta.input=word;flow=WhatsAppFlow(svc);svc.engine=mock_engine('PASS',word)
    send(flow,'Hi');send(flow,'main.format');send(flow,'subject.math');send(flow,'Exam.docx','document');send(flow,'confirm.start',mid='confirm')
    state=svc.db.session('15551230000',30);assert state['state']=='processing'
    send(flow,'1',mid='double-confirm');assert len(svc.db.all('SELECT * FROM jobs'))==1
    send(flow,'Hi');assert svc.active('wa:15551230000')
    svc.process_one()
    for _ in range(20):
        if not svc.deliver_one():break
    assert len(meta.documents)==1
    assert meta.uploads==[word]
    assert svc.db.session('15551230000',30)['state']=='main'

def test_whatsapp_check_and_help_back(env,word):
    svc,_,meta=env;meta.input=word;flow=WhatsAppFlow(svc)
    send(flow,'Hi');send(flow,'main.check');send(flow,'subject.math');send(flow,'menu.help');send(flow,'help.back')
    assert svc.db.session('15551230000',30)['state']=='upload'
    send(flow,'file.docx','document');send(flow,'confirm.start')
    assert svc.db.all('SELECT * FROM jobs')[0]['kind']=='check'

def test_bad_option_invalid_file_replace_change_subject(env,word):
    svc,_,meta=env;meta.input=word;flow=WhatsAppFlow(svc)
    send(flow,'Hi');send(flow,'main.format');send(flow,'99');assert svc.db.session('15551230000',30)['state']=='subject'
    send(flow,'subject.math');send(flow,'file.pdf','document');assert svc.db.session('15551230000',30)['state']=='upload'
    send(flow,'one.docx','document');send(flow,'two.docx','document');assert svc.db.session('15551230000',30)['filename']=='two.docx'
    send(flow,'confirm.subject');send(flow,'subject.chemistry');state=svc.db.session('15551230000',30);assert state['state']=='confirm'
    assert svc.resolve(state['reference_id'])[0]['subject']=='chemistry'
    send(flow,'confirm.cancel');assert svc.db.session('15551230000',30)['state']=='main'
    assert not list((svc.s.data/'pending').iterdir())

def test_delivery_retry_same_file_without_new_engine_run(env,word):
    svc,_,meta=env;svc.engine=mock_engine('PASS',word)
    row=submit(svc,word,owner='wa:15551230000',channel='whatsapp');svc.process_one();meta.fail=True
    for _ in range(3):
        svc.db.execute('UPDATE outbox SET due=0');svc.deliver_one()
    assert svc.get(row['id'])['delivery']=='failed'
    assert svc.retry_delivery(row['id'],row['owner']);meta.fail=False
    svc.db.execute('UPDATE outbox SET due=0');svc.deliver_one()
    assert svc.get(row['id'])['delivery']=='sent'
    assert len(svc.db.all('SELECT * FROM jobs'))==1 and meta.uploads==[word]
    svc.delivery_status('wamid.result','delivered');svc.delivery_status('wamid.result','sent')
    assert svc.get(row['id'])['delivery']=='delivered'

def test_web_auth_upload_owner_protection_admin(env,word):
    svc,c,_=env
    assert c.get('/api/references').status_code==401
    assert login(c).status_code==200
    refs=c.get('/api/references').json();ref=next(r for r in refs if r['subject']=='math')
    fields={'reference_id':ref['id'],'reference_version':ref['version'],'kind':'format','submission_id':'web-one'}
    r=c.post('/api/jobs',headers=H,data=fields,files={'file':('Exam.docx',word,'application/octet-stream')});assert r.status_code==200,r.text
    jid=r.json()['id'];assert c.post('/api/jobs',headers=H,data=fields,files={'file':('Exam.docx',word)}).json()['id']==jid
    svc.engine=mock_engine('PASS',word);svc.process_one()
    assert c.get(f'/api/jobs/{jid}/download').content==word
    assert c.get('/api/admin/overview').status_code==403
    login(c);assert c.get(f'/api/jobs/{jid}').status_code==404
    login(c,'admin');assert c.get(f'/api/jobs/{jid}/download').content==word
    assert c.get('/api/admin/overview').status_code==200

def signed_post(c,payload):
    body=json.dumps(payload).encode();sig='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    return c.post('/webhooks/whatsapp',content=body,headers={'X-Hub-Signature-256':sig,'Content-Type':'application/json'})

def test_webhook_signature_verify_dedupe_no_inline_processing(env):
    svc,c,_=env
    assert c.get('/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=123').text=='123'
    assert c.post('/webhooks/whatsapp',json={}).status_code==401
    payload={'entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'phone'},'messages':[{'from':'15551230000','id':'wamid.incoming','type':'text','text':{'body':'Hi'}}]}}]}]}
    assert signed_post(c,payload).status_code==200;assert signed_post(c,payload).status_code==200
    assert len(svc.db.all('SELECT * FROM inbox'))==1
    assert svc.db.all('SELECT * FROM jobs')==[]
    assert svc.db.all('SELECT * FROM outbox')==[]

def test_csrf(env):
    _,c,_=env;login(c)
    assert c.post('/api/logout',headers={**H,'Origin':'https://evil.example'}).status_code==403
    assert c.post('/api/logout').status_code==403

def test_admin_reference_crud(env,word):
    svc,c,_=env;login(c,'admin')
    r=c.post('/api/admin/references',headers=H,data={'subject':'Physics','grade':'12','enabled':'false'},files={'file':('ref.docx',word)})
    assert r.status_code==200,r.text
    ref=next(r for r in svc.references() if r['subject']=='Physics');assert not ref['enabled']
    assert c.get(f'/api/admin/references/{ref["id"]}/download').content==word
    assert c.delete(f'/api/admin/references/{ref["id"]}',headers=H).status_code==200
    assert not any(r['subject']=='Physics' for r in svc.references())

def test_missing_reference(env,word):
    svc,_,_=env;r=svc.references()[0];(svc.s.data/'references'/(r['id']+'.docx')).unlink()
    with pytest.raises(UserError):submit(svc,word,rid=r['id'])

def test_safe_retry_and_uncertain_block(env,word):
    svc,_,_=env;svc.engine=lambda *a,**kw:job.Result('SYSTEM ERROR','busy',retryable=True)
    row=submit(svc,word);svc.process_one();retry=svc.retry(row['id'],row['owner'],'retry1');assert retry['state']=='queued'
    svc.engine=lambda *a,**kw:job.Result('SYSTEM ERROR','timeout unknown',uncertain=True)
    svc.process_one();assert svc.get(retry['id'])['state']=='uncertain'
    with pytest.raises(UserError):submit(svc,word,idem='new')
    with pytest.raises(UserError):svc.retry(retry['id'],row['owner'],'retry2')

def test_retention_and_session_expiry(env,word):
    svc,_,_=env;row=submit(svc,word);svc.cancel(row['id'],row['owner'])
    svc.db.execute('UPDATE jobs SET updated=0 WHERE id=?',(row['id'],))
    active=submit(svc,word,owner='web:active',idem='active')
    svc.db.execute('UPDATE jobs SET updated=0 WHERE id=?',(active['id'],))
    svc.db.save_session('15551230000',{'state':'confirm'});svc.db.execute('UPDATE sessions SET updated=0')
    assert svc.db.session('15551230000',30)=={'state':'main'}
    svc.cleanup();assert svc.get(row['id']) is None;assert svc.get(active['id'])

@pytest.mark.parametrize('subject',['math','chemistry','english'])
def test_every_subject_gets_only_its_own_reference(env,word,subject):
    svc,_,_=env
    ref=next(r for r in svc.references() if r['subject']==subject)
    if not ref['enabled']:svc.db.execute('INSERT OR REPLACE INTO reference_settings VALUES(?,1)',(ref['id'],))
    reference_data=(svc.s.data/'references'/(ref['id']+'.docx')).read_bytes()
    def engine(kind,actual_subject,grade,reference,teacher,*a,**kw):
        assert actual_subject==subject
        assert reference.read_bytes()==reference_data
        assert teacher.read_bytes()==word
        return job.Result('OK','')
    svc.engine=engine
    row=submit(svc,word,'check',rid=ref['id']);svc.process_one()
    assert svc.get(row['id'])['state']=='completed'
