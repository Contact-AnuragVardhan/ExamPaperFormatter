"""Check compatibility with the attached older Flask app's endpoint/env names."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import pytest
import httpx
import job
from service.config import Settings
from service.meta import MetaClient
from conftest import ROOT
from test_engine import setup_engine

NAMES=['EXAM_DATA_ROOT','EXAM_DATA_DIR','VERIFY_TOKEN','WHATSAPP_VERIFY_TOKEN',
       'WHATSAPP_TOKEN','WHATSAPP_ACCESS_TOKEN','WHATSAPP_GRAPH_VERSION','WHATSAPP_API_VERSION',
       'APP_ENV','COOKIE_SECURE','WHATSAPP_ASYNC','WHATSAPP_HTTP_TIMEOUT','LOG_LEVEL',
       'WHATSAPP_PHONE_NUMBER_ID','WHATSAPP_APP_SECRET','EXAM_REDO_MODEL','OPENAI_MODEL']

@pytest.fixture
def clean_env(monkeypatch):
    for name in NAMES:monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(job.records,'load_env',lambda:None)
    return monkeypatch

def test_existing_env_names_take_precedence(clean_env,tmp_path):
    m=clean_env
    settings={
        'EXAM_DATA_ROOT':str(tmp_path/'primary'),'EXAM_DATA_DIR':str(tmp_path/'alias'),
        'VERIFY_TOKEN':'primary-verify','WHATSAPP_VERIFY_TOKEN':'alias-verify',
        'WHATSAPP_TOKEN':'primary-token','WHATSAPP_ACCESS_TOKEN':'alias-token',
        'WHATSAPP_GRAPH_VERSION':'v25.0','WHATSAPP_API_VERSION':'v24.0',
        'WHATSAPP_PHONE_NUMBER_ID':'phone','WHATSAPP_APP_SECRET':'secret',
        'WHATSAPP_HTTP_TIMEOUT':'23','APP_ENV':'prod','LOG_LEVEL':'warning'}
    for k,v in settings.items():m.setenv(k,v)
    s=Settings.load()
    assert s.data==tmp_path/'primary'
    assert s.wa_verify_token=='primary-verify' and s.wa_token=='primary-token'
    assert s.wa_version=='v25.0' and s.wa_http_timeout==23
    assert s.secure_cookie and s.wa_async and s.whatsapp_ready
    assert s.log_level=='WARNING'

def test_previous_rev2_aliases_still_work(clean_env,tmp_path):
    for k,v in {'EXAM_DATA_DIR':str(tmp_path),'WHATSAPP_VERIFY_TOKEN':'verify',
                'WHATSAPP_ACCESS_TOKEN':'token','WHATSAPP_API_VERSION':'v24.0',
                'VERIFY_TOKEN':'','WHATSAPP_TOKEN':''}.items():clean_env.setenv(k,v)
    s=Settings.load()
    assert s.data==tmp_path and s.wa_verify_token=='verify' and s.wa_token=='token' and s.wa_version=='v24.0'

def test_defaults_match_existing_graph_and_timeout(clean_env):
    s=Settings.load()
    assert s.wa_version=='v25.0' and s.wa_http_timeout==60 and s.wa_async
    assert s.data==job.records.ROOT/'data'

@pytest.mark.parametrize('endpoint',['/webhook','/webhooks/whatsapp'])
def test_shared_get_and_post_endpoint(env,endpoint):
    svc,c,_=env
    assert c.get(endpoint+'?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=12345').text=='12345'
    payload={'object':'whatsapp_business_account','entry':[{'changes':[{'value':{
      'metadata':{'phone_number_id':'phone'},'messages':[{'from':'15551230000','id':'shared-message','type':'text','text':{'body':'Hi'}}]}}]}]}
    body=json.dumps(payload).encode();sig='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    assert c.post(endpoint,content=body,headers={'X-Hub-Signature-256':sig}).status_code==200
    assert svc.db.one('SELECT * FROM inbox WHERE id=?',('shared-message',))['state']=='queued'
    assert c.post(endpoint,content=body,headers={'X-Hub-Signature-256':sig}).status_code==200
    assert len(svc.db.all('SELECT * FROM inbox'))==1
    assert c.post(endpoint,content=body).status_code==401

def test_local_sync_flag_handles_hi_without_formatting_inline(env):
    svc,c,_=env;svc.s.wa_async=False
    payload={'entry':[{'changes':[{'value':{'metadata':{'phone_number_id':'phone'},'messages':[
      {'from':'15551230000','id':'sync-hi','type':'text','text':{'body':'Hi'}}]}}]}]}
    body=json.dumps(payload).encode();sig='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    response=c.post('/webhook',content=body,headers={'X-Hub-Signature-256':sig})
    assert response.status_code==200
    assert svc.db.one('SELECT * FROM inbox WHERE id=?',('sync-hi',))['state']=='done'
    assert len(svc.db.all('SELECT * FROM outbox'))==1
    assert svc.db.all('SELECT * FROM jobs')==[]
    assert not svc.handle_incoming('sync-hi')

def test_model_uses_existing_env_name_first(setup_engine,clean_env):
    client,folder=setup_engine
    clean_env.setenv('EXAM_REDO_MODEL','primary-model')
    clean_env.setenv('OPENAI_MODEL','alias-model')
    ref=next((ROOT/'data'/'references').glob('*.docx'))
    result=job.run('format','math','10',ref,ref,output_dir=folder)
    assert result.status=='PASS' and client.last['model']=='primary-model'

def test_meta_transport_uses_configured_http_timeout(env,monkeypatch):
    svc,_,_=env;svc.s.wa_http_timeout=17
    seen=[]
    class Client:
        def __init__(self,**kw):seen.append(kw['timeout'])
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def request(self,*a,**kw):return httpx.Response(200,json={'messages':[{'id':'sent'}]})
    monkeypatch.setattr(httpx,'Client',Client)
    assert MetaClient(svc.s).send_text('15551230000','Hi')=='sent'
    assert seen==[17]

def test_production_rejects_slow_synchronous_webhooks(env):
    from fastapi.testclient import TestClient
    from service.app import create_app
    svc,_,_=env;svc.s.app_env='prod';svc.s.wa_async=False
    with pytest.raises(RuntimeError,match='WHATSAPP_ASYNC must be true'):
        with TestClient(create_app(svc.s,svc,start_workers=False)):
            pass
