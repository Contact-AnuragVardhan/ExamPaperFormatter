"""Native admin workflows, shared web state, and password/session boundaries."""
import hashlib
import hmac
import json
import time
from pathlib import Path
import pytest
from service.whatsapp import WhatsAppFlow
from service.whatsapp_admin import WhatsAppAdmin
from service.menus import replies
from test_interactive_menus import latest,choice,tap,assert_limits,SENDER
from test_integration import send,submit,mock_engine,H
from conftest import login

def admin(env,password='admin-test'):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    send(flow,'Hi');tap(svc,flow,'main.admin');send(flow,password)
    assert svc.admin_authenticated(SENDER)
    return svc,flow

def pick_reference(svc,flow,subject='english'):
    tap(svc,flow,'admin.refs')
    state=svc.db.session(SENDER,30)
    rid=next(r['id'] for r in svc.references() if r['subject']==subject)
    index=state['ref_ids'].index(rid)
    tap(svc,flow,'admin.ref.'+str(index))
    return rid

def open_job(svc,flow,jid,filter_name='all'):
    tap(svc,flow,'admin.jobs');tap(svc,flow,'admin.jobs.'+filter_name)
    state=svc.db.session(SENDER,30);index=state['admin_job_ids'].index(jid)
    tap(svc,flow,'admin.job.'+str(index))

