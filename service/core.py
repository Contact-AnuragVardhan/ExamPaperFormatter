import hashlib
import hmac
import json
import logging
import shutil
import threading
import time
import uuid
from pathlib import Path
import job
import records
import reporting
from runtime_logging import event,log_context,actor_key,error_fields,register_secrets
from .files import UserError,filename,validate_docx
from .storage import Store
from .meta import MetaClient

log=logging.getLogger('exam.service')
ACTIVE=('queued','running','uncertain')

class Service:
    def __init__(self,settings,engine=None,meta=None):
        self.s=settings
        register_secrets(settings.teacher_code,settings.admin_password,settings.wa_token,
                         settings.wa_app_secret,settings.wa_verify_token)
        # Desktop and service read the same index and log when using the same data directory.
        records.DATA=settings.data
        records.REFERENCES=settings.data/'references'
        records.INDEX=records.REFERENCES/'index.json'
        records.LOG=settings.data/'activity.log'
        records.USAGE=settings.data/'usage.jsonl'
        self.db=Store(settings.data)
        # A process restart requires fresh admin authentication, including after
        # a Render change to ADMIN_PASSWORD. Web cookies use a separate table.
        self.db.execute('DELETE FROM whatsapp_admin')
        self.engine=engine or job.run
        self.meta=meta or MetaClient(settings)
        self.lock=threading.RLock()
        self.stop=threading.Event()
        self.threads=[]
        (settings.data/'jobs').mkdir(exist_ok=True)
        (settings.data/'pending').mkdir(exist_ok=True)
        (settings.data/'admin_downloads').mkdir(exist_ok=True)

    def admin_authenticated(self,sender,touch=False):
        row=self.db.one('SELECT * FROM whatsapp_admin WHERE sender=? AND expires>?',(sender,time.time()))
        if row and touch:
            self.db.execute('UPDATE whatsapp_admin SET expires=? WHERE sender=?',(time.time()+self.s.session_minutes*60,sender))
        return bool(row)

    def admin_logout(self,sender):
        self.db.execute('DELETE FROM whatsapp_admin WHERE sender=?',(sender,))

    def password_proof(self,sender,mid,text):
        # Bind to this sender and webhook message; never store a password or a
        # reusable password hash. The proof is verified with the current secret.
        value=json.dumps([sender,mid,text],ensure_ascii=False).encode('utf-8')
        return hmac.new(self.s.admin_password.encode('utf-8'),value,hashlib.sha256).hexdigest()

    def prepare_incoming(self,sender,mid,message):
        message=json.loads(json.dumps(message))
        message.pop('_admin_password_proof',None)
        if message.get('type')=='text':
            text=str(message.get('text',{}).get('body',''))
            message['_admin_password_proof']=self.password_proof(sender,mid,text)
            login=self.db.session(sender,self.s.session_minutes)['state']=='admin_login'
            secret_match=bool(self.s.admin_password and hmac.compare_digest(text.encode('utf-8'),self.s.admin_password.encode('utf-8')))
            if (login and text.strip().casefold().strip(' !.,?') not in ('hi','hello','hey','menu','start','help')) or secret_match:
                message['text']={'body':''}
        return message

    def admin_reference(self,rid):
        ref=next((r for r in self.references() if r['id']==rid),None)
        if not ref: raise UserError('Reference no longer exists. Refresh the reference list.')
        return ref

    def save_reference(self,subject,grade,data,enabled=False,expected_version=None):
        subject=subject.strip();grade=grade.strip()
        if not subject or len(subject)>80 or not grade or len(grade)>20 or any(ord(c)<32 for c in subject+grade):
            raise UserError('Enter a subject (up to 80 characters) and class (up to 20 characters).')
        validate_docx(data,'reference.docx',self.s.max_bytes)
        with self.lock:
            existing=records.find_reference(subject,grade)
            version=hashlib.sha256(existing.path.read_bytes()).hexdigest() if existing and existing.path.is_file() else ''
            if expected_version is not None and version!=expected_version:
                raise UserError('This reference changed while you were uploading. Start the upload again to review the latest reference.')
            action,ref=records.save_reference(subject,grade,data)
            self.db.execute('INSERT OR REPLACE INTO reference_settings VALUES(?,?)',(ref.path.stem,int(enabled)))
        event(log,'reference.saved',reference_id=ref.path.stem,subject=subject,grade=grade,enabled=enabled,bytes=len(data),action=action)
        return self.admin_reference(ref.path.stem)

    def enable_reference(self,rid,enabled):
        with self.lock:
            self.admin_reference(rid)
            self.db.execute('INSERT OR REPLACE INTO reference_settings VALUES(?,?)',(rid,int(enabled)))
        event(log,'reference.enabled_changed',reference_id=rid,enabled=enabled)

    def delete_reference(self,rid,expected_version=None):
        with self.lock:
            ref=self.admin_reference(rid)
            if expected_version is not None and ref['version']!=expected_version:
                raise UserError('This reference changed. Review it again before deleting.')
            records.delete_reference(ref['subject'],ref['grade'])
            self.db.execute('DELETE FROM reference_settings WHERE id=?',(rid,))
        event(log,'reference.deleted',reference_id=rid)

    def reconcile(self,jid,confirmed_no_remote_request=False):
        with self.lock:
            row=self.get(jid)
            if not row or row['state']!='uncertain': raise UserError('Only unresolved requests can be reconciled.')
            remote=json.loads(row['remote'] or '{}')
            if remote.get('response_id'):
                self.update(jid,state='queued',message='Resuming the existing response.')
                return 'Resuming the existing response without a new formatting request.'
            if not confirmed_no_remote_request:
                raise UserError('Check the OpenAI dashboard and confirm no remote request remains active before releasing this request.')
            self.update(jid,state='failed',retryable=1,message='Administrator confirmed that no remote request remains active. You may retry.')
            return 'Request released for a safe manual retry.'

    def admin_document(self,sender,data,name,caption,dedupe):
        validate_docx(data,name,self.s.max_bytes)
        folder=self.s.data/'admin_downloads'/uuid.uuid4().hex;folder.mkdir()
        path=folder/filename(name);path.write_bytes(data)
        self.db.enqueue(sender,'document',{'path':str(path),'caption':caption,'admin_only':True},dedupe=dedupe)
        event(log,'whatsapp.admin.document_queued',bytes=len(data),sender_key=actor_key(sender))

    def references(self):
        result=[]
        order={'math':0,'chemistry':1,'english':2}
        for ref in records.list_references():
            rid=ref.path.stem
            row=self.db.one('SELECT * FROM reference_settings WHERE id=?',(rid,))
            enabled=bool(row['enabled']) if row else ref.subject.casefold()!='english'
            exists=ref.path.is_file()
            version=hashlib.sha256(ref.path.read_bytes()).hexdigest() if exists else ''
            result.append(dict(id=rid,subject=ref.subject,grade=ref.grade,enabled=enabled and exists,
                configured_enabled=enabled,available=exists,version=version,
                note='' if enabled and exists else ('Reference file missing' if not exists else 'Awaiting staff validation')))
        return sorted(result,key=lambda r:(order.get(r['subject'].casefold(),3),r['subject'],r['grade']))

    def resolve(self,rid):
        for ref in self.references():
            if ref['id']==rid:
                if not ref['enabled']: raise UserError('This subject is not available yet. Please choose another subject.')
                return ref,records.REFERENCES/(rid+'.docx')
        raise UserError('The selected reference is no longer available. Please select a subject again.')

    def active(self,owner):
        return self.db.one("SELECT * FROM jobs WHERE owner=? AND state IN ('queued','running','uncertain') ORDER BY created DESC LIMIT 1",(owner,))

    def submit(self,owner,channel,kind,rid,name,data,idem):
        if kind not in ('check','format'): raise UserError('Choose Check Exam or Format Exam.')
        if not idem or len(idem)>150: raise UserError('A valid submission ID is required.')
        with self.lock:
            existing=self.db.one('SELECT * FROM jobs WHERE owner=? AND idem=?',(owner,idem))
            if existing:
                event(log,'job.submission.duplicate',job_id=existing['id'],channel=channel)
                return existing
            active=self.active(owner)
            if active:
                event(log,'job.submission.blocked',level=logging.WARNING,job_id=active['id'],reason='active_job',state=active['state'])
                raise UserError('An exam is already being processed. Wait for its result before starting another.')
            if self.db.limited('jobs:'+owner,20,3600):
                event(log,'job.submission.blocked',level=logging.WARNING,actor=actor_key(owner),reason='hourly_limit')
                raise UserError('You have reached the hourly exam limit. Please try again later.')
            ref,ref_path=self.resolve(rid)
            validate_docx(data,name,self.s.max_bytes)
            reference_data=ref_path.read_bytes()
            validate_docx(reference_data,'reference.docx',self.s.max_bytes)
            jid=uuid.uuid4().hex
            folder=self.s.data/'jobs'/jid; folder.mkdir()
            (folder/'teacher.docx').write_bytes(data)
            (folder/'reference.docx').write_bytes(reference_data)
            now=time.time()
            self.db.execute('''INSERT INTO jobs(id,owner,channel,kind,reference_id,subject,grade,version,filename,state,idem,created,updated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(jid,owner,channel,kind,rid,ref['subject'],ref['grade'],ref['version'],filename(name),'queued',idem,now,now))
            event(log,'job.queued',job_id=jid,actor=actor_key(owner),channel=channel,kind=kind,
                  subject=ref['subject'],grade=ref['grade'],reference_id=rid,
                  reference_version=ref['version'][:12],teacher_bytes=len(data),reference_bytes=len(reference_data))
            return self.get(jid)

    def get(self,jid): return self.db.one('SELECT * FROM jobs WHERE id=?',(jid,))
    def public(self,row):
        return {k:row[k] for k in ('id','channel','kind','subject','grade','filename','state','result_status','message','retryable','delivery','created','updated')} | {'has_output':bool(row['output'])}
    def admin_public(self,row):
        remote=json.loads(row['remote'] or '{}')
        diagnostic=json.loads(row.get('diagnostic') or '{}')
        if row['state'] in ('failed','uncertain') and not diagnostic:
            diagnostic={'stage':'unknown','message':'This older request did not retain its underlying error. It cannot be reconstructed from the job record.'}
        return self.public(row) | {'remote_response_id':remote.get('response_id',''),'diagnostic':diagnostic}
    def diagnostic(self,jid,detail,**metadata):
        secrets=(self.s.teacher_code,self.s.admin_password,self.s.wa_token,
                 self.s.wa_app_secret,self.s.wa_verify_token)
        # Only bounded scalar diagnostic fields, never request bodies or exam files.
        values={k:job.redact(str(v),secrets) if isinstance(v,str) else v
                for k,v in metadata.items() if k in ('stage','model','error_type','http_status','response_id','request_id','error_code')
                and isinstance(v,(str,int,float,bool,type(None)))}
        values['message']=job.redact(str(detail),secrets)
        self.update(jid,diagnostic=json.dumps(values))
        event(log,'job.failed',level=logging.ERROR,job_id=jid,
              stage=values.get('stage','result_validation'),error_type=values.get('error_type','result'),
              http_status=values.get('http_status'),error_code=values.get('error_code'),
              model=values.get('model'),response_id=values.get('response_id'),
              provider_request_id=values.get('request_id'))
    def update(self,jid,**values):
        before=self.get(jid) if 'state' in values or 'delivery' in values else None
        values['updated']=time.time()
        self.db.execute('UPDATE jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),jid))
        if before:
            for field in ('state','delivery'):
                if field in values and values[field]!=before[field]:
                    event(log,'job.'+field+'.changed',job_id=jid,previous=before[field],current=values[field])
    def cancel(self,jid,owner):
        with self.lock:
            row=self.get(jid)
            if not row or row['owner']!=owner: raise UserError('Exam request not found.')
            if row['state']=='queued':
                self.update(jid,state='cancelled',message='Cancelled before processing started.')
                event(log,'job.cancel.confirmed',job_id=jid)
                return True
            event(log,'job.cancel.unavailable',job_id=jid,state=row['state'])
            return False
    def retry(self,jid,owner,idem):
        event(log,'job.retry.requested',job_id=jid,actor=actor_key(owner))
        row=self.get(jid)
        if not row or row['owner']!=owner or row['state']!='failed' or not row['retryable']:
            raise UserError('This request cannot be retried safely. Choose another file, or contact support if processing is still unresolved.')
        folder=self.s.data/'jobs'/jid
        # Reuse the pinned reference, even if the administrator replaced it.
        with self.lock:
            current=records.REFERENCES/(row['reference_id']+'.docx')
            _,path=self.resolve(row['reference_id'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=row['version']:
                raise UserError('The subject reference has changed. Start a new request with a fresh subject selection.')
            return self.submit(owner,row['channel'],row['kind'],row['reference_id'],row['filename'],(folder/'teacher.docx').read_bytes(),idem)

    def start(self):
        event(log,'workers.starting')
        # Resume a known remote response; never repeat an ambiguous paid POST.
        for row in self.db.all("SELECT * FROM jobs WHERE state='running'"):
            remote=json.loads(row['remote'] or '{}')
            event(log,'job.restart.recovery',job_id=row['id'],known_response=bool(remote.get('response_id')))
            if remote.get('response_id'): self.update(row['id'],state='queued',message='Resuming the existing request.')
            else: self.update(row['id'],state='uncertain',message='Processing was interrupted. An administrator must check its status before another exam can be submitted.')
        self.db.execute("UPDATE inbox SET state='queued' WHERE state='processing'")
        self.db.execute("UPDATE outbox SET state='queued' WHERE state='sending'")
        for row in self.db.all("SELECT * FROM jobs WHERE channel='whatsapp' AND delivery IS NULL AND state IN ('completed','needs_changes','failed','uncertain')"):
            self.finish_whatsapp(row)
        for target in (self.jobs_loop,self.messages_loop,self.delivery_loop):
            thread=threading.Thread(target=target,daemon=True,name=target.__name__);thread.start();self.threads.append(thread)
            event(log,'worker.started',worker=target.__name__)
    def close(self):
        event(log,'workers.stopping',count=len(self.threads))
        self.stop.set()
        for t in self.threads: t.join(timeout=2)
        event(log,'workers.stopped',still_running=sum(t.is_alive() for t in self.threads))
    def jobs_loop(self):
        cleaned=0
        while not self.stop.is_set():
            try:
                if time.time()-cleaned>3600: self.cleanup();cleaned=time.time()
                if not self.process_one(): self.stop.wait(.4)
            except Exception as exc:
                event(log,'worker.failed',level=logging.ERROR,worker='jobs_loop',**error_fields(exc))
                self.stop.wait(1)
    def process_one(self):
        with self.lock:
            row=self.db.one("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1")
            if not row: return False
            jid=row['id'];self.update(jid,state='running',message='Your exam is being processed.')
        started=time.monotonic()
        event(log,'job.started',job_id=jid,channel=row['channel'],kind=row['kind'],subject=row['subject'],grade=row['grade'])
        folder=self.s.data/'jobs'/jid
        def progress(_): self.update(jid,message='Your exam is being checked.' if row['kind']=='check' else 'Your exam is being formatted.')
        def remote(state):
            self.update(jid,remote=json.dumps(state))
            event(log,'job.remote.saved',job_id=jid,response_id=state.get('response_id'),
                  container_id=state.get('container_id'),submitting=state.get('submitting',False),
                  cached_result=state.get('result_status'),rejected=state.get('response_rejected',False))
        try:
            cached=json.loads(row['remote'] or '{}')
            if cached.get('result_status') in ('OK','PASS','FORMAT ERROR','STRUCTURAL ERROR'):
                event(log,'job.cached_result.recovered',job_id=jid,result_status=cached['result_status'])
                # The engine persisted this stage before deleting its remote
                # container. Recover its exact local output after a crash.
                saved=folder/Path(cached['output_file']).name if cached.get('output_file') else None
                result=job.Result(cached['result_status'],'',saved)
            else:
                with log_context(job_id=jid):
                    result=self.engine(row['kind'],row['subject'],row['grade'],folder/'reference.docx',folder/'teacher.docx',progress,
                        output_dir=folder,remote_state=cached,on_remote=remote)
            output=None
            if result.saved_path:
                source=Path(result.saved_path)
                payload=source.read_bytes()
                validate_docx(payload,source.name,self.s.max_bytes)
                role='Formatted' if result.status=='PASS' else 'Issues'
                name=f'Exam_{role}_{records._filename_piece(row["subject"])}_{records._filename_piece(row["grade"])}_{jid[:10]}.docx'
                target=folder/name
                target.write_bytes(payload)  # Deliver exact engine bytes, without re-saving in python-docx.
                output=str(target)
                event(log,'job.output.saved',job_id=jid,bytes=len(payload),result_status=result.status)
            report=reporting.assess_engine(result.status, result.detail)
            if report and report.status==reporting.ALL_GOOD and result.status=='PASS' and output:
                state='completed';message=report.message
            elif report and report.status==reporting.ALL_GOOD and result.status=='OK' and row['kind']=='check':
                state='completed';message=report.message
            elif report and report.proceed and result.status in ('FORMAT ERROR','STRUCTURAL ERROR') and output:
                state='completed';message=report.message
                # An issues file is not a formatted exam, so a clear proceed result does not offer it as one.
                if report.status==reporting.ALL_GOOD: output=None
            elif report and report.status==reporting.STRUCTURAL and output:
                state='needs_changes';message=report.message
            else:
                report=None
                state='uncertain' if result.uncertain else 'failed'
                diagnostic=dict(result.diagnostic or {})
                detail=diagnostic.pop('message',result.detail)
                diagnostic.setdefault('stage',{'configuration':'configuration','incomplete':'inspect_response',
                    'missing_output':'download_output','invalid_output':'validate_output',
                    'unrecognized':'inspect_response'}.get(result.error_code,'result_validation'))
                diagnostic['error_code']=result.error_code
                self.diagnostic(jid,detail,**diagnostic)
                errors={
                    'busy':'The service is busy. Please try again shortly.',
                    'timeout':'Processing took too long and was stopped. Please try again.',
                    'configuration':'The formatter is not available. Please contact the administrator.',
                    'incomplete':'Processing stopped before a complete result was ready. Please try again.',
                    'missing_output':'No Word result file was produced. Please contact the administrator.',
                    'invalid_output':'The result was not a valid Word file. Please contact the administrator.',
                    'unrecognized':'The formatter returned an unexpected result. Please contact the administrator.',
                    'provider_failed':'Formatting failed. Please try again later or contact the administrator.'}
                message='Processing status is unresolved. Please contact the administrator before retrying.' if result.uncertain else errors.get(result.error_code,'The formatter could not produce a complete result. Please try again later or contact the administrator.')
                output=None
            shown=report.status if report else result.status
            if report: reporting.remember(report,engine_status=result.status,engine_detail=result.detail,job_id=jid,logger=log)
            self.update(jid,state=state,result_status=shown,message=message,output=output,retryable=int(result.retryable and not result.uncertain))
            if state in ('completed','needs_changes'): self.update(jid,diagnostic=None)
            records.log(row['subject'],row['grade'],row['kind'].upper(),shown)
        except UserError as exc:
            self.diagnostic(jid,str(exc),stage='validate_output',error_type=type(exc).__name__)
            self.update(jid,state='failed',result_status='SYSTEM ERROR',message='No valid Word result file was produced. Please contact the administrator.',output=None,retryable=0)
        except Exception as exc:
            # Unknown engine failure must not invite a second paid call.
            self.diagnostic(jid,str(exc),stage='service_processing',error_type=type(exc).__name__)
            self.update(jid,state='uncertain',result_status='SYSTEM ERROR',message='Processing was interrupted. Please contact the administrator before retrying.',retryable=0)
        if row['channel']=='whatsapp': self.finish_whatsapp(self.get(jid))
        finished=self.get(jid)
        event(log,'job.finished',job_id=jid,state=finished['state'],result_status=finished['result_status'],
              has_output=bool(finished['output']),elapsed_seconds=round(time.monotonic()-started,2))
        return True

    def finish_whatsapp(self,row):
        """WhatsApp delivery adapter; exam classification remains unchanged."""
        from .whatsapp import WhatsAppFlow
        event(log,'whatsapp.result.queued',job_id=row['id'],document=bool(row['output']),state=row['state'])
        sender=row['owner'][3:]
        flow=WhatsAppFlow(self)
        if row['output']:
            self.db.enqueue(sender,'document',
                {'path':row['output'],'caption':flow.document_caption(row)},
                row['id'],'result:'+row['id'])
            self.update(row['id'],delivery='queued')
        else:
            # With no output, keep the existing message and menu delivery flow.
            self.update(row['id'],delivery='not_required')
            self.db.emit(sender,row['message'],row['id'],'error:'+row['id'])
            self.set_finished_session(sender,row)
            self.emit_reply(sender,self.navigation_menu(sender,row),row['id'],'next:'+row['id'])
    def emit_reply(self,sender,content,job_id=None,dedupe=None):
        if isinstance(content,dict):
            from .menus import replies
            with self.lock:
                if dedupe and self.db.one('SELECT id FROM outbox WHERE dedupe=?',(dedupe,)): return
                content=json.loads(json.dumps(content))
                token=uuid.uuid4().hex[:12]
                actions={}
                for choice in replies(content):
                    command=choice['id'];choice['id']='ef:'+token+':'+command
                    actions[choice['id']]=command
                state=self.db.session(sender,self.s.session_minutes)
                state['menu_actions']=actions;state['menu_version']=token
                self.db.save_session(sender,state)
                event(log,'whatsapp.menu.queued',job_id=job_id,menu=state['state'],menu_type=content['type'],choices=len(actions))
                self.db.emit(sender,content,job_id,dedupe)
        else: self.db.emit(sender,content,job_id,dedupe)
    def set_finished_session(self,sender,row):
        state=self.db.session(sender,self.s.session_minutes)
        # A Hi reset or another menu must not be overwritten by a late result.
        if state.get('state')=='processing' and state.get('job_id')==row['id']:
            state={'state':'result' if row['state'] in ('completed','needs_changes') else 'error','job_id':row['id'],
                'reference_id':row['reference_id']}
            self.db.save_session(sender,state)
    def navigation_menu(self,sender,row):
        from .whatsapp import WhatsAppFlow
        flow=WhatsAppFlow(self)
        state=self.db.session(sender,self.s.session_minutes)
        if state.get('job_id')==row['id'] and state.get('state') in ('result','error'):
            return self.result_menu(row)
        return flow.current_menu(sender,state)

    def result_menu(self,row):
        from .whatsapp import WhatsAppFlow
        return WhatsAppFlow(self).result_menu(row)

    def handle_incoming(self,message_id):
        """Claim one persisted message atomically, including in local sync mode."""
        from .whatsapp import WhatsAppFlow
        claimed=self.db.execute("UPDATE inbox SET state='processing' WHERE id=? AND state='queued'",(message_id,))
        if not claimed: return False
        row=self.db.one('SELECT * FROM inbox WHERE id=?',(message_id,))
        message=json.loads(row['payload'])
        if self.db.session(row['sender'],self.s.session_minutes)['state']=='admin_login' and message.get('type')=='text':
            # Also cover a password queued immediately behind the Admin tap.
            # Preserve greetings; remove login text before processing/logging.
            text=str(message.get('text',{}).get('body',''))
            if text.strip().casefold().strip(' !.,?') not in ('hi','hello','hey','menu','start','help'):
                message['text']={'body':''}
            self.db.execute('UPDATE inbox SET payload=? WHERE id=?',(json.dumps(message),message_id))
        event(log,'whatsapp.message.started',message_id=message_id,sender_key=actor_key(row['sender']))
        try:
            with log_context(message_id=message_id,sender_key=actor_key(row['sender'])):
                WhatsAppFlow(self).handle(row['sender'],message,row['id'])
            self.db.execute("UPDATE inbox SET state='done' WHERE id=?",(row['id'],))
            event(log,'whatsapp.message.finished',message_id=message_id)
        except Exception as exc:
            self.db.emit(row['sender'],'We could not handle that message. Please send it again, or say Hi for the main menu.',dedupe='inboxerror:'+row['id'])
            self.emit_reply(row['sender'],WhatsAppFlow(self).current_menu(row['sender']),dedupe='inboxmenu:'+row['id'])
            self.db.execute("UPDATE inbox SET state='failed' WHERE id=?",(row['id'],))
            event(log,'whatsapp.message.failed',level=logging.ERROR,message_id=row['id'],**error_fields(exc))
        return True

    def messages_loop(self):
        while not self.stop.is_set():
            try:
                row=self.db.one("SELECT * FROM inbox WHERE state='queued' ORDER BY created LIMIT 1")
                if not row: self.stop.wait(.2);continue
                self.handle_incoming(row['id'])
            except Exception as exc:
                event(log,'worker.failed',level=logging.ERROR,worker='messages_loop',**error_fields(exc));self.stop.wait(1)

    def delivery_loop(self):
        while not self.stop.is_set():
            try:
                if not self.deliver_one(): self.stop.wait(.3)
            except Exception as exc:
                event(log,'worker.failed',level=logging.ERROR,worker='delivery_loop',**error_fields(exc));self.stop.wait(1)
    def deliver_one(self):
        # Preserve message ordering per sender; do not let next-menu overtake a document.
        row=self.db.one('''SELECT * FROM outbox o WHERE state='queued' AND due<=?
          AND NOT EXISTS(SELECT 1 FROM outbox p WHERE p.sender=o.sender AND p.id<o.id AND p.state IN ('queued','sending'))
          ORDER BY id LIMIT 1''',(time.time(),))
        if not row: return False
        self.db.execute("UPDATE outbox SET state='sending' WHERE id=?",(row['id'],))
        payload=json.loads(row['payload'])
        started=time.monotonic()
        event(log,'whatsapp.delivery.started',outbox_id=row['id'],job_id=row['job_id'],kind=row['kind'],
              attempt=row['attempts']+1,sender_key=actor_key(row['sender']))
        try:
            if row['kind']=='text': mid=self.meta.send_text(row['sender'],payload['text'])
            elif row['kind']=='interactive': mid=self.meta.send_interactive(row['sender'],payload)
            else:
                if payload.get('admin_only') and not self.admin_authenticated(row['sender']):
                    self.db.execute("UPDATE outbox SET state='cancelled' WHERE id=?",(row['id'],))
                    event(log,'whatsapp.admin.download.cancelled',outbox_id=row['id'],reason='admin_access_expired')
                    return True
                path=Path(payload['path'])
                if not payload.get('media_id'):
                    payload['media_id']=self.meta.upload(path)
                    self.db.execute('UPDATE outbox SET payload=? WHERE id=?',(json.dumps(payload),row['id']))
                mid=self.meta.send_document(row['sender'],payload['media_id'],path.name,payload['caption'])
            event(log,'whatsapp.delivery.sent',outbox_id=row['id'],job_id=row['job_id'],kind=row['kind'],
                  meta_message_id=mid,elapsed_ms=round((time.monotonic()-started)*1000))
            self.db.execute("UPDATE outbox SET state='sent',meta_id=? WHERE id=?",(mid,row['id']))
            if row['kind']=='document' and row['job_id']:
                current=self.get(row['job_id']);self.update(row['job_id'],delivery='sent')
                self.set_finished_session(row['sender'],current)
                self.emit_reply(row['sender'],self.navigation_menu(row['sender'],current),row['job_id'],'next:'+row['job_id'])
        except Exception as exc:
            n=row['attempts']+1
            event(log,'whatsapp.delivery.failed',level=logging.ERROR,outbox_id=row['id'],job_id=row['job_id'],
                  kind=row['kind'],attempt=n,retry=n<3,retry_seconds=min(60,2**n) if n<3 else None,**error_fields(exc))
            self.db.execute('UPDATE outbox SET state=?,attempts=?,due=? WHERE id=?',
                ('failed' if n>=3 else 'queued',n,time.time()+min(60,2**n),row['id']))
            if n>=3 and row['job_id'] and row['kind']=='document':
                self.update(row['job_id'],delivery='failed')
                self.db.emit(row['sender'],'The result is saved, but delivery failed. Say Hi and tap Retry Delivery to send the same file.',dedupe='deliveryerror:'+row['job_id'])
                from .whatsapp import WhatsAppFlow
                self.emit_reply(row['sender'],WhatsAppFlow(self).current_menu(row['sender']),dedupe='deliverymenu:'+row['job_id'])
            elif n>=3 and row['kind']=='document' and not row['job_id']:
                self.db.emit(row['sender'],'The admin file could not be delivered. Open the latest menu and choose Download again.',dedupe='admin-deliveryerror:'+str(row['id']))
                from .whatsapp import WhatsAppFlow
                self.emit_reply(row['sender'],WhatsAppFlow(self).current_menu(row['sender']),dedupe='admin-deliverymenu:'+str(row['id']))
        return True
    def retry_delivery(self,jid,owner):
        event(log,'whatsapp.delivery.retry_requested',job_id=jid,actor=actor_key(owner))
        row=self.get(jid)
        if not row or row['owner']!=owner or not row['output']: raise UserError('There is no saved file available to deliver.')
        changed=self.db.execute("UPDATE outbox SET state='queued',attempts=0,due=? WHERE job_id=? AND kind='document' AND state='failed'",(time.time(),jid))
        if changed: self.update(jid,delivery='queued')
        return bool(changed)
    def delivery_status(self,mid,status):
        row=self.db.one('SELECT * FROM outbox WHERE meta_id=?',(mid,))
        if not row or row['kind']!='document' or not row['job_id']:
            event(log,'whatsapp.delivery.status_ignored',level=logging.DEBUG,meta_message_id=mid,status=status)
            return
        event(log,'whatsapp.delivery.status',job_id=row['job_id'],meta_message_id=mid,status=status)
        current=self.get(row['job_id'])
        # Late callbacks must not regress an already delivered/read document.
        rank={'queued':0,'sent':1,'delivered':2,'read':3}
        if status=='failed':
            if rank.get(current['delivery'],0)<2:
                self.update(row['job_id'],delivery='failed')
                self.db.execute("UPDATE outbox SET state='failed' WHERE id=?",(row['id'],))
        elif status in rank and rank[status]>=rank.get(current['delivery'],0):
            self.update(row['job_id'],delivery=status)
    def cleanup(self):
        started=time.monotonic();removed_jobs=0;removed_pending=0
        cutoff=time.time()-self.s.retention_days*86400
        for row in self.db.all("SELECT * FROM jobs WHERE updated<? AND state NOT IN ('queued','running','uncertain')",(cutoff,)):
            if self.db.one("SELECT id FROM outbox WHERE job_id=? AND state IN ('queued','sending')",(row['id'],)): continue
            shutil.rmtree(self.s.data/'jobs'/row['id'],ignore_errors=True)
            self.db.execute('DELETE FROM outbox WHERE job_id=?',(row['id'],))
            self.db.execute('DELETE FROM jobs WHERE id=?',(row['id'],))
            removed_jobs+=1
        for folder in (self.s.data/'jobs').iterdir():
            if folder.is_dir() and folder.stat().st_mtime<cutoff and not self.get(folder.name):
                shutil.rmtree(folder,ignore_errors=True)
        for folder in (self.s.data/'pending').iterdir():
            if folder.stat().st_mtime<time.time()-self.s.session_minutes*60:
                shutil.rmtree(folder,ignore_errors=True);removed_pending+=1
        self.db.execute('DELETE FROM rates WHERE until<?',(time.time(),))
        self.db.execute('DELETE FROM auth WHERE expires<?',(time.time(),))
        self.db.execute('DELETE FROM whatsapp_admin WHERE expires<?',(time.time(),))
        for folder in (self.s.data/'admin_downloads').iterdir():
            if folder.stat().st_mtime<cutoff:
                pending=self.db.all("SELECT payload FROM outbox WHERE job_id IS NULL AND kind='document' AND state IN ('queued','sending')")
                if not any(Path(json.loads(r['payload'])['path']).parent==folder for r in pending):
                    shutil.rmtree(folder,ignore_errors=True)
        self.db.execute('DELETE FROM sessions WHERE updated<?',(time.time()-self.s.session_minutes*60,))
        self.db.execute("DELETE FROM inbox WHERE created<? AND state IN ('done','failed')",(cutoff,))
        self.db.execute("DELETE FROM outbox WHERE created<? AND job_id IS NULL AND state IN ('sent','failed','cancelled')",(cutoff,))
        event(log,'storage.cleanup.completed',removed_jobs=removed_jobs,removed_pending=removed_pending,
              elapsed_ms=round((time.monotonic()-started)*1000))
