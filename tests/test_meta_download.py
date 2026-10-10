"""Exercise the real media transport with mocked Graph/CDN HTTP responses."""
import hashlib
import httpx
import pytest
from job import DOCX_MIME
from service.files import UserError
from service.meta import MetaClient,DeliveryError
from service.whatsapp import WhatsAppFlow
from test_integration import send
from test_interactive_menus import tap,SENDER

def transport(monkeypatch,settings,metadata,data,status=200,location=None):
    calls=[];real_client=httpx.Client
    def handle(request):
        calls.append(request)
        assert request.headers['authorization']=='Bearer '+settings.wa_token
        if request.url.host=='graph.facebook.com': return httpx.Response(200,json=metadata)
        return httpx.Response(status,content=data,headers={'Location':location} if location else {})
    mock=httpx.MockTransport(handle)
    monkeypatch.setattr(httpx,'Client',lambda **kwargs:real_client(transport=mock,**kwargs))
    return calls

def info(data,url='https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=example&hash=PRIVATE_URL_HASH'):
    return {'url':url,'mime_type':DOCX_MIME,'file_size':len(data),'sha256':hashlib.sha256(data).hexdigest()}

@pytest.mark.parametrize('host',['lookaside.fbsbx.com','LOOKASIDE.FBSBX.COM','lookaside.facebook.com','attachment.fbcdn.net'])
def test_download_accepts_meta_hosts_preserves_bytes_and_hides_url(env,word,monkeypatch,caplog,host):
    svc,_,_=env;caplog.set_level('INFO',logger='exam')
    metadata=info(word,'https://'+host+'/attachments/?hash=PRIVATE_URL_HASH')
    calls=transport(monkeypatch,svc.s,metadata,word)
    assert MetaClient(svc.s).download('123','teacher.docx')==word
    assert len(calls)==2
    assert calls[1].url.host==host.lower()
    assert 'meta.media.url_accepted' in caplog.text and 'meta.media.download.completed' in caplog.text
    assert 'PRIVATE_URL_HASH' not in caplog.text and 'teacher.docx' not in caplog.text

@pytest.mark.parametrize('url',[
    'http://lookaside.fbsbx.com/attachments/',
    'https://lookaside.fbsbx.com.attacker.example/attachments/',
    'https://evilfbsbx.com/attachments/',
    'https://example.com/attachments/',
    'https://127.0.0.1/attachments/',
    'https://lookaside.fbsbx.com:444/attachments/',
    'https://user:pass@lookaside.fbsbx.com/attachments/',
    'https://lookaside.fbsbx.com/attachments/#fragment',
    'https://lookaside.fbsbx.com:invalid/attachments/',
    '',None])
def test_untrusted_or_malformed_media_urls_never_receive_token(env,word,monkeypatch,caplog,url):
    svc,_,_=env;caplog.set_level('INFO',logger='exam')
    metadata=info(word);metadata['url']=url
    calls=transport(monkeypatch,svc.s,metadata,word)
    with pytest.raises(DeliveryError): MetaClient(svc.s).download('123','teacher.docx')
    assert len(calls)==1
    assert 'meta.media.url_rejected' in caplog.text and 'unexpected_media_origin' in caplog.text
    assert 'user:pass' not in caplog.text

@pytest.mark.parametrize('status',[302,401,404,500])
def test_download_http_failure_does_not_follow_redirect_or_return_body(env,word,monkeypatch,status):
    svc,_,_=env
    calls=transport(monkeypatch,svc.s,info(word),b'error-body',status,'https://attacker.example/receive-token')
    with pytest.raises(DeliveryError) as error: MetaClient(svc.s).download('123','teacher.docx')
    assert error.value.status_code==status and len(calls)==2

def test_media_size_and_checksum_checks_remain_enforced(env,word,monkeypatch):
    svc,_,_=env;metadata=info(word);metadata['file_size']=svc.s.max_bytes+1
    calls=transport(monkeypatch,svc.s,metadata,word)
    with pytest.raises(UserError,match='upload limit'): MetaClient(svc.s).download('123','teacher.docx')
    assert len(calls)==1
    metadata['file_size']=len(word);metadata['sha256']='0'*64
    with pytest.raises(UserError,match='incomplete'): MetaClient(svc.s).download('123','teacher.docx')

@pytest.mark.parametrize('admin',[False,True])
def test_real_download_adapter_reaches_teacher_and_admin_confirmation(env,word,monkeypatch,admin):
    svc,_,_=env;flow=WhatsAppFlow(svc)
    send(flow,'Hi')
    if admin:
        tap(svc,flow,'main.admin');send(flow,'admin-test');tap(svc,flow,'admin.refs');tap(svc,flow,'admin.ref.0');tap(svc,flow,'admin.ref.replace')
    else:
        tap(svc,flow,'main.format');tap(svc,flow,'subject.'+svc.references()[0]['id'])
    svc.meta.download=MetaClient(svc.s).download
    calls=transport(monkeypatch,svc.s,info(word),word)
    send(flow,'paper.docx','document')
    state=svc.db.session(SENDER,30)
    assert state['state']==('admin_confirm' if admin else 'confirm')
    name='reference.docx' if admin else 'teacher.docx'
    assert (svc.s.data/'pending'/state['pending_id']/name).read_bytes()==word
    assert len(calls)==2 and not svc.db.all('SELECT * FROM jobs')
