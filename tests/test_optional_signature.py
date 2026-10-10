"""Exercise signed and explicitly unsigned beta requests without live services."""
import hashlib
import hmac
import json
import pytest
from service.config import Settings
from test_compatibility import clean_env


def payload():
    return {'entry':[{'changes':[{'value':{
        'metadata':{'phone_number_id':'phone'},
        'messages':[{'from':'15551230000','id':'beta-hi','type':'text','text':{'body':'Hi'}}]
    }}]}]}


@pytest.mark.parametrize('endpoint',['/webhook','/webhooks/whatsapp'])
@pytest.mark.parametrize('secret',['','secret'])
@pytest.mark.parametrize('signature',[None,'sha256=invalid'])
def test_beta_accepts_unsigned_hi_without_secret(env,endpoint,secret,signature):
    svc,c,meta=env
    svc.s.wa_verify_signature=False
    svc.s.wa_app_secret=secret
    svc.s.app_env='prod'
    body=json.dumps(payload()).encode()
    headers={'Content-Type':'application/json'}
    if signature is not None: headers['X-Hub-Signature-256']=signature
    assert c.post(endpoint,content=body,headers=headers).status_code==200
    assert c.post(endpoint,content=body,headers=headers).status_code==200
    assert len(svc.db.all('SELECT * FROM inbox'))==1
    assert svc.handle_incoming('beta-hi')
    assert svc.db.session('15551230000',30)['state']=='main'
    assert svc.deliver_one()
    assert 'Exam Formatter' in meta.interactives[0][1]['body']['text']
    assert svc.db.all('SELECT * FROM jobs')==[]
    assert c.get(endpoint+'?hub.mode=subscribe&hub.verify_token=verify&hub.challenge=123').text=='123'
    assert c.get(endpoint+'?hub.mode=subscribe&hub.verify_token=wrong&hub.challenge=123').status_code==403


@pytest.mark.parametrize('endpoint',['/webhook','/webhooks/whatsapp'])
def test_strict_rejects_missing_and_invalid_signatures(env,endpoint):
    svc,c,_=env
    assert svc.s.wa_verify_signature
    body=json.dumps(payload()).encode()
    assert c.post(endpoint,content=body).status_code==401
    assert c.post(endpoint,content=body,headers={'X-Hub-Signature-256':'sha256=invalid'}).status_code==401
    assert svc.db.all('SELECT * FROM inbox')==[]
    signature='sha256='+hmac.new(b'secret',body,hashlib.sha256).hexdigest()
    assert c.post(endpoint,content=body,headers={'X-Hub-Signature-256':signature}).status_code==200


def test_strict_without_secret_still_returns_503(env):
    svc,c,_=env
    svc.s.wa_app_secret=''
    assert c.post('/webhook',json=payload()).status_code==503


@pytest.mark.parametrize('field',['wa_token','wa_phone_id','wa_verify_token','wa_version'])
def test_beta_still_requires_other_whatsapp_settings(env,field):
    svc,c,_=env
    svc.s.wa_verify_signature=False
    svc.s.wa_app_secret=''
    setattr(svc.s,field,'')
    assert c.post('/webhook',json=payload()).status_code==503


@pytest.mark.parametrize('value',['false',' FALSE ','0','off'])
def test_signature_mode_can_be_disabled_from_environment(clean_env,value):
    clean_env.setenv('WHATSAPP_VERIFY_SIGNATURE',value)
    assert not Settings.load().wa_verify_signature


@pytest.mark.parametrize('value',[None,'','true'])
def test_signature_validation_defaults_to_enabled(clean_env,value):
    if value is not None: clean_env.setenv('WHATSAPP_VERIFY_SIGNATURE',value)
    assert Settings.load().wa_verify_signature


def test_invalid_signature_mode_does_not_silently_disable_validation(clean_env):
    clean_env.setenv('WHATSAPP_VERIFY_SIGNATURE','flase')
    with pytest.raises(ValueError,match='WHATSAPP_VERIFY_SIGNATURE'):
        Settings.load()
