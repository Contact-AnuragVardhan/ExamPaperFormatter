"""Native button/list payloads, safe old-menu rejection, and full teacher flows."""
import json
import time
import httpx
import pytest
from pathlib import Path
import job
from service.whatsapp import WhatsAppFlow
from service.menus import replies
from service.meta import MetaClient
from test_integration import send,mock_engine,submit

SENDER='15551230000'

def latest(svc):
    row=svc.db.one("SELECT * FROM outbox WHERE kind='interactive' ORDER BY id DESC LIMIT 1")
    return json.loads(row['payload'])

def choice(svc,action):
    return next(k for k,v in svc.db.session(SENDER,30)['menu_actions'].items() if v==action)

def tap(svc,flow,action,old_id=None):
    kind='button_reply' if latest(svc)['type']=='button' else 'list_reply'
    flow.handle(SENDER,{'type':'interactive','interactive':{kind:{'id':old_id or choice(svc,action)}}},str(time.time_ns()))

def ready(env,word):
    svc,_,meta=env;meta.input=word;flow=WhatsAppFlow(svc)
    send(flow,'Hi');tap(svc,flow,'main.format')
    rid=svc.references()[0]['id'];tap(svc,flow,'subject.'+rid)
    send(flow,'teacher.docx','document')
    return svc,flow,meta

def assert_limits(payload):
    assert payload['type'] in ('button','list')
    assert 0<len(payload['body']['text'])<=1024
    assert len(payload['header']['text'])<=60 and len(payload['footer']['text'])<=60
    options=replies(payload)
    assert len({r['id'] for r in options})==len(options)
    assert all(0<len(r['id'])<=200 for r in options)
    if payload['type']=='button':
        assert 1<=len(options)<=3
        assert all(0<len(r['title'])<=20 for r in options)
    else:
        assert 1<=len(options)<=10 and 0<len(payload['action']['button'])<=20
        assert all(0<len(r['title'])<=24 and len(r.get('description',''))<=72 for r in options)
    assert not any(r['title'][0].isdigit() for r in options)

@pytest.mark.parametrize('screen',['main','subject','upload','confirm','processing','result','error','help'])
def test_all_screens_have_native_choices_within_limits(env,word,screen):
    svc,flow,_=ready(env,word)
    row=submit(svc,word,owner='wa:'+SENDER,channel='whatsapp')
    svc.update(row['id'],state='failed',retryable=1,message='Please try again.')
    state=svc.db.session(SENDER,30)
    state.update(state=screen,job_id=row['id'])
    if screen=='result': svc.update(row['id'],state='completed',message='Formatted exam ready.')
    svc.db.save_session(SENDER,state)
    svc.emit_reply(SENDER,flow.current_menu(SENDER))
    assert_limits(latest(svc))

def test_numeric_reply_does_not_navigate(env):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    send(flow,'Hi');send(flow,'1')
    assert svc.db.session(SENDER,30)['state']=='main'
    assert svc.db.all('SELECT * FROM jobs')==[]
    assert 'Tap an option' in latest(svc)['body']['text']

def test_old_confirmation_after_file_replacement_never_starts_job(env,word):
    svc,flow,_=ready(env,word)
    old=choice(svc,'confirm.start')
    tap(svc,flow,'confirm.replace')
    send(flow,'replacement.docx','document')
    tap(svc,flow,'confirm.start',old_id=old)
    assert svc.db.all('SELECT * FROM jobs')==[]
    assert svc.db.session(SENDER,30)['filename']=='replacement.docx'
    tap(svc,flow,'confirm.start')
    assert len(svc.db.all('SELECT * FROM jobs'))==1
    tap(svc,flow,'confirm.start',old_id=old)
    assert len(svc.db.all('SELECT * FROM jobs'))==1

@pytest.mark.parametrize('state',['result','error'])
def test_help_back_returns_to_result_and_error_menus(env,word,state):
    svc,flow,_=ready(env,word)
    row=submit(svc,word,owner='wa:'+SENDER,channel='whatsapp')
    svc.update(row['id'],state='completed' if state=='result' else 'failed',message='Done.')
    svc.db.save_session(SENDER,{'state':state,'job_id':row['id']})
    svc.emit_reply(SENDER,flow.current_menu(SENDER))
    tap(svc,flow,'menu.help');tap(svc,flow,'help.back')
    assert svc.db.session(SENDER,30)['state']==state
    assert_limits(latest(svc))

@pytest.mark.parametrize('running',[False,True])
def test_cancel_request_button_is_honest(env,word,running):
    svc,flow,_=ready(env,word);tap(svc,flow,'confirm.start')
    jid=svc.db.session(SENDER,30)['job_id']
    if running: svc.update(jid,state='running')
    tap(svc,flow,'processing.cancel')
    assert svc.get(jid)['state']==('running' if running else 'cancelled')
    assert svc.db.session(SENDER,30)['state']==('processing' if running else 'main')
    if running: assert 'cannot be cancelled' in latest(svc)['body']['text']

def test_subjects_are_paginated_and_disabled_references_hidden(env,monkeypatch):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    refs=[{'id':'ref'+str(i),'subject':'Subject '+str(i),'grade':'10','enabled':i!=15} for i in range(17)]
    monkeypatch.setattr(svc,'references',lambda:refs)
    send(flow,'Hi');tap(svc,flow,'main.format')
    seen=set()
    for _ in range(3):
        payload=latest(svc);assert_limits(payload)
        actions=list(svc.db.session(SENDER,30)['menu_actions'].values())
        seen.update(a for a in actions if a.startswith('subject.'))
        if 'subjects.next' in actions: tap(svc,flow,'subjects.next')
    assert len(seen)==16 and 'subject.ref15' not in seen
    tap(svc,flow,'subjects.previous')
    assert svc.db.session(SENDER,30)['subject_page']==1

