"""Password-protected native WhatsApp menus for the shared REV2 admin service."""
import hmac
import json
import logging
import os
import time
import uuid
from pathlib import Path
import job
import records
from runtime_logging import event,actor_key,error_fields
from .files import UserError,filename,validate_docx
from .menus import menu

log=logging.getLogger('exam.whatsapp.admin')

class WhatsAppAdmin:
    def __init__(self,flow):
        self.flow=flow;self.v=flow.v;self.db=flow.db

    def login_menu(self):
        return menu('Admin Sign In','Send the administrator password configured in ADMIN_PASSWORD as a text message. Password entry is the only sign-in step; menu choices are tappable.\n\nSay Hi to cancel.',[('menu.main','Cancel')])

    def home(self):
        return menu('Exam Formatter — Admin','Manage reference papers, subject availability, requests, activity, and estimated costs. Choose an option.',[
            ('admin.refs','Reference Papers'),('admin.add','Upload New Reference'),('admin.jobs','Exam Requests'),
            ('admin.activity','Activity Log'),('admin.cost','Cost Totals'),('admin.system','System Status'),
            ('admin.help','Admin Help'),('admin.logout','Logout')],force_list=True)

    def page(self,state,items,prefix,title,body,back,extra=()):
        page=min(max(0,state.get('page',0)),max(0,(len(items)-1)//7))
        state['page']=page
        choices=[(prefix+'.'+str(i),label,detail) for i,(label,detail) in enumerate(items[page*7:(page+1)*7],page*7)]
        if page: choices.append(('admin.previous','Previous Page'))
        if (page+1)*7<len(items): choices.append(('admin.next','More Options'))
        choices.append((back,'Back'))
        # Page size may be reduced by the caller for extra actions.
        choices.extend(extra)
        return menu(title,body+f'\nPage {page+1} of {max(1,(len(items)+6)//7)}',choices,force_list=True)

    def ref_list(self,state):
        refs=self.v.references();state['ref_ids']=[r['id'] for r in refs]
        items=[(f'{r["subject"].title()} — {r["grade"]}',('Enabled' if r['enabled'] else 'Disabled')+(' | File missing' if not r['available'] else ' | Reference paper')) for r in refs]
        return self.page(state,items,'admin.ref','Reference Papers','Select a paper to download, replace, enable/disable, or delete it. Disabled subjects are included.','admin.home')

    def ref_detail(self,state):
        ref=self.v.admin_reference(state['admin_ref_id'])
        choices=[]
        if ref['available']: choices.append(('admin.ref.download','Download Reference'))
        choices.extend([('admin.ref.replace','Replace Reference'),('admin.ref.toggle','Disable Subject' if ref['configured_enabled'] else 'Enable Subject'),
                        ('admin.ref.delete','Delete Reference'),('admin.refs','Back to References'),('admin.home','Admin Menu')])
        state['viewed_version']=ref['version']
        body=f'Subject: {ref["subject"].title()}\nClass: {ref["grade"]}\nTeacher availability: {"Enabled" if ref["enabled"] else "Disabled"}\nReference file: {"Available" if ref["available"] else "Missing"}\n\nEnabling makes this subject visible to teachers. Validate the reference before enabling.'
        return menu('Manage Reference Paper',body,choices,force_list=True)

    def subject_options(self,state):
        subjects=sorted({r['subject'] for r in self.v.references()}|{'math','chemistry','english','physics','biology','history','geography','computer science','hindi'})
        state['subject_options']=subjects+[None]
        return self.page(state,[(s.title(),'Choose subject') for s in subjects]+[('Other Subject','Type a new subject name')],
                         'admin.subject','Reference Subject','Choose an existing/common subject, or Other Subject to enter a new one.','admin.home')

    def grade_options(self,state):
        grades=[str(i) for i in range(1,13)]+['KG',None];state['grade_options']=grades
        return self.page(state,[('Class '+g,'Choose class') if g else ('Other Class','Type a class/grade') for g in grades],
                         'admin.grade','Reference Class','Choose the class for '+state['admin_subject']+'.','admin.add')

    def prepare_upload(self,state):
        ref=next((r for r in self.v.references() if r['subject'].casefold()==state['admin_subject'].casefold() and r['grade'].casefold()==state['admin_grade'].casefold()),None)
        state['upload_existing_id']=ref['id'] if ref else None
        state['upload_expected_version']=ref['version'] if ref else ''
        state['upload_enabled']=ref['configured_enabled'] if ref else False
        state['state']='admin_upload'

    def upload_menu(self,state):
        replacing=bool(state.get('upload_existing_id'))
        return menu('Upload Reference Paper',f'Subject: {state["admin_subject"]}\nClass: {state["admin_grade"]}\n\nAttach a Word .docx reference paper (up to {self.v.s.max_upload_mb} MB). '+
                    ('This will replace the existing paper only after confirmation.' if replacing else 'New subjects start disabled until you enable them.')+
                    '\nNo exam formatting will run.',[('admin.upload.cancel','Cancel Upload'),('admin.home','Admin Menu')])

    def upload_confirmation(self,state):
        return menu('Confirm Reference Upload',f'Subject: {state["admin_subject"]}\nClass: {state["admin_grade"]}\nFile: {state["filename"]}\nTeacher availability after save: {"Enabled" if state["upload_enabled"] else "Disabled"}\n'+
                    ('The existing reference will be replaced.' if state.get('upload_existing_id') else 'A new reference will be added.')+
                    '\n\nEnable only after staff validation. Save writes the same reference used by web and WhatsApp teachers.',[
                    ('admin.upload.save','Save Reference'),('admin.upload.toggle','Disable After Save' if state['upload_enabled'] else 'Enable After Save'),
                    ('admin.upload.replace','Choose Another File'),('admin.upload.cancel','Cancel Upload')],force_list=True)

    def job_rows(self,state):
        filters={'all':'1=1','issues':"(state IN ('failed','uncertain') OR delivery='failed')",'active':"state IN ('queued','running','uncertain')",'delivery':"channel='whatsapp' AND delivery='failed'"}
        where=filters.get(state.get('job_filter','all'),'1=1')
        return self.db.all('SELECT * FROM jobs WHERE '+where+' ORDER BY created DESC LIMIT 200')

    def job_list(self,state):
        rows=self.job_rows(state);state['admin_job_ids']=[r['id'] for r in rows]
        return self.page(state,[(r['subject'].title()+' — '+r['grade'],r['state']+' | '+r['channel']+' | '+r['id'][:8]) for r in rows],
                         'admin.job','Exam Requests','Select a request for status, diagnostics, saved output, or available recovery actions. Showing up to 200 recent requests.','admin.jobs')

    def selected_job(self,state):
        row=self.v.get(state.get('admin_job_id',''))
        if not row: raise UserError('This request is no longer available. Refresh the request list.')
        return row

    def job_detail(self,state):
        row=self.selected_job(state);choices=[('admin.job.refresh','Refresh Status'),('admin.job.diagnostics','Error Diagnostics')]
        if row['output'] and Path(row['output']).is_file(): choices.append(('admin.job.download','Download Result'))
        if row['state']=='queued': choices.append(('admin.job.cancel','Cancel Queued Request'))
        if row['state']=='failed' and row['retryable']: choices.append(('admin.job.retry','Retry Formatting' if row['kind']=='format' else 'Retry Checking'))
        if row['channel']=='whatsapp' and row['delivery']=='failed' and row['output']: choices.append(('admin.job.delivery','Retry Delivery'))
        if row['state']=='uncertain': choices.append(('admin.job.resolve','Reconcile Request'))
        choices.extend([('admin.job.back','Back to Requests'),('admin.home','Admin Menu')])
        body=f'Request: {row["id"]}\n{row["subject"].title()} | Class {row["grade"]} | {row["kind"]}\nChannel: {row["channel"]}\nState: {row["state"]}\nResult: {row["result_status"] or "Pending"}\nDelivery: {row["delivery"] or "Not yet sent"}\nFile: {row["filename"]}\n\n{row["message"] or ""}'
        return menu('Manage Exam Request',self.clean(body),choices,force_list=True)

    def clean(self,text):
        return job.redact(str(text),(self.v.s.admin_password,self.v.s.teacher_code,self.v.s.wa_token,self.v.s.wa_app_secret,self.v.s.wa_verify_token))

    def current_menu(self,sender,state):
        try: return self._current_menu(sender,state)
        except UserError as exc:
            # A web administrator may delete a selection while its WhatsApp
            # menu is open. Return to a fresh list rather than strand the worker.
            state['state']='admin_jobs' if state.get('state') in ('admin_job','admin_diagnostics','admin_job_confirm') else 'admin_refs'
            state['page']=0
            self.db.save_session(sender,state)
            result=self._current_menu(sender,state)
            result['body']['text']=(str(exc)+'\n\n'+result['body']['text'])[:1024]
            return result

    def _current_menu(self,sender,state):
        if state['state']=='admin_login': return self.login_menu()
        if not self.v.admin_authenticated(sender):
            self.flow.discard(state);self.db.save_session(sender,{'state':'admin_login'})
            return self.login_menu()
        name=state['state']
        if name=='admin_refs': result=self.ref_list(state)
        elif name=='admin_ref': result=self.ref_detail(state)
        elif name=='admin_subject': result=self.subject_options(state)
        elif name=='admin_grade': result=self.grade_options(state)
        elif name=='admin_newsubject': result=menu('New Subject Name','Send the new subject name as text (up to 80 characters).',[('admin.add','Back'),('admin.home','Admin Menu')])
        elif name=='admin_newgrade': result=menu('New Class / Grade','Send the class/grade as text (up to 20 characters).',[('admin.grade.back','Back'),('admin.home','Admin Menu')])
        elif name=='admin_upload': result=self.upload_menu(state)
        elif name=='admin_confirm': result=self.upload_confirmation(state)
        elif name=='admin_delete':
            ref=self.v.admin_reference(state['admin_ref_id'])
            result=menu('Confirm Reference Deletion',f'Delete {ref["subject"]} — Class {ref["grade"]}? Teachers will no longer be able to select it. Existing requests retain their saved reference. This deletes the stored reference paper.',[('admin.delete.yes','Delete Reference'),('admin.delete.no','Keep Reference')])
        elif name=='admin_toggle':
            ref=self.v.admin_reference(state['admin_ref_id']);enabled=state['toggle_enabled']
            result=menu('Confirm Subject Availability',f'{"Enable" if enabled else "Disable"} {ref["subject"]} — Class {ref["grade"]}? '+
                        ('Teachers will be able to choose it. Confirm you have validated the reference paper.' if enabled else 'It will be hidden from teacher subject menus; existing requests keep their saved reference.'),[('admin.toggle.yes','Confirm Change'),('admin.delete.no','Back')])
        elif name=='admin_job_filters': result=menu('Choose Request View','Choose which exam requests to view.',[
            ('admin.jobs.all','All Requests'),('admin.jobs.issues','Errors / Unresolved'),('admin.jobs.active','Active Requests'),('admin.jobs.delivery','Failed Deliveries'),('admin.home','Admin Menu')],force_list=True)
        elif name=='admin_jobs': result=self.job_list(state)
        elif name=='admin_job': result=self.job_detail(state)
        elif name=='admin_diagnostics':
            row=self.selected_job(state);detail=self.v.admin_public(row)
            body='Request: '+row['id']+'\nRemote response: '+(detail['remote_response_id'] or 'Not recorded')+'\n\n'+json.dumps(detail['diagnostic'],ensure_ascii=False,indent=2)
            result=menu('Request Diagnostics',self.clean(body),[('admin.job.refresh','Back to Request'),('admin.home','Admin Menu')])
        elif name=='admin_job_confirm':
            op=state['job_operation'];row=self.selected_job(state)
            texts={'cancel':'Cancel this queued request before processing starts?',
                   'retry':'Submit a new formatter/check request using the original input? This may incur a new API charge. The original teacher remains the recipient.',
                   'delivery':'Retry delivery of the saved output to the original teacher? This does not rerun the formatter.',
                   'resume':'Resume the recorded remote response without submitting a new formatter request?',
                   'release':'First check the OpenAI dashboard and verify no remote request remains active. Confirm ONLY after this check. This releases the request for a later manual retry; it does not submit a new job.'}
            result=menu('Confirm Request Action',row['id']+'\n\n'+texts[op],[('admin.job.confirm','Confirm Action'),('admin.job.refresh','Back')])
        elif name=='admin_activity':
            chunks=self.activity_chunks();page=min(max(0,state.get('page',0)),len(chunks)-1);state['page']=page
            choices=[]
            if page: choices.append(('admin.previous','Newer Activity'))
            if page+1<len(chunks): choices.append(('admin.next','Older Activity'))
            choices.extend([('admin.activity','Refresh Activity'),('admin.home','Admin Menu')])
            result=menu('Activity Log',self.clean(chunks[page])+f'\n\nPage {page+1} of {len(chunks)}',choices,force_list=True)
        elif name=='admin_cost':
            today,month,total,unpriced=records.cost_totals()
            result=menu('Estimated OpenAI Costs',f'Today: ${today:.2f}\nThis month: ${month:.2f}\nAll time: ${total:.2f}\n\nThese are local estimates, not your provider invoice.'+('\nSome model usage is unpriced and excluded.' if unpriced else ''),[('admin.cost','Refresh Costs'),('admin.home','Admin Menu')])
        elif name=='admin_system':
            counts=self.db.all('SELECT state,COUNT(*) AS count FROM jobs GROUP BY state')
            body=f'WhatsApp configured: {"Yes" if self.v.s.whatsapp_ready else "No"}\nWebhook signature verification: {"Enabled" if self.v.s.wa_verify_signature else "Optional / disabled"}\nAsync webhooks: {self.v.s.wa_async}\nModel: {os.getenv("EXAM_REDO_MODEL", "").strip() or os.getenv("OPENAI_MODEL", "").strip() or "gpt-6-astra"}\nReferences: {len(self.v.references())}\nEnabled subjects: {sum(r["enabled"] for r in self.v.references())}\nAdmin idle timeout: {self.v.s.session_minutes} minutes\n\nRequests:\n'+'\n'.join(f'{r["state"]}: {r["count"]}' for r in counts)
            result=menu('System Status',self.clean(body),[('admin.system','Refresh Status'),('admin.home','Admin Menu')])
        elif name=='admin_help':
            result=menu('Admin Help','Reference Papers: download/view, replace, enable/disable, or delete a paper.\nUpload New Reference: select subject and class, attach .docx, review and save. Choose Other Subject/Class only when you need a new name.\nExam Requests: view errors and available safe recovery actions.\nActivity/Cost: view the same records as the web/Windows admin.\n\nAll choices use menus. Password, new names, and file attachments require input. Say Hi or Logout to end admin access.',[('admin.home','Admin Menu'),('admin.logout','Logout')])
        else: result=self.home()
        self.db.save_session(sender,state)
        return result

    def activity_chunks(self):
        lines=list(reversed((records.read_log() or 'No activity yet.').splitlines()));chunks=[];part=''
        for line in lines:
            line=line[:850]
            if len(part)+len(line)+1>850: chunks.append(part);part=''
            part+=line+'\n'
        if part: chunks.append(part.rstrip())
        return chunks or ['No activity yet.']

    def handle(self,sender,message,mid,state,action):
        def save(): self.db.save_session(sender,state)
        def show(note=None):
            payload=self.current_menu(sender,state)
            if note: payload['body']['text']=(note+'\n\n'+payload['body']['text'])[:1024]
            self.v.emit_reply(sender,payload,dedupe=mid+':reply')
        def go(name,**values):
            state.update(state=name,**values);save();show()
        def ref_back(note=None):
            state['state']='admin_ref';save();show(note)
        if action=='main.admin':
            self.flow.discard(state)
            state={'state':'admin_home' if self.v.admin_authenticated(sender) else 'admin_login'};save();show();return
        if state['state']=='admin_login':
            if message.get('type')!='text' or action:
                show('Enter the admin password as a text message, or tap Cancel.');return
            if self.db.limited('wa-admin-login:'+sender,5,300):
                event(log,'admin.login.rate_limited',level=logging.WARNING,actor=actor_key(sender))
                show('Too many sign-in attempts. Try again in five minutes.');return
            # Direct local calls support text; webhook intake always uses the
            # server-generated message-bound proof and removes password text.
            proof=message.get('_admin_password_proof')
            if proof is None: proof=self.v.password_proof(sender,mid,str(message.get('text',{}).get('body','')))
            expected=self.v.password_proof(sender,mid,self.v.s.admin_password)
            if not self.v.s.admin_password or not hmac.compare_digest(str(proof),expected):
                event(log,'admin.login.rejected',level=logging.WARNING,actor=actor_key(sender));show('Incorrect administrator password. Please try again.');return
            self.db.execute('INSERT OR REPLACE INTO whatsapp_admin VALUES(?,?)',(sender,time.time()+self.v.s.session_minutes*60))
            state={'state':'admin_home'};save();event(log,'admin.login.succeeded',actor=actor_key(sender));show('Administrator access granted.');return
        if not self.v.admin_authenticated(sender,touch=True):
            self.flow.discard(state);state={'state':'admin_login'};save()
            event(log,'admin.session.expired',actor=actor_key(sender));show('Admin access expired. Sign in again.');return
        if action=='admin.logout':
            self.v.admin_logout(sender);self.flow.discard(state);state={'state':'main'};save()
            event(log,'admin.logout',actor=actor_key(sender))
            self.v.emit_reply(sender,self.flow.main(sender),dedupe=mid+':reply');return
        try:
            if action=='admin.home':
                self.flow.discard(state);state={'state':'admin_home'};save();show();return
            if action in ('admin.refs','admin.jobs','admin.activity','admin.cost','admin.system','admin.help','menu.help','admin.add'):
                targets={'admin.refs':'admin_refs','admin.jobs':'admin_job_filters','admin.activity':'admin_activity','admin.cost':'admin_cost','admin.system':'admin_system','admin.help':'admin_help','menu.help':'admin_help','admin.add':'admin_subject'}
                self.flow.discard(state);state.pop('pending_id',None);go(targets[action],page=0);return
            if action in ('admin.previous','admin.next'):
                go(state['state'],page=state.get('page',0)+(-1 if action=='admin.previous' else 1));return
            if action and action.startswith('admin.ref.') and state['state']=='admin_refs':
                idx=int(action.rsplit('.',1)[1]);rid=state['ref_ids'][idx]
                go('admin_ref',admin_ref_id=rid);return
            if action=='admin.ref.download':
                with self.v.lock:
                    ref=self.v.admin_reference(state['admin_ref_id'])
                    if not ref['available']: raise UserError('The reference file is missing. Replace it first.')
                    data=(records.REFERENCES/(ref['id']+'.docx')).read_bytes()
                    self.v.admin_document(sender,data,f'Reference_{ref["subject"]}_{ref["grade"]}.docx','Reference paper — '+ref['subject']+' '+ref['grade'],'admin-reference:'+mid)
                show('Sending the stored reference paper.');return
            if action=='admin.ref.replace':
                ref=self.v.admin_reference(state['admin_ref_id']);state.update(admin_subject=ref['subject'],admin_grade=ref['grade'])
                self.prepare_upload(state);save();show();return
            if action=='admin.ref.toggle':
                ref=self.v.admin_reference(state['admin_ref_id'])
                if not ref['available'] and not ref['configured_enabled']: raise UserError('Upload the missing reference before enabling this subject.')
                go('admin_toggle',toggle_enabled=not ref['configured_enabled'],mutation_version=ref['version']);return
            if action=='admin.toggle.yes' and state['state']=='admin_toggle':
                with self.v.lock:
                    ref=self.v.admin_reference(state['admin_ref_id'])
                    if ref['version']!=state['mutation_version']: raise UserError('The reference changed. Review it before enabling/disabling.')
                    self.v.enable_reference(ref['id'],state['toggle_enabled'])
                ref_back('Subject availability updated for web and WhatsApp teachers.');return
            if action=='admin.ref.delete':
                ref=self.v.admin_reference(state['admin_ref_id']);go('admin_delete',mutation_version=ref['version']);return
            if action=='admin.delete.no': ref_back();return
            if action=='admin.delete.yes' and state['state']=='admin_delete':
                self.v.delete_reference(state['admin_ref_id'],state['mutation_version']);go('admin_refs',page=0);return
            if action and action.startswith('admin.subject.') and state['state']=='admin_subject':
                value=state['subject_options'][int(action.rsplit('.',1)[1])]
                if value is None: go('admin_newsubject')
                else: go('admin_grade',admin_subject=value,page=0)
                return
            if state['state']=='admin_newsubject' and message.get('type')=='text':
                value=str(message.get('text',{}).get('body','')).strip()
                if not value or len(value)>80 or any(ord(c)<32 for c in value): raise UserError('Enter a subject name of 1–80 characters on one line.')
                go('admin_grade',admin_subject=value,page=0);return
            if action=='admin.grade.back': go('admin_grade',page=0);return
            if action and action.startswith('admin.grade.') and state['state']=='admin_grade':
                value=state['grade_options'][int(action.rsplit('.',1)[1])]
                if value is None: go('admin_newgrade');return
                state['admin_grade']=value;self.prepare_upload(state);save();show();return
            if state['state']=='admin_newgrade' and message.get('type')=='text':
                value=str(message.get('text',{}).get('body','')).strip()
                if not value or len(value)>20 or any(ord(c)<32 for c in value): raise UserError('Enter a class/grade of 1–20 characters on one line.')
                state['admin_grade']=value;self.prepare_upload(state);save();show();return
            if action=='admin.upload.cancel':
                self.flow.discard(state);state.pop('pending_id',None);go('admin_refs' if state.get('upload_existing_id') else 'admin_home',page=0);return
            if state['state'] in ('admin_upload','admin_confirm') and message.get('type')=='document':
                doc=message.get('document',{});name=filename(doc.get('filename',''))
                if not name.lower().endswith('.docx'): raise UserError('Send a Word .docx reference paper. PDF and images are not supported.')
                data=self.v.meta.download(doc['id'],name);validate_docx(data,name,self.v.s.max_bytes)
                self.flow.discard(state);pid=uuid.uuid4().hex;folder=self.v.s.data/'pending'/pid;folder.mkdir()
                (folder/'reference.docx').write_bytes(data)
                go('admin_confirm',pending_id=pid,filename=name);event(log,'admin.reference.upload_validated',bytes=len(data));return
            if action=='admin.upload.toggle' and state['state']=='admin_confirm':
                state['upload_enabled']=not state['upload_enabled'];save();show();return
            if action=='admin.upload.replace' and state['state']=='admin_confirm':
                self.flow.discard(state);state.pop('pending_id',None);go('admin_upload');return
            if action=='admin.upload.save' and state['state']=='admin_confirm':
                path=self.v.s.data/'pending'/state['pending_id']/'reference.docx'
                if not path.is_file(): raise UserError('The pending upload expired. Choose another file.')
                ref=self.v.save_reference(state['admin_subject'],state['admin_grade'],path.read_bytes(),state['upload_enabled'],state['upload_expected_version'])
                self.flow.discard(state);state.pop('pending_id',None);state.update(state='admin_ref',admin_ref_id=ref['id']);save()
                show('Reference saved successfully.');return
            if action and action.startswith('admin.jobs.'):
                go('admin_jobs',job_filter=action.rsplit('.',1)[1],page=0);return
            if action=='admin.job.back': go('admin_jobs');return
            if action and action.startswith('admin.job.') and state['state']=='admin_jobs':
                jid=state['admin_job_ids'][int(action.rsplit('.',1)[1])];go('admin_job',admin_job_id=jid);return
            if action=='admin.job.refresh': go('admin_job');return
            if action=='admin.job.diagnostics': go('admin_diagnostics');return
            if action=='admin.job.download':
                row=self.selected_job(state)
                if not row['output'] or not Path(row['output']).is_file(): raise UserError('No saved output remains for this request.')
                self.v.admin_document(sender,Path(row['output']).read_bytes(),Path(row['output']).name,'Saved result — '+row['id'],'admin-result:'+mid)
                show('Sending a copy of the saved result to you.');return
            if action in ('admin.job.cancel','admin.job.retry','admin.job.delivery','admin.job.resolve'):
                row=self.selected_job(state);op=action.rsplit('.',1)[1]
                if op=='resolve': op='resume' if json.loads(row['remote'] or '{}').get('response_id') else 'release'
                go('admin_job_confirm',job_operation=op);return
            if action=='admin.job.confirm' and state['state']=='admin_job_confirm':
                row=self.selected_job(state);op=state['job_operation'];note=''
                if op=='cancel': note='Request cancelled.' if self.v.cancel(row['id'],row['owner']) else 'Processing already started; cancellation was not possible.'
                elif op=='retry':
                    new=self.v.retry(row['id'],row['owner'],'admin-retry:'+mid);state['admin_job_id']=new['id'];note='New request queued for the original teacher.'
                elif op=='delivery': note='Saved result delivery queued for the original teacher.' if self.v.retry_delivery(row['id'],row['owner']) else 'No failed delivery is waiting.'
                else: note=self.v.reconcile(row['id'],op=='release')
                state['state']='admin_job';save();event(log,'admin.job.action',operation=op,job_id=row['id'],actor=actor_key(sender));show(note);return
            show('Tap an option below. Only password, new subject/class names, and file attachments need input.')
        except UserError as exc:
            event(log,'admin.action.rejected',level=logging.WARNING,actor=actor_key(sender),**error_fields(exc));show(str(exc))
        except (IndexError,ValueError,KeyError):
            state={'state':'admin_home'};save();show('The selection is no longer available. Choose from the latest menu.')
        except Exception as exc:
            event(log,'admin.action.failed',level=logging.ERROR,actor=actor_key(sender),**error_fields(exc))
            state['state']='admin_home';save();show('The admin action could not complete. Refresh the list before trying again.')
