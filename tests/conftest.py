from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import hashlib
import json
import shutil
import pytest
from fastapi.testclient import TestClient
from service.config import Settings
from service.core import Service
from service.app import create_app
from service.meta import MetaClient

ROOT=Path(__file__).resolve().parents[1]
REFS=ROOT/'data'/'references'

class FakeMeta:
    def __init__(self,s): self.real=MetaClient(s);self.documents=[];self.texts=[];self.fail=False;self.uploads=[];self.input=b''
    def signature_valid(self,*args): return self.real.signature_valid(*args)
    def download(self,*_): return self.input
    def upload(self,p): self.uploads.append(p.read_bytes());return 'uploaded'
    def send_document(self,s,mid,name,caption):
        if self.fail: raise RuntimeError('delivery failed')
        self.documents.append((s,mid,name,caption));return 'wamid.result'
    def send_text(self,s,t): self.texts.append((s,t));return 'wamid.text.'+str(len(self.texts))

@pytest.fixture
def env(tmp_path):
    data=tmp_path/'data';shutil.copytree(REFS,data/'references')
    settings=Settings(data=data,teacher_code='teacher-test',admin_password='admin-test',wa_token='token',wa_phone_id='phone',wa_app_secret='secret',wa_verify_token='verify',wa_version='v25.0')
    meta=FakeMeta(settings)
    svc=Service(settings,meta=meta)
    app=create_app(settings,svc,start_workers=False)
    with TestClient(app) as client: yield svc,client,meta

@pytest.fixture
def word(): return next(REFS.glob('*.docx')).read_bytes()

def login(client,role='teacher'):
    return client.post('/api/login',headers={'X-Exam-Client':'web'},json={'role':role,'password':'admin-test' if role=='admin' else 'teacher-test'})
