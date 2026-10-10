"""Verify useful operational events and keep private payloads out of logs."""
import json
import logging
import subprocess
import sys
import httpx
import pytest
from conftest import ROOT
from runtime_logging import AccessLogFilter,event,log_context,register_secrets
from service.meta import MetaClient,DeliveryError
from test_engine import setup_engine,run_engine


def test_webhook_503_lists_missing_setting_without_secret(env,caplog):
    svc,c,_=env
    svc.s.wa_app_secret=''
    caplog.set_level(logging.INFO,logger='exam')
    r=c.post('/webhook',json={})
    assert r.status_code==503 and r.headers['X-Request-ID']
    assert 'webhook.not_configured' in caplog.text
    assert 'WHATSAPP_APP_SECRET' in caplog.text
    assert r.headers['X-Request-ID'] in caplog.text


def test_menu_logs_state_and_deduplication_without_message_text_or_phone(env,caplog):
    svc,c,_=env
    caplog.set_level(logging.INFO,logger='exam')
    svc.s.wa_verify_signature=False
    private='PRIVATE_TEACHER_EXAM_TEXT_ABC'
    payload={'entry':[{'changes':[{'value':{
        'metadata':{'phone_number_id':'phone'},'messages':[{
            'from':'15551230000','id':'log-msg','type':'text','text':{'body':private}}]
    }}]}]}
    assert c.post('/webhook',json=payload).status_code==200
    assert c.post('/webhook',json=payload).status_code==200
    assert svc.handle_incoming('log-msg')
    assert svc.deliver_one()
    for name in ('webhook.received','webhook.accepted','webhook.message.duplicate',
                 'whatsapp.menu.input','whatsapp.menu.state','whatsapp.delivery.sent'):
        assert name in caplog.text
    assert private not in caplog.text and '15551230000' not in caplog.text


def test_engine_logs_have_job_correlation_and_stage_metadata(env,setup_engine,word,caplog):
    svc,_,_=env
    caplog.set_level(logging.INFO,logger='exam')
    row=svc.submit('web:log-owner','web','format',svc.references()[0]['id'],'PrivateTeacherName.docx',word,'log-job')
    assert svc.process_one()
    assert svc.get(row['id'])['state']=='completed'
    engine_records=[r.getMessage() for r in caplog.records if r.name=='exam.engine']
    for name in ('openai.container.created','openai.file.uploading','openai.response.submitted',
                 'openai.output.saved','openai.container.deleted'):
        assert any(name in line and row['id'] in line for line in engine_records)
    assert 'PrivateTeacherName' not in caplog.text
    assert 'job.finished' in caplog.text


def test_api_error_logs_parameter_and_code_without_provider_message(setup_engine,caplog):
    client,folder=setup_engine
    caplog.set_level(logging.INFO,logger='exam')
    class Rejected(Exception):
        status_code=400
        request_id='req-log-test'
        body={'error':{'code':'unsupported_parameter','param':'reasoning.effort',
                       'message':'PRIVATE_PROVIDER_ECHO_WITH_EXAM_TEXT'}}
    def fail(**kwargs): raise Rejected('PRIVATE_EXCEPTION_TEXT')
    client.responses.create=fail
    assert run_engine(folder).status=='SYSTEM ERROR'
    assert 'unsupported_parameter' in caplog.text and 'reasoning.effort' in caplog.text
    assert 'req-log-test' in caplog.text
    assert 'PRIVATE_PROVIDER_ECHO' not in caplog.text and 'PRIVATE_EXCEPTION_TEXT' not in caplog.text


def test_meta_error_logs_code_without_token_url_or_payload(env,monkeypatch,caplog):
    svc,_,_=env
    caplog.set_level(logging.INFO,logger='exam')
    class Client:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def request(self,*args,**kwargs):
            return httpx.Response(401,json={'error':{'code':190,'type':'OAuthException',
                'message':'PRIVATE_META_ERROR_BODY'}})
    monkeypatch.setattr(httpx,'Client',Client)
    with pytest.raises(DeliveryError):
        MetaClient(svc.s).send_text('15551230000','PRIVATE_REPLY_TEXT')
    assert 'meta.request.failed' in caplog.text and '190' in caplog.text
    for private in ('PRIVATE_META_ERROR_BODY','PRIVATE_REPLY_TEXT','15551230000','graph.facebook.com'):
        assert private not in caplog.text


def test_context_resets_and_secrets_are_redacted(caplog):
    caplog.set_level(logging.INFO,logger='exam')
    register_secrets('unique-credential-abc')
    logger=logging.getLogger('exam.test')
    with log_context(job_id='context-job'):
        event(logger,'context.inside',detail='unique-credential-abc Bearer opaque-value')
    event(logger,'context.outside')
    assert 'unique-credential-abc' not in caplog.text and 'opaque-value' not in caplog.text
    assert 'context-job' in caplog.records[-2].getMessage()
    assert 'context-job' not in caplog.records[-1].getMessage()


def test_uvicorn_access_filter_removes_verification_query():
    record=logging.LogRecord('uvicorn.access',logging.INFO,'',0,'%s %s %s %s %s',
        ('client','GET','/webhook?hub.verify_token=private-value&hub.challenge=123','1.1',200),None)
    assert AccessLogFilter().filter(record)
    assert '/webhook' in record.getMessage() and 'private-value' not in record.getMessage()


def test_console_configuration_prints_once_and_keeps_http_libraries_quiet():
    code='''
import logging
from runtime_logging import configure_logging,event
configure_logging('DEBUG')
configure_logging('DEBUG')
event(logging.getLogger('exam.test'),'console.ready',level=logging.DEBUG,job_id='console-job')
logging.getLogger('httpx').debug('PRIVATE_HTTP_HEADERS')
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,capture_output=True,text=True,check=True)
    assert result.stdout.count('event=console.ready')==1
    assert 'console-job' in result.stdout and 'PRIVATE_HTTP_HEADERS' not in result.stdout+result.stderr
