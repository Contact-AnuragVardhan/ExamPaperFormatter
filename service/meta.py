"""Meta Cloud API transport. No exam formatting rules live here."""
import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlparse
import httpx
from job import DOCX_MIME
from runtime_logging import event,error_fields
from .files import UserError

class DeliveryError(Exception): pass
log=logging.getLogger('exam.meta')

class MetaClient:
    def __init__(self,settings): self.s=settings
    @property
    def base(self): return f'https://graph.facebook.com/{self.s.wa_version}'
    @property
    def headers(self): return {'Authorization':f'Bearer {self.s.wa_token}'}
    def signature_valid(self,body,signature):
        expected='sha256='+hmac.new(self.s.wa_app_secret.encode(),body,hashlib.sha256).hexdigest()
        return bool(self.s.wa_app_secret) and hmac.compare_digest(expected,signature or '')
    def _json(self,method,path,**kwargs):
        operation='send_message' if path.endswith('/messages') else ('upload_media' if path.endswith('/media') else 'media_lookup')
        started=time.monotonic()
        event(log,'meta.request.started',operation=operation,method=method,api_version=self.s.wa_version)
        try:
            with httpx.Client(timeout=self.s.wa_http_timeout,follow_redirects=False) as client:
                r=client.request(method,self.base+'/'+path,headers=self.headers,**kwargs)
                if r.status_code>=400:
                    exc=DeliveryError(f'Meta HTTP {r.status_code}')
                    exc.status_code=r.status_code
                    try: exc.body=r.json()
                    except ValueError: pass
                    raise exc
                result=r.json()
                event(log,'meta.request.completed',operation=operation,http_status=r.status_code,
                      elapsed_ms=round((time.monotonic()-started)*1000))
                return result
        except Exception as exc:
            event(log,'meta.request.failed',level=logging.ERROR,operation=operation,
                  elapsed_ms=round((time.monotonic()-started)*1000),**error_fields(exc))
            raise
    def download(self,media_id,name):
        event(log,'meta.media.download.started',media_id=media_id)
        info=self._json('GET',media_id)
        if int(info.get('file_size',0))>self.s.max_bytes: raise UserError('This document exceeds the upload limit.')
        url=info.get('url','')
        try:
            p=urlparse(url)
            host=(p.hostname or '').lower()
            # Meta returns Cloud API attachments from lookaside.fbsbx.com.
            # Keep an explicit allowlist before sending the bearer token.
            allowed=host=='lookaside.fbsbx.com' or host.endswith(('.facebook.com','.fbcdn.net'))
            valid=(p.scheme=='https' and allowed and p.port in (None,443)
                   and not p.username and not p.password and not p.fragment)
        except (ValueError,TypeError,AttributeError):
            host='';valid=False
        if not valid:
            event(log,'meta.media.url_rejected',level=logging.ERROR,media_id=media_id,
                  reason='unexpected_media_origin',host=host)
            raise DeliveryError('Unexpected Meta media host or URL')
        event(log,'meta.media.url_accepted',media_id=media_id,host=host)
        if info.get('mime_type')!=DOCX_MIME: raise UserError('Please send a Word .docx file.')
        with httpx.Client(timeout=self.s.wa_http_timeout,follow_redirects=False) as client:
            with client.stream('GET',url,headers=self.headers) as r:
                event(log,'meta.media.http_response',media_id=media_id,http_status=r.status_code)
                if r.status_code>=300:
                    exc=DeliveryError(f'Media HTTP {r.status_code}');exc.status_code=r.status_code
                    raise exc
                out=bytearray()
                for chunk in r.iter_bytes():
                    out.extend(chunk)
                    if len(out)>self.s.max_bytes: raise UserError('This document exceeds the upload limit.')
        if info.get('sha256') and hashlib.sha256(out).hexdigest()!=info['sha256']:
            raise UserError('The document download was incomplete. Please attach it again.')
        event(log,'meta.media.download.completed',media_id=media_id,bytes=len(out))
        return bytes(out)
    def send_text(self,sender,text):
        event(log,'meta.text.send',characters=min(len(text),4096))
        return self._send(sender,'text',{'body':text[:4096],'preview_url':False})
    def send_interactive(self,sender,payload):
        event(log,'meta.interactive.send',menu_type=payload['type'])
        return self._send(sender,'interactive',payload)
    def upload(self,path):
        event(log,'meta.media.upload',bytes=path.stat().st_size)
        with path.open('rb') as handle:
            return self._json('POST',self.s.wa_phone_id+'/media',data={'messaging_product':'whatsapp','type':DOCX_MIME},
                files={'file':(path.name,handle,DOCX_MIME)})['id']
    def send_document(self,sender,media_id,name,caption):
        return self._send(sender,'document',{'id':media_id,'filename':name,'caption':caption})
    def _send(self,sender,kind,value):
        response=self._json('POST',self.s.wa_phone_id+'/messages',json={
            'messaging_product':'whatsapp','recipient_type':'individual','to':sender,'type':kind,kind:value})
        return response['messages'][0]['id']
