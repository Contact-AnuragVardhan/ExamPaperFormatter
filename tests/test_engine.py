"""Exercise the original engine orchestration with a fake OpenAI client."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import threading
import pytest
import job
from conftest import ROOT

class Response:
    def __init__(self,status='completed',text='PASS'):
        self.id='resp-existing';self.status=status;self.output_text=text;self.usage=None
    def model_dump(self):return {'output':[]}

class FakeClient:
    def __init__(self,word,status='completed',text='PASS',files=True):
        self.response=Response(status,text);self.creates=0;self.deleted=False;self.cancelled=False
        self.responses=NS(create=self.create,retrieve=lambda _:self.response,cancel=self.cancel)
        self.containers=NS(create=lambda **kw:NS(id='container-test'),delete=self.delete)
        self.containers.files=NS(create=lambda *a,**kw:NS(path='/mnt/'+kw['file'][0]),
            list=lambda *a,**kw:[NS(path='/mnt/revised.docx',id='file-test',source='assistant',created_at=1)] if files else [],
            content=NS(retrieve=lambda *a,**kw:NS(read=lambda:word)))
    def create(self,**kw):self.creates+=1;self.last=kw;return self.response
    def cancel(self,_):self.cancelled=True;return NS(status='cancelled')
    def delete(self,_):self.deleted=True

@pytest.fixture
def setup_engine(monkeypatch,word,tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY','test-only')
    monkeypatch.setattr(job.records,'record_usage',lambda *_:None)
    client=FakeClient(word)
    import openai
    monkeypatch.setattr(openai,'OpenAI',lambda **kw:client)
    return client,tmp_path

def run_engine(tmp_path,**kw):
    ref=next((ROOT/'data'/'references').glob('*.docx'))
    return job.run('format','math','10',ref,ref,output_dir=tmp_path,**kw)

def test_actual_engine_two_files_and_exact_output(setup_engine,word):
    client,folder=setup_engine;states=[]
    result=run_engine(folder,on_remote=states.append)
    assert result.status=='PASS';assert result.saved_path.read_bytes()==word
    assert client.creates==1 and client.last['background'] is True
    assert client.last['tools'][0]['container']=='container-test'
    assert 'reference.docx' in client.last['input'] and 'teacher.docx' in client.last['input']
    assert states[-1]['response_id']=='resp-existing';assert client.deleted

def test_resume_does_not_create_paid_response(setup_engine,word):
    client,folder=setup_engine
    result=run_engine(folder,remote_state={'container_id':'container-test','response_id':'resp-existing'})
    assert result.status=='PASS' and client.creates==0

def test_incomplete_is_never_success(setup_engine):
    client,folder=setup_engine;client.response.status='incomplete'
    result=run_engine(folder)
    assert result.status=='SYSTEM ERROR' and result.saved_path is None

def test_missing_output_is_system_error(setup_engine):
    client,folder=setup_engine;client.containers.files.list=lambda *a,**kw:[]
    assert run_engine(folder).status=='SYSTEM ERROR'

def test_timeout_confirmed_cancellation(setup_engine,monkeypatch):
    client,folder=setup_engine;client.response.status='in_progress'
    monkeypatch.setattr(job,'DEADLINE_SECONDS',-1)
    result=run_engine(folder)
    assert result.status=='SYSTEM ERROR' and result.retryable and not result.uncertain
    assert client.cancelled

def test_timeout_unconfirmed_preserves_container(setup_engine,monkeypatch):
    client,folder=setup_engine;client.response.status='in_progress'
    client.responses.cancel=lambda _:NS(status='in_progress')
    monkeypatch.setattr(job,'DEADLINE_SECONDS',-1)
    result=run_engine(folder)
    assert result.uncertain and not result.retryable and not client.deleted

def test_lost_create_response_is_not_retried(setup_engine):
    client,folder=setup_engine
    def fail(**_):raise TimeoutError('lost response')
    client.responses.create=fail
    result=run_engine(folder)
    assert result.uncertain and not result.retryable and not client.deleted

def test_api_busy_is_safe_manual_retry(setup_engine):
    client,folder=setup_engine
    class Busy(Exception):status_code=429
    def fail(**_):raise Busy('busy')
    client.responses.create=fail
    result=run_engine(folder)
    assert result.retryable and not result.uncertain

@pytest.mark.parametrize('status_code',[400,401,403,404,405,413,415,422])
def test_rejected_paid_post_is_failed_not_uncertain(setup_engine,status_code):
    client,folder=setup_engine;states=[]
    class Rejected(Exception):
        request_id='req-rejected'
    exc=Rejected('Request rejected: unsupported model or invalid configuration')
    exc.status_code=status_code
    def fail(**_): raise exc
    client.responses.create=fail
    result=run_engine(folder,on_remote=states.append)
    assert not result.uncertain and not result.retryable and client.deleted
    assert result.error_code=='configuration'
    assert result.diagnostic['stage']=='submit_response'
    assert result.diagnostic['http_status']==status_code
    assert result.diagnostic['request_id']=='req-rejected'
    assert states[-1]['response_rejected'] and not states[-1]['submitting']

@pytest.mark.parametrize('status_code',[408,409,500,502,503,504])
def test_ambiguous_paid_post_stays_blocked(setup_engine,status_code):
    client,folder=setup_engine
    class Ambiguous(Exception): pass
    exc=Ambiguous('Reply lost or server failed');exc.status_code=status_code
    def fail(**_): raise exc
    client.responses.create=fail
    result=run_engine(folder)
    assert result.uncertain and not result.retryable and not client.deleted
    assert result.diagnostic['stage']=='submit_response'

def test_poll_failure_retains_known_response_for_resume(setup_engine,monkeypatch):
    client,folder=setup_engine;client.response.status='in_progress'
    monkeypatch.setattr(job.time,'sleep',lambda _:None)
    def fail(_): raise TimeoutError('polling connection lost')
    client.responses.retrieve=fail
    result=run_engine(folder)
    assert result.uncertain and not result.retryable and not client.deleted
    assert result.diagnostic['response_id']=='resp-existing'
    assert result.diagnostic['stage']=='retrieve_response'

def test_upload_failure_reports_stage_and_has_no_paid_post(setup_engine):
    client,folder=setup_engine
    def fail(*a,**kw): raise RuntimeError('upload rejected')
    client.containers.files.create=fail
    result=run_engine(folder)
    assert not result.uncertain and client.creates==0 and client.deleted
    assert result.diagnostic['stage']=='upload_reference'

def test_diagnostic_redacts_key_and_bearer(setup_engine,monkeypatch):
    client,folder=setup_engine
    monkeypatch.setenv('OPENAI_API_KEY','sk-secret-for-test')
    def fail(**_): raise RuntimeError('sk-secret-for-test Bearer opaque-token')
    client.responses.create=fail
    result=run_engine(folder)
    assert 'sk-secret-for-test' not in result.detail
    assert 'opaque-token' not in result.diagnostic['message']

def test_restart_known_response_resume_and_unknown_block(env,word):
    svc,_,_=env
    ref=svc.references()[0]['id']
    a=svc.submit('web:a','web','format',ref,'exam.docx',word,'a')
    b=svc.submit('web:b','web','format',ref,'exam.docx',word,'b')
    svc.update(a['id'],state='running',remote=json.dumps({'response_id':'known','container_id':'c'}))
    svc.update(b['id'],state='running',remote=json.dumps({'submitting':True,'container_id':'d'}))
    # Prevent actual worker startup; inspect only recovery state changes.
    svc.stop.set();svc.start();svc.close()
    assert svc.get(a['id'])['state']=='queued'
    assert svc.get(b['id'])['state']=='uncertain'

def test_cached_completed_output_recovers_without_remote_container(env,word):
    svc,_,_=env
    row=svc.submit('web:recover','web','format',svc.references()[0]['id'],'exam.docx',word,'cached')
    folder=svc.s.data/'jobs'/row['id'];(folder/'revised.docx').write_bytes(word)
    svc.update(row['id'],remote=json.dumps({'response_id':'known','container_id':'deleted','result_status':'PASS','output_file':'revised.docx'}))
    def must_not_run(*a,**kw):raise AssertionError('No second engine or remote request is permitted')
    svc.engine=must_not_run
    svc.process_one()
    recovered=svc.get(row['id']);assert recovered['state']=='completed';assert Path(recovered['output']).read_bytes()==word