def webhook(svc,c,msg,mid=None,sender=SENDER):
    mid=mid or 'wamid.'+str(time.time_ns())
    payload={'entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'phone'},'messages':[dict(msg,id=mid,**{'from':sender})]}}]}]}
    body=json.dumps(payload).encode()
    signature='sha256='+hmac.new(svc.s.wa_app_secret.encode(),body,hashlib.sha256).hexdigest()
    response=c.post('/webhook',content=body,headers={'Content-Type':'application/json','X-Hub-Signature-256':signature})
    assert response.status_code==200
    svc.handle_incoming(mid)
    return mid

def test_admin_requires_password_not_teacher_code(env):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    send(flow,'Hi');tap(svc,flow,'main.admin');send(flow,'teacher-test')
    assert not svc.admin_authenticated(SENDER)
    assert svc.db.session(SENDER,30)['state']=='admin_login'
    assert 'Incorrect' in latest(svc)['body']['text']
    send(flow,'admin-test');assert svc.admin_authenticated(SENDER)
    assert set(svc.db.session(SENDER,30)['menu_actions'].values())=={'admin.refs','admin.add','admin.jobs','admin.activity','admin.cost','admin.system','admin.help','admin.logout'}

def test_unicode_password_exact_and_constant_time_bytes(env):
    svc,_,_=env;svc.s.admin_password='密碼🔑 é'
    admin(env,'密碼🔑 é')

def test_rate_limit_rejects_sixth_password_attempt_even_if_correct(env):
    svc,_,_=env;flow=WhatsAppFlow(svc);send(flow,'Hi');tap(svc,flow,'main.admin')
    for _ in range(5): send(flow,'wrong-password')
    send(flow,'admin-test')
    assert not svc.admin_authenticated(SENDER)
    assert 'five minutes' in latest(svc)['body']['text']

@pytest.mark.parametrize('action',['hi','logout','main'])
def test_admin_logout_and_stale_controls_cannot_mutate(env,action):
    svc,flow=admin(env);rid=pick_reference(svc,flow)
    old=choice(svc,'admin.ref.toggle')
    if action=='hi': send(flow,'Hi')
    elif action=='logout': tap(svc,flow,'admin.home');tap(svc,flow,'admin.logout')
    else:
        svc.emit_reply(SENDER,{'type':'button','body':{'text':'Back'},'action':{'buttons':[{'type':'reply','reply':{'id':'menu.main','title':'Main Menu'}}]}})
        tap(svc,flow,'menu.main')
    assert not svc.admin_authenticated(SENDER)
    tap(svc,flow,'admin.ref.toggle',old_id=old)
    assert svc.db.session(SENDER,30)['state']=='main'
    assert not svc.admin_reference(rid)['configured_enabled']

def test_idle_expiry_ignores_admin_mutation_and_clears_pending(env,word):
    svc,flow=admin(env);pick_reference(svc,flow)
    tap(svc,flow,'admin.ref.replace');svc.meta.input=word;send(flow,'ref.docx','document')
    pid=svc.db.session(SENDER,30)['pending_id'];old=choice(svc,'admin.upload.save')
    svc.db.execute('UPDATE whatsapp_admin SET expires=0 WHERE sender=?',(SENDER,))
    tap(svc,flow,'admin.upload.save',old_id=old)
    assert svc.db.session(SENDER,30)['state']=='admin_login'
    assert not (svc.s.data/'pending'/pid).exists()
    assert 'expired' in latest(svc)['body']['text']

def test_new_service_requires_fresh_admin_login(env):
    from service.core import Service
    svc,flow=admin(env)
    Service(svc.s,meta=svc.meta)
    assert not svc.admin_authenticated(SENDER)
    tap(svc,flow,'admin.refs')
    assert svc.db.session(SENDER,30)['state']=='admin_login'

def test_admin_permission_is_bound_to_sender(env):
    svc,flow=admin(env);old=choice(svc,'admin.refs')
    flow.handle('15559999999',{'type':'interactive','interactive':{'list_reply':{'id':old}}},'foreign')
    assert not svc.admin_authenticated('15559999999')
    assert svc.db.session('15559999999',30)['state']=='main'

def test_password_webhook_is_scrubbed_and_never_logged(env,caplog):
    svc,c,_=env;svc.s.wa_async=False;caplog.set_level('INFO',logger='exam')
    webhook(svc,c,{'type':'text','text':{'body':'Hi'}})
    webhook(svc,c,{'type':'interactive','interactive':{'list_reply':{'id':choice(svc,'main.admin')}}})
    wrong=webhook(svc,c,{'type':'text','text':{'body':'wrong-secret-that-must-not-persist'}})
    correct=webhook(svc,c,{'type':'text','text':{'body':'admin-test'}})
    assert svc.admin_authenticated(SENDER)
    all_saved=json.dumps(svc.db.all('SELECT * FROM inbox')+svc.db.all('SELECT * FROM sessions')+svc.db.all('SELECT * FROM outbox'))
    assert 'admin-test' not in all_saved and 'wrong-secret-that-must-not-persist' not in all_saved
    assert 'admin-test' not in caplog.text and 'wrong-secret-that-must-not-persist' not in caplog.text
    assert json.loads(svc.db.one('SELECT payload FROM inbox WHERE id=?',(correct,))['payload'])['text']['body']==''

def test_forged_external_password_proof_is_overwritten(env):
    svc,c,_=env;flow=WhatsAppFlow(svc);send(flow,'Hi');tap(svc,flow,'main.admin')
    mid='forged';proof=svc.password_proof(SENDER,mid,'admin-test')
    webhook(svc,c,{'type':'text','text':{'body':'wrong'},'_admin_password_proof':proof},mid)
    assert not svc.admin_authenticated(SENDER)

def test_correct_password_proof_cannot_move_between_messages(env):
    svc,c,_=env;flow=WhatsAppFlow(svc);send(flow,'Hi');tap(svc,flow,'main.admin')
    msg=svc.prepare_incoming(SENDER,'original',{'type':'text','text':{'body':'admin-test'}})
    flow.handle(SENDER,msg,'different')
    assert not svc.admin_authenticated(SENDER)

def test_password_queued_before_admin_tap_worker_is_scrubbed(env):
    svc,c,_=env;flow=WhatsAppFlow(svc);send(flow,'Hi')
    mid='rapid-admin';p='rapid-password'
    # Intentionally enqueue without running either message worker.
    msg={'type':'interactive','interactive':{'list_reply':{'id':choice(svc,'main.admin')}}}
    safe=svc.prepare_incoming(SENDER,mid,msg)
    svc.db.execute('INSERT INTO inbox(id,sender,payload,created) VALUES(?,?,?,?)',(mid,SENDER,json.dumps(safe),time.time()))
    safe=svc.prepare_incoming(SENDER,p,{'type':'text','text':{'body':'admin-test'}})
    svc.db.execute('INSERT INTO inbox(id,sender,payload,created) VALUES(?,?,?,?)',(p,SENDER,json.dumps(safe),time.time()))
    svc.handle_incoming(mid);svc.handle_incoming(p)
    assert svc.admin_authenticated(SENDER)
    assert 'admin-test' not in svc.db.one('SELECT payload FROM inbox WHERE id=?',(p,))['payload']

def test_enable_disable_is_confirmed_and_shared_with_web(env):
    svc,flow=admin(env);c=env[1];rid=pick_reference(svc,flow)
    tap(svc,flow,'admin.ref.toggle')
    assert not svc.admin_reference(rid)['enabled']
    tap(svc,flow,'admin.toggle.yes');assert svc.admin_reference(rid)['enabled']
    login(c,'admin');assert next(r for r in c.get('/api/admin/overview').json()['references'] if r['id']==rid)['enabled']
    tap(svc,flow,'admin.ref.toggle');tap(svc,flow,'admin.toggle.yes')
    assert not svc.admin_reference(rid)['enabled']
    assert rid not in [r['id'] for r in svc.references() if r['enabled']]

def test_reference_replace_exact_bytes_preserves_id_and_enable_state(env,word):
    svc,flow=admin(env);rid=pick_reference(svc,flow,'math')
    svc.meta.input=word+b'new-reference';old=svc.admin_reference(rid)['version']
    tap(svc,flow,'admin.ref.replace');send(flow,'Certified.docx','document')
    assert svc.admin_reference(rid)['version']==old
    stale=choice(svc,'admin.upload.save');tap(svc,flow,'admin.upload.save')
    ref=svc.admin_reference(rid)
    assert ref['enabled'] and ref['version']==hashlib.sha256(word+b'new-reference').hexdigest()
    assert (svc.s.data/'references'/(rid+'.docx')).read_bytes()==word+b'new-reference'
    assert not svc.db.all('SELECT * FROM jobs')
    tap(svc,flow,'admin.upload.save',old_id=stale)
    assert len(svc.references())==3

def test_new_reference_subject_grade_menu_defaults_disabled(env,word):
    svc,flow=admin(env);svc.meta.input=word
    tap(svc,flow,'admin.add')
    state=svc.db.session(SENDER,30);index=state['subject_options'].index('biology')
    tap(svc,flow,'admin.subject.'+str(index));tap(svc,flow,'admin.next')
    tap(svc,flow,'admin.grade.9') # Class 10 is on the second page.
    send(flow,'Biology10.docx','document');tap(svc,flow,'admin.upload.save')
    ref=next(r for r in svc.references() if r['subject']=='biology')
    assert ref['grade']=='10' and not ref['enabled']
    login(env[1],'admin')
    assert any(r['id']==ref['id'] for r in env[1].get('/api/admin/overview').json()['references'])

def test_custom_subject_and_grade_entry_and_explicit_enable(env,word):
    svc,flow=admin(env);svc.meta.input=word
    tap(svc,flow,'admin.add');tap(svc,flow,'admin.next')
    state=svc.db.session(SENDER,30);idx=state['subject_options'].index(None)
    tap(svc,flow,'admin.subject.'+str(idx));send(flow,'Environmental Studies')
    tap(svc,flow,'admin.next');tap(svc,flow,'admin.grade.13');send(flow,'Pre-K')
    send(flow,'New.docx','document');tap(svc,flow,'admin.upload.toggle');tap(svc,flow,'admin.upload.save')
    ref=next(r for r in svc.references() if r['subject']=='Environmental Studies')
    assert ref['grade']=='Pre-K' and ref['enabled']

def test_invalid_reference_upload_never_changes_stored_paper(env):
    svc,flow=admin(env);rid=pick_reference(svc,flow);old=svc.admin_reference(rid)['version']
    tap(svc,flow,'admin.ref.replace');svc.meta.input=b'corrupt';send(flow,'Bad.docx','document')
    assert svc.db.session(SENDER,30)['state']=='admin_upload'
    assert svc.admin_reference(rid)['version']==old
    assert not svc.db.all('SELECT * FROM jobs')

def test_replacement_conflict_with_web_change_is_rejected(env,word):
    svc,flow=admin(env);rid=pick_reference(svc,flow,'math');tap(svc,flow,'admin.ref.replace')
    svc.meta.input=word;send(flow,'pending.docx','document')
    ref=svc.admin_reference(rid);svc.save_reference(ref['subject'],ref['grade'],word+b'web-update',True)
    tap(svc,flow,'admin.upload.save')
    assert 'changed while' in latest(svc)['body']['text']
    assert (svc.s.data/'references'/(rid+'.docx')).read_bytes()==word+b'web-update'

def test_delete_requires_confirmation_preserves_pinned_jobs(env,word):
    svc,flow=admin(env);row=submit(svc,word);rid=pick_reference(svc,flow,'math')
    pinned=(svc.s.data/'jobs'/row['id']/'reference.docx').read_bytes()
    tap(svc,flow,'admin.ref.delete');tap(svc,flow,'admin.delete.no')
    assert svc.admin_reference(rid)
    tap(svc,flow,'admin.ref.delete');old=choice(svc,'admin.delete.yes');tap(svc,flow,'admin.delete.yes')
    assert not any(r['id']==rid for r in svc.references())
    assert (svc.s.data/'jobs'/row['id']/'reference.docx').read_bytes()==pinned
    tap(svc,flow,'admin.delete.yes',old_id=old)
    assert len(svc.references())==2

def test_concurrent_web_deletion_returns_fresh_list(env):
    svc,flow=admin(env);rid=pick_reference(svc,flow,'math');svc.delete_reference(rid)
    tap(svc,flow,'admin.ref.replace')
    assert svc.db.session(SENDER,30)['state']=='admin_refs'
    assert 'no longer exists' in latest(svc)['body']['text']

def test_reference_download_delivers_exact_bytes_without_formatting(env):
    svc,flow=admin(env);rid=pick_reference(svc,flow,'math')
    data=(svc.s.data/'references'/(rid+'.docx')).read_bytes()
    tap(svc,flow,'admin.ref.download')
    while svc.deliver_one(): pass
    assert svc.meta.uploads[-1]==data and svc.meta.documents[-1][0]==SENDER
    assert not svc.db.all('SELECT * FROM jobs')

def test_logout_cancels_queued_privileged_download(env):
    svc,flow=admin(env);pick_reference(svc,flow,'math');tap(svc,flow,'admin.ref.download');send(flow,'Hi')
    while svc.deliver_one(): pass
    assert not svc.meta.documents
    assert svc.db.one("SELECT state FROM outbox WHERE kind='document'")['state']=='cancelled'

@pytest.mark.parametrize('screen',['admin_home','admin_refs','admin_ref','admin_subject','admin_grade','admin_newsubject','admin_newgrade','admin_upload','admin_confirm','admin_delete','admin_toggle','admin_job_filters','admin_jobs','admin_job','admin_diagnostics','admin_job_confirm','admin_activity','admin_cost','admin_system','admin_help'])
def test_admin_menu_constraints(env,word,screen):
    svc,flow=admin(env);rid=svc.references()[0]['id'];row=submit(svc,word)
    state={'state':screen,'admin_ref_id':rid,'admin_subject':'math','admin_grade':'10','filename':'ref.docx','upload_enabled':False,'toggle_enabled':False,'admin_job_id':row['id'],'job_operation':'retry'}
    svc.db.save_session(SENDER,state)
    svc.emit_reply(SENDER,flow.current_menu(SENDER))
    assert_limits(latest(svc))

def test_admin_job_cancel_does_not_cancel_running_request(env,word):
    svc,flow=admin(env);row=submit(svc,word);open_job(svc,flow,row['id'])
    tap(svc,flow,'admin.job.cancel');svc.update(row['id'],state='running');tap(svc,flow,'admin.job.confirm')
    assert svc.get(row['id'])['state']=='running'
    assert 'not possible' in latest(svc)['body']['text']

def test_safe_job_retry_is_confirmed_and_stale_tap_no_duplicate(env,word):
    svc,flow=admin(env);row=submit(svc,word);svc.update(row['id'],state='failed',retryable=1,message='Busy')
    open_job(svc,flow,row['id']);tap(svc,flow,'admin.job.retry')
    assert len(svc.db.all('SELECT * FROM jobs'))==1
    old=choice(svc,'admin.job.confirm');tap(svc,flow,'admin.job.confirm')
    assert len(svc.db.all('SELECT * FROM jobs'))==2
    new=svc.get(svc.db.session(SENDER,30)['admin_job_id']);assert new['owner']==row['owner'] and new['channel']=='web'
    tap(svc,flow,'admin.job.confirm',old_id=old);assert len(svc.db.all('SELECT * FROM jobs'))==2

@pytest.mark.parametrize('remote',[False,True])
def test_uncertain_reconciliation_requires_explicit_confirmation(env,word,remote):
    svc,flow=admin(env);row=submit(svc,word);svc.update(row['id'],state='uncertain',remote=json.dumps({'response_id':'resp-existing'} if remote else {'submitting':True}))
    open_job(svc,flow,row['id'],'issues');tap(svc,flow,'admin.job.resolve')
    assert svc.get(row['id'])['state']=='uncertain'
    if not remote: assert 'OpenAI dashboard' in latest(svc)['body']['text']
    tap(svc,flow,'admin.job.confirm')
    assert svc.get(row['id'])['state']==('queued' if remote else 'failed')
    assert len(svc.db.all('SELECT * FROM jobs'))==1

def test_diagnostics_are_admin_only_redacted_and_no_secrets(env,word):
    svc,flow=admin(env);row=submit(svc,word);svc.update(row['id'],state='failed',diagnostic=json.dumps({'stage':'submit_response','http_status':400,'message':'wrong model admin-test token'}))
    open_job(svc,flow,row['id']);tap(svc,flow,'admin.job.diagnostics')
    body=latest(svc)['body']['text'];assert 'submit_response' in body and '400' in body
    assert 'admin-test' not in body and ' token' not in body

def test_saved_job_result_download_copy_leaves_delivery_unchanged(env,word):
    svc,flow=admin(env);row=submit(svc,word,owner='wa:15550009999',channel='whatsapp')
    svc.engine=mock_engine('PASS',word);svc.process_one()
    open_job(svc,flow,row['id']);tap(svc,flow,'admin.job.download')
    # Deliver the normal teacher result and then the independent admin copy.
    while svc.deliver_one(): pass
    assert svc.meta.uploads==[word,word]
    assert svc.meta.documents[0][0]=='15550009999' and svc.meta.documents[1][0]==SENDER
    assert svc.get(row['id'])['delivery']=='sent'

def test_activity_cost_status_and_help_navigation(env):
    svc,flow=admin(env)
    for action,fragment in [('admin.activity','Page'),('admin.cost','local estimates'),('admin.system','Model:'),('admin.help','Reference Papers')]:
        tap(svc,flow,action);assert fragment in latest(svc)['body']['text'];tap(svc,flow,'admin.home')
    send(flow,'1');assert svc.db.session(SENDER,30)['state']=='admin_home'

def test_many_reference_and_request_pages_stay_selectable(env,word):
    svc,flow=admin(env)
    for i in range(17): svc.save_reference('custom'+str(i),'10',word,False)
    tap(svc,flow,'admin.refs');seen=set()
    for _ in range(3):
        assert_limits(latest(svc))
        seen.update(v for v in svc.db.session(SENDER,30)['menu_actions'].values() if v.startswith('admin.ref.'))
        if 'admin.next' in svc.db.session(SENDER,30)['menu_actions'].values(): tap(svc,flow,'admin.next')
    assert len(seen)==20
    tap(svc,flow,'admin.home')
    for i in range(17): submit(svc,word,owner='web:page'+str(i))
    tap(svc,flow,'admin.jobs');tap(svc,flow,'admin.jobs.all');seen=set()
    for _ in range(3):
        assert_limits(latest(svc));seen.update(v for v in svc.db.session(SENDER,30)['menu_actions'].values() if v.startswith('admin.job.'))
        if 'admin.next' in svc.db.session(SENDER,30)['menu_actions'].values(): tap(svc,flow,'admin.next')
    assert len(seen)==17

def test_hi_resets_every_admin_screen_and_preserves_active_teacher_job(env,word):
    svc,flow=admin(env);row=submit(svc,word,owner='wa:'+SENDER,channel='whatsapp')
    names=['admin_home','admin_refs','admin_ref','admin_subject','admin_grade','admin_newsubject','admin_newgrade','admin_upload','admin_confirm','admin_delete','admin_toggle','admin_job_filters','admin_jobs','admin_job','admin_diagnostics','admin_job_confirm','admin_activity','admin_cost','admin_system','admin_help','admin_login']
    for screen in names:
        svc.db.execute('INSERT OR REPLACE INTO whatsapp_admin VALUES(?,?)',(SENDER,time.time()+1800))
        svc.db.save_session(SENDER,{'state':screen})
        send(flow,'Hi')
        assert svc.db.session(SENDER,30)['state']=='main'
        assert not svc.admin_authenticated(SENDER)
        assert svc.get(row['id'])['state']=='queued'

def test_admin_delivery_retry_sends_saved_teacher_bytes_no_formatter_call(env,word):
    svc,flow=admin(env);owner='wa:15550002222';row=submit(svc,word,owner=owner,channel='whatsapp')
    called=[]
    def engine(*args,**kw):
        called.append(1);return mock_engine('PASS',word)(*args,**kw)
    svc.engine=engine;svc.process_one();svc.meta.fail=True
    while svc.deliver_one():
        svc.db.execute("UPDATE outbox SET due=0 WHERE state='queued'")
    assert svc.get(row['id'])['delivery']=='failed'
    open_job(svc,flow,row['id'],'delivery');tap(svc,flow,'admin.job.delivery');tap(svc,flow,'admin.job.confirm')
    svc.meta.fail=False
    while svc.deliver_one(): pass
    assert called==[1] and len(svc.db.all('SELECT * FROM jobs'))==1
    assert svc.meta.uploads==[word]
    assert svc.meta.documents[-1][0]==owner[3:]
    assert svc.get(row['id'])['delivery']=='sent'

def test_incoming_result_does_not_overwrite_current_admin_screen(env,word):
    svc,flow=admin(env);row=submit(svc,word,owner='wa:'+SENDER,channel='whatsapp')
    tap(svc,flow,'admin.refs');svc.engine=mock_engine('PASS',word);svc.process_one()
    while svc.deliver_one(): pass
    assert svc.db.session(SENDER,30)['state']=='admin_refs'
    assert 'Reference Papers' in latest(svc)['header']['text']

def test_privileged_download_failure_offers_fresh_controls(env):
    svc,flow=admin(env);pick_reference(svc,flow,'math');tap(svc,flow,'admin.ref.download');svc.meta.fail=True
    while svc.deliver_one(): svc.db.execute("UPDATE outbox SET due=0 WHERE state='queued'")
    assert any('admin file could not be delivered' in t for _,t in svc.meta.texts)
    assert 'admin.ref.download' in svc.db.session(SENDER,30)['menu_actions'].values()
    assert not svc.db.all('SELECT * FROM jobs')

def test_password_rotation_invalidates_queued_login_proof(env):
    svc,_,_=env;flow=WhatsAppFlow(svc);send(flow,'Hi');tap(svc,flow,'main.admin')
    message=svc.prepare_incoming(SENDER,'rotation',{'type':'text','text':{'body':'admin-test'}})
    svc.s.admin_password='new-secret';flow.handle(SENDER,message,'rotation')
    assert not svc.admin_authenticated(SENDER)
