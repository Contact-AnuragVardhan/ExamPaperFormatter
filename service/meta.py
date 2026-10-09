"""Meta Cloud API transport. No exam formatting rules live here."""
import hashlib
import hmac
import json
from urllib.parse import urlparse
import httpx
from job import DOCX_MIME
from .files import UserError

class DeliveryError(Exception): pass

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
        with httpx.Client(timeout=self.s.wa_http_timeout,follow_redirects=False) as client:
            r=client.request(method,self.base+'/'+path,headers=self.headers,**kwargs)
            if r.status_code>=400: raise DeliveryError(f'Meta HTTP {r.status_code}')
            return r.json()
    def download(self,media_id,name):
        info=self._json('GET',media_id)
        if int(info.get('file_size',0))>self.s.max_bytes: raise UserError('This document exceeds the upload limit.')
        url=info.get('url',''); p=urlparse(url)
        if p.scheme!='https' or not (p.hostname or '').endswith(('.facebook.com','.fbcdn.net')):
            raise DeliveryError('Unexpected Meta media host')
        if info.get('mime_type')!=DOCX_MIME: raise UserError('Please send a Word .docx file.')
        with httpx.Client(timeout=self.s.wa_http_timeout,follow_redirects=False) as client:
            with client.stream('GET',url,headers=self.headers) as r:
                if r.status_code>=400: raise DeliveryError(f'Media HTTP {r.status_code}')
                out=bytearray()
                for chunk in r.iter_bytes():
                    out.extend(chunk)
                    if len(out)>self.s.max_bytes: raise UserError('This document exceeds the upload limit.')
        if info.get('sha256') and hashlib.sha256(out).hexdigest()!=info['sha256']:
            raise UserError('The document download was incomplete. Please attach it again.')
        return bytes(out)
    def send_text(self,sender,text):
        return self._send(sender,'text',{'body':text[:4096],'preview_url':False})
    def upload(self,path):
        with path.open('rb') as handle:
            return self._json('POST',self.s.wa_phone_id+'/media',data={'messaging_product':'whatsapp','type':DOCX_MIME},
                files={'file':(path.name,handle,DOCX_MIME)})['id']
    def send_document(self,sender,media_id,name,caption):
        return self._send(sender,'document',{'id':media_id,'filename':name,'caption':caption})
    def _send(self,sender,kind,value):
        response=self._json('POST',self.s.wa_phone_id+'/messages',json={
            'messaging_product':'whatsapp','recipient_type':'individual','to':sender,'type':kind,kind:value})
        return response['messages'][0]['id']