def test_expired_menu_tap_returns_fresh_main_without_submission(env):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    send(flow,'Hi');old=choice(svc,'main.format')
    svc.db.execute('UPDATE sessions SET updated=0 WHERE sender=?',(SENDER,))
    tap(svc,flow,'main.format',old_id=old)
    assert svc.db.session(SENDER,30)['state']=='main' and not svc.db.all('SELECT * FROM jobs')

def test_safe_retry_is_a_tappable_choice(env,word):
    svc,flow,_=ready(env,word);tap(svc,flow,'confirm.start')
    jid=svc.db.session(SENDER,30)['job_id']
    svc.update(jid,state='failed',retryable=1,message='The service is busy.')
    svc.set_finished_session(SENDER,svc.get(jid));svc.emit_reply(SENDER,svc.result_menu(svc.get(jid)))
    tap(svc,flow,'error.retry')
    assert len(svc.db.all('SELECT * FROM jobs'))==2
    assert svc.db.session(SENDER,30)['job_id']!=jid

def test_unsafe_retry_is_not_offered(env,word):
    svc,flow,_=ready(env,word);tap(svc,flow,'confirm.start')
    jid=svc.db.session(SENDER,30)['job_id'];svc.update(jid,state='uncertain',retryable=0,message='Contact administrator.')
    svc.set_finished_session(SENDER,svc.get(jid));svc.emit_reply(SENDER,svc.result_menu(svc.get(jid)))
    assert 'error.retry' not in svc.db.session(SENDER,30)['menu_actions'].values()

@pytest.mark.parametrize('endpoint',['/webhook','/webhooks/whatsapp'])
def test_native_webhook_full_flow_delivers_exact_result_and_menu(env,word,endpoint):
    svc,c,meta=env;meta.input=word;svc.s.wa_verify_signature=False
    calls=[]
    def engine(*args,**kwargs): calls.append(1);return mock_engine('PASS',word)(*args,**kwargs)
    svc.engine=engine
    def post(message,mid):
        message['from']=SENDER;message['id']=mid
        payload={'entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'phone'},'messages':[message]}}]}]}
        assert c.post(endpoint,json=payload).status_code==200
        svc.handle_incoming(mid)
        return payload
    def click(action,mid):
        kind='button_reply' if latest(svc)['type']=='button' else 'list_reply'
        return post({'type':'interactive','interactive':{kind:{'id':choice(svc,action)}}},mid)
    post({'type':'text','text':{'body':'Hi'}},'hi')
    click('main.format','format');click('subject.'+svc.references()[0]['id'],'subject')
    post({'type':'document','document':{'id':'media','filename':'teacher.docx'}},'file')
    repeated=click('confirm.start','confirm')
    assert c.post(endpoint,json=repeated).status_code==200
    assert len(svc.db.all('SELECT * FROM jobs'))==1
    assert svc.process_one()
    for _ in range(15):
        if not svc.deliver_one(): break
    assert calls==[1] and meta.uploads==[word] and len(meta.documents)==1
    assert svc.db.session(SENDER,30)['state']=='result'
    assert all(r['kind']!='text' for r in svc.db.all('SELECT * FROM outbox'))
    assert_limits(meta.interactives[-1][1])

def test_meta_transport_sends_interactive_schema(env,monkeypatch):
    svc,_,_=env;seen=[]
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def request(self,*args,**kwargs):
            seen.append(kwargs['json']);return httpx.Response(200,json={'messages':[{'id':'native-menu'}]})
    monkeypatch.setattr(httpx,'Client',Client)
    payload=WhatsAppFlow(svc).main(SENDER)
    assert MetaClient(svc.s).send_interactive(SENDER,payload)=='native-menu'
    assert seen[0]['type']=='interactive' and seen[0]['interactive']==payload

def test_delivery_retry_menu_sends_saved_document_without_reformatting(env,word):
    svc,flow,meta=ready(env,word);calls=[]
    def engine(*args,**kwargs): calls.append(1);return mock_engine('PASS',word)(*args,**kwargs)
    svc.engine=engine;tap(svc,flow,'confirm.start')
    jid=svc.db.session(SENDER,30)['job_id'];svc.process_one();meta.fail=True
    for _ in range(15):
        svc.db.execute('UPDATE outbox SET due=0')
        svc.deliver_one()
        if svc.get(jid)['delivery']=='failed': break
    assert svc.get(jid)['delivery']=='failed'
    send(flow,'Hi');tap(svc,flow,'delivery.retry');meta.fail=False
    svc.db.execute('UPDATE outbox SET due=0');svc.deliver_one()
    assert svc.get(jid)['delivery']=='sent'
    assert calls==[1] and meta.uploads==[word]

def test_failed_result_menu_does_not_mark_sent_document_as_failed(env,word):
    svc,flow,meta=ready(env,word);svc.engine=mock_engine('PASS',word)
    tap(svc,flow,'confirm.start');jid=svc.db.session(SENDER,30)['job_id'];svc.process_one()
    while not meta.documents: assert svc.deliver_one()
    assert svc.get(jid)['delivery']=='sent'
    def fail(*args): raise RuntimeError('menu rejected')
    meta.send_interactive=fail
    for _ in range(3):
        svc.db.execute('UPDATE outbox SET due=0');svc.deliver_one()
    assert svc.get(jid)['delivery']=='sent'
