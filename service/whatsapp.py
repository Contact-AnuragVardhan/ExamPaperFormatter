"""Native WhatsApp list/button navigation, with Hi as a global reset."""
import logging
import shutil
import uuid
from runtime_logging import event,error_fields
from .files import UserError,filename,validate_docx
from .menus import menu

INTRO=('👋 Welcome to *Exam Formatter*!\n\n'
       'Turn your Word exam into a consistent, classroom-ready paper using your school’s subject and class format. '
       'Choose your subject, attach your .docx exam, and we’ll return a formatted Word file while preserving your questions and academic content. '
       'You can also check an exam and receive a report of changes needed. Please review every result before use.\n\n'
       'Tap Open Menu to choose what you would like to do.')
HELP=('Choose Format Exam or Check Exam from the main menu.\n'
      'Select your subject and class, then attach your exam as a Word .docx file.\n'
      'Review the confirmation and tap Start Formatting or Start Checking.\n'
      'We will send your result here. Review the Word file or issues report before use.')
log=logging.getLogger('exam.whatsapp')
GREETINGS=('hi','hello','hey','menu','start')

class WhatsAppFlow:
    def __init__(self,service): self.v=service;self.db=service.db
    def main(self,sender):
        choices=[('main.format','Format Exam'),('main.check','Check Exam'),('main.admin','Admin'),('menu.help','Help')]
        if self.db.one("SELECT id FROM jobs WHERE owner=? AND output IS NOT NULL AND delivery='failed' ORDER BY created DESC LIMIT 1",('wa:'+sender,)):
            choices.append(('delivery.retry','Retry Delivery'))
        choices.append(('main.exit','Exit'))
        active=self.v.active('wa:'+sender)
        note=''
        if active:
            note='\n\nYour earlier request needs an administrator to resolve its status.' if active['state']=='uncertain' else '\n\nYour earlier exam request is still active. You may use Help while waiting.'
        return menu('Exam Formatter — Main Menu',INTRO+note,choices,force_list=True)
    def subject_menu(self,state):
        refs=[r for r in self.v.references() if r['enabled']]
        page=min(max(0,int(state.get('subject_page',0))),max(0,(len(refs)-1)//7))
        state['subject_page']=page
        visible=refs[page*7:(page+1)*7]
        state['choices']=[r['id'] for r in visible]
        choices=[('subject.'+r['id'],f'{r["subject"].title()} — {r["grade"]}',f'{r["subject"].title()} | Class {r["grade"]}') for r in visible]
        if page: choices.append(('subjects.previous','Previous Subjects'))
        if (page+1)*7<len(refs): choices.append(('subjects.next','More Subjects'))
        choices.append(('menu.main','Back to Main Menu'))
        body='Tap a subject and class from the list.' if refs else 'No subject formats are currently available. Please contact the administrator.'
        return menu('Select Subject / Class',body,choices,button='Choose Subject',force_list=bool(refs))
    def upload_menu(self):
        return menu('Send Your Exam',f'Attach your Word (.docx) exam, up to {self.v.s.max_upload_mb} MB. Use the attachment icon to send the file.',
                    [('upload.cancel','Cancel'),('menu.help','Help')])
    def confirm_menu(self,state):
        ref,_=self.v.resolve(state['reference_id'])
        start='Start Checking' if state.get('kind')=='check' else 'Start Formatting'
        return menu('Ready to '+state.get('kind','format').title(),
                    f'Subject: {ref["subject"].title()} — Class {ref["grade"]}\nFile: {state["filename"]}\n\nReview these details and choose an action.',
                    [('confirm.start',start),('confirm.subject','Change Subject'),('confirm.replace','Replace File'),('confirm.cancel','Cancel')],force_list=True)
    def processing_menu(self,row=None):
        body='Your exam request is being processed. We will send the result when ready.'
        if row and row['state']=='uncertain': body='The request status is unresolved. Please contact the administrator before trying another exam.'
        return menu('Exam Request Status',body,[('processing.status','Check Status'),('processing.cancel','Cancel Request'),('menu.main','Main Menu')])
    def help_menu(self):
        return menu('How to Use Exam Formatter',HELP,[('help.back','Back'),('menu.main','Main Menu')])
    @staticmethod
    def document_caption(row):
        """Describe the actual attachment, independent of the completed job state.

        The formatter may finish with FORMATTING while producing an issues report,
        not a formatted exam. WhatsApp captions are limited to 1024 characters.
        """
        status = row.get('result_status')
        message = row.get('message') or 'Your exam request has finished.'
        if status in ('FORMATTING', 'STRUCTURAL'):
            caption = 'Issues report (not a formatted exam).\n' + message
            if len(caption) > 1024:
                return ('Issues report (not a formatted exam). '
                        'Review the attached Word report for the issues to address.')
            return caption
        return message[:1024]

    def result_menu(self,row):
        if row and row['state'] in ('completed','needs_changes'):
            title = {
                'ALL GOOD': 'ALL GOOD',
                'FORMATTING': 'FORMATTING — You Can Proceed',
                'STRUCTURAL': 'STRUCTURAL — Fix Required',
            }.get(row.get('result_status'), 'Next Step')
            return menu(title,row['message'] or 'Your exam request has finished.',
                        [('main.format','Format Another Exam'),('main.check','Check Another Exam'),('menu.help','Help'),('menu.main','Main Menu')],force_list=True)
        choices=[]
        if row and row['retryable'] and row['state']=='failed': choices.append(('error.retry','Try Again'))
        choices.extend([('error.subject','Choose Another Subject'),('error.upload','Upload Another File'),('menu.help','Help'),('menu.main','Main Menu')])
        return menu('Exam Request Needs Attention',row['message'] if row else 'This request is no longer available. Please start again.',choices,force_list=True)
    def current_menu(self,sender,state=None):
        state=state or self.db.session(sender,self.v.s.session_minutes)
        name=state['state']
        if name.startswith('admin_'):
            from .whatsapp_admin import WhatsAppAdmin
            return WhatsAppAdmin(self).current_menu(sender,state)
        if name=='subject':
            result=self.subject_menu(state);self.db.save_session(sender,state);return result
        if name=='upload': return self.upload_menu()
        if name=='confirm':
            try: return self.confirm_menu(state)
            except UserError:
                state={'state':'subject','kind':state.get('kind','format')}
                result=self.subject_menu(state);self.db.save_session(sender,state);return result
        if name=='help': return self.help_menu()
        if name=='processing': return self.processing_menu(self.v.get(state.get('job_id','')))
        if name in ('result','error'): return self.result_menu(self.v.get(state.get('job_id','')))
        return self.main(sender)
    def discard(self,state):
        if state.get('pending_id'): shutil.rmtree(self.v.s.data/'pending'/state['pending_id'],ignore_errors=True)
    def handle(self,sender,message,message_id):
        before=self.db.session(sender,self.v.s.session_minutes)
        reply=message.get('interactive',{})
        selection=(reply.get('button_reply',{}) or reply.get('list_reply',{})).get('id','')
        command=before.get('menu_actions',{}).get(selection,'other')
        text=str(message.get('text',{}).get('body','')).strip().casefold().strip(' !.,?')
        if text in GREETINGS or text=='help': command=text
        kind=message.get('type')
        if kind not in ('text','interactive','document','image','audio','video','sticker','location','contacts'): kind='other'
        event(log,'whatsapp.menu.input',message_id=message_id,menu=before['state'],command=command,message_type=kind)
        try: return self._handle(sender,message,message_id)
        finally:
            after=self.db.session(sender,self.v.s.session_minutes)
            event(log,'whatsapp.menu.state',message_id=message_id,previous=before['state'],current=after['state'],
                  job_id=after.get('job_id'),reference_id=after.get('reference_id'),has_pending_file=bool(after.get('pending_id')))
    def _handle(self,sender,message,message_id):
        state=self.db.session(sender,self.v.s.session_minutes)
        text=str(message.get('text',{}).get('body','')).strip().casefold().strip(' !.,?')
        def say(content): self.v.emit_reply(sender,content,dedupe=f'{message_id}:reply')
        def save(): self.db.save_session(sender,state)
        def show(note=None):
            content=self.current_menu(sender,state)
            if note: content['body']['text']=(note+'\n\n'+content['body']['text'])[:1024]
            say(content)
        if text in GREETINGS:
            self.v.admin_logout(sender)
            self.discard(state);state={'state':'main'};save();say(self.main(sender));return
        action='menu.help' if text=='help' else None
        if message.get('type')=='interactive':
            reply=message.get('interactive',{})
            selected=(reply.get('button_reply',{}) or reply.get('list_reply',{})).get('id','')
            action=state.get('menu_actions',{}).get(selected)
            if not action:
                event(log,'whatsapp.menu.stale_selection',level=logging.WARNING)
                show('Please use the latest menu below.');return
        if action=='menu.main':
            self.v.admin_logout(sender)
            self.discard(state);state={'state':'main'};save();say(self.main(sender));return
        if action=='main.admin' or state['state'].startswith('admin_'):
            from .whatsapp_admin import WhatsAppAdmin
            return WhatsAppAdmin(self).handle(sender,message,message_id,state,action)
        if action=='menu.help':
            if state['state']!='help': state['previous']=state['state']
            state['state']='help';save();say(self.help_menu());return
        if action=='help.back' and state['state']=='help':
            state['state']=state.pop('previous','main');save();show();return
        if action=='main.exit' and state['state']=='main':
            state={'state':'main'};save();say(menu('See You Soon','Goodbye! Say Hi whenever you want to use Exam Formatter again.',[('menu.main','Start Again')]));return
        if action=='delivery.retry':
            row=self.db.one("SELECT * FROM jobs WHERE owner=? AND output IS NOT NULL AND delivery='failed' ORDER BY created DESC LIMIT 1",('wa:'+sender,))
            ok=bool(row and self.v.retry_delivery(row['id'],'wa:'+sender))
            show('Retrying delivery of your saved result. Your exam will not be formatted again.' if ok else 'No failed delivery is waiting.');return
        if action in ('main.format','main.check'):
            active=self.v.active('wa:'+sender)
            if active:
                state={'state':'processing','job_id':active['id']};save();say(self.processing_menu(active));return
            self.discard(state)
            state={'state':'subject','kind':'format' if action=='main.format' else 'check'}
            content=self.subject_menu(state);save();say(content);return
        if state['state']=='subject':
            if action in ('subjects.previous','subjects.next'):
                state['subject_page']=state.get('subject_page',0)+(-1 if action=='subjects.previous' else 1)
                content=self.subject_menu(state);save();say(content);return
            if action and action.startswith('subject.'):
                rid=action[len('subject.'):]
                if rid not in state.get('choices',[]): show('Please choose a subject from the latest menu.');return
                state['reference_id']=rid
                try:
                    ref,_=self.v.resolve(rid);state['reference_version']=ref['version']
                    pending=state.get('pending_id')
                    state['state']='confirm' if pending and (self.v.s.data/'pending'/pending/'teacher.docx').is_file() else 'upload'
                except UserError as exc:
                    event(log,'whatsapp.subject.unavailable',level=logging.WARNING,reference_id=rid)
                    state['state']='subject';save();show(str(exc));return
                save();show();return
            show('Tap Choose Subject to select your subject and class.');return
        if state['state']=='upload' or (state['state']=='confirm' and message.get('type')=='document'):
            if action=='upload.cancel':
                self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            if message.get('type')!='document': show('Please attach a Word .docx file or tap a button below.');return
            document=message.get('document',{});name=filename(document.get('filename',''))
            try:
                if not name.lower().endswith('.docx'): raise UserError('Please send a Word .docx file. PDF and images are not supported.')
                data=self.v.meta.download(document['id'],name);validate_docx(data,name,self.v.s.max_bytes)
                event(log,'whatsapp.upload.validated',bytes=len(data),reference_id=state.get('reference_id'),replacing=bool(state.get('pending_id')))
                self.discard(state)
                pid=uuid.uuid4().hex;folder=self.v.s.data/'pending'/pid;folder.mkdir()
                (folder/'teacher.docx').write_bytes(data)
                state.update(pending_id=pid,filename=name,state='confirm');save();show()
            except UserError as exc:
                event(log,'whatsapp.upload.rejected',level=logging.WARNING,reason='document_validation',**error_fields(exc));show(str(exc))
            except Exception as exc:
                event(log,'whatsapp.upload.failed',level=logging.ERROR,**error_fields(exc));show('We could not download that file. Please attach the Word document again.')
            return
        if state['state']=='confirm':
            if action=='confirm.cancel':
                self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            if action=='confirm.subject':
                state['state']='subject';state['subject_page']=0;content=self.subject_menu(state);save();say(content);return
            if action=='confirm.replace':
                self.discard(state);state.pop('pending_id',None);state['state']='upload';save();show();return
            if action!='confirm.start': show('Tap Open Menu and choose an action.');return
            try:
                ref,_=self.v.resolve(state['reference_id'])
                if ref['version']!=state.get('reference_version'): raise UserError('The subject format changed. Please choose the subject again.')
                path=self.v.s.data/'pending'/state['pending_id']/'teacher.docx'
                if not path.is_file(): raise UserError('Your upload has expired. Please upload the file again.')
                row=self.v.submit('wa:'+sender,'whatsapp',state.get('kind','format'),state['reference_id'],state['filename'],path.read_bytes(),'wa:'+message_id)
                self.discard(state);state={'state':'processing','job_id':row['id']};save();say(self.processing_menu(row))
            except UserError as exc:
                event(log,'whatsapp.confirmation.rejected',level=logging.WARNING,reference_id=state.get('reference_id'),**error_fields(exc));show(str(exc))
            return
        if state['state']=='processing':
            row=self.v.get(state.get('job_id',''))
            if action=='processing.cancel':
                if self.v.cancel(state['job_id'],'wa:'+sender):
                    state={'state':'main'};save();content=self.main(sender);content['body']['text']='Your queued request was cancelled.\n\n'+content['body']['text'];say(content)
                else: show('Processing has already started, so it cannot be cancelled here.')
            elif row and row['state'] in ('completed','needs_changes','failed','uncertain'):
                self.v.set_finished_session(sender,row);state=self.db.session(sender,self.v.s.session_minutes);show()
            else: show()
            return
        if state['state'] in ('result','error'):
            row=self.v.get(state.get('job_id',''))
            if action=='error.subject':
                state={'state':'subject','kind':row['kind'] if row else 'format'};content=self.subject_menu(state);save();say(content);return
            if action=='error.upload' and row:
                state={'state':'upload','kind':row['kind'],'reference_id':row['reference_id'],'reference_version':row['version']};save();show();return
            if action=='error.retry' and row:
                try:
                    retried=self.v.retry(row['id'],'wa:'+sender,'retry:'+message_id)
                    state={'state':'processing','job_id':retried['id']};save();say(self.processing_menu(retried))
                except UserError as exc: show(str(exc))
                return
        show('Tap an option in the menu below; there is no need to type a number.')
