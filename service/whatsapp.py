"""Numbered teacher menus, with Hi as a global navigation command."""
import json
import logging
import shutil
import uuid
from runtime_logging import event,error_fields
from .files import UserError,filename,validate_docx

INTRO=('👋 Welcome to *Exam Formatter*!\n\n'
       'Turn your Word exam into a consistent, classroom-ready paper using your school’s subject and class format. '
       'Choose your subject, attach your .docx exam, and we’ll return a formatted Word file while preserving your questions and academic content. '
       'You can also check an exam and receive a report of changes needed. Please review every result before use.\n\n')
HELP=('HELP\n1. Choose Format Exam or Check Exam.\n2. Select your subject and class.\n'
      '3. Attach your exam as a Word .docx document.\n4. Confirm and wait for the result.\n'
      '5. Review the returned Word file or issues report.\n\n0. Back\nSay Hi at any time for the main menu.')
log=logging.getLogger('exam.whatsapp')

class WhatsAppFlow:
    def __init__(self,service): self.v=service;self.db=service.db
    def main(self,sender):
        extra=''
        if self.db.one("SELECT id FROM jobs WHERE owner=? AND output IS NOT NULL AND delivery='failed' ORDER BY created DESC LIMIT 1",('wa:'+sender,)):
            extra='3. Retry Result Delivery\n'
        active=self.v.active('wa:'+sender)
        note='\nYour earlier exam request is still active. We will send its result when ready. You can use Help while you wait.' if active else ''
        return INTRO+'*MAIN MENU*\n1. Format Exam\n2. Help\n'+extra+'4. Check Exam\n0. Exit\n\nReply with a number. Say Hi anytime to return here.'+note
    def subject_menu(self,state):
        refs=[r for r in self.v.references() if r['enabled']]
        state['choices']=[r['id'] for r in refs]
        return 'SELECT SUBJECT / CLASS\n'+'\n'.join(f'{i}. {r["subject"].title()} — Class {r["grade"]}' for i,r in enumerate(refs,1))+'\n0. Back'
    def upload_menu(self): return f'SEND YOUR EXAM\nPlease attach your Word (.docx) exam (up to {self.v.s.max_upload_mb} MB).\n0. Cancel'
    def confirm_menu(self,state):
        ref,_=self.v.resolve(state['reference_id'])
        return f'READY TO {state.get("kind","format").upper()}\nSubject: {ref["subject"].title()} — Class {ref["grade"]}\nFile: {state["filename"]}\n1. Start {"Checking" if state.get("kind")=="check" else "Formatting"}\n2. Change Subject\n3. Replace File\n0. Cancel'
    def discard(self,state):
        if state.get('pending_id'): shutil.rmtree(self.v.s.data/'pending'/state['pending_id'],ignore_errors=True)
    def handle(self,sender,message,message_id):
        before=self.db.session(sender,self.v.s.session_minutes)
        body=str(message.get('text',{}).get('body','')).strip()
        if message.get('type')=='interactive':
            body=message.get('interactive',{}).get('button_reply',{}).get('id','') or message.get('interactive',{}).get('list_reply',{}).get('id','')
        command=body.casefold().strip(' !.,?')
        if command not in ('hi','hello','hey','menu','start','help','0','1','2','3','4','5','6','7','8','9'): command='other'
        kind=message.get('type')
        if kind not in ('text','interactive','document','image','audio','video','sticker','location','contacts'): kind='other'
        event(log,'whatsapp.menu.input',message_id=message_id,menu=before['state'],command=command,message_type=kind)
        try:
            return self._handle(sender,message,message_id)
        finally:
            after=self.db.session(sender,self.v.s.session_minutes)
            event(log,'whatsapp.menu.state',message_id=message_id,previous=before['state'],current=after['state'],
                  job_id=after.get('job_id'),reference_id=after.get('reference_id'),has_pending_file=bool(after.get('pending_id')))
    def _handle(self,sender,message,message_id):
        state=self.db.session(sender,self.v.s.session_minutes)
        body=str(message.get('text',{}).get('body','')).strip()
        if message.get('type')=='interactive':
            body=message.get('interactive',{}).get('button_reply',{}).get('id','') or message.get('interactive',{}).get('list_reply',{}).get('id','')
        def say(text): self.db.emit(sender,text,dedupe=f'{message_id}:reply')
        def save(): self.db.save_session(sender,state)
        # Global command before file/state handling; Hi is always a fresh main menu.
        if body.casefold().strip(' !.,?') in ('hi','hello','hey','menu','start'):
            self.discard(state);state={'state':'main'};save();say(self.main(sender));return
        if body.casefold()=='help':
            state['previous']=state.get('state','main');state['state']='help';save();say(HELP);return
        if state['state']=='help':
            if body=='0':
                state['state']=state.pop('previous','main')
                if state['state']=='main': text=self.main(sender)
                elif state['state']=='subject': text=self.subject_menu(state)
                elif state['state']=='upload': text=self.upload_menu()
                elif state['state']=='confirm':
                    try: text=self.confirm_menu(state)
                    except UserError: state={'state':'main'};text=self.main(sender)
                elif state['state']=='processing': text='Your exam request is still being processed. Say Hi for the main menu.'
                else: state={'state':'main'};text=self.main(sender)
                save();say(text)
            else: say(HELP)
            return
        if state['state']=='main':
            if body=='0': state={'state':'main'};save();say('Goodbye! Say Hi whenever you want to use Exam Formatter again.');return
            if body=='2': state={'state':'help','previous':'main'};save();say(HELP);return
            if body=='3':
                row=self.db.one("SELECT * FROM jobs WHERE owner=? AND output IS NOT NULL AND delivery='failed' ORDER BY created DESC LIMIT 1",('wa:'+sender,))
                if row and self.v.retry_delivery(row['id'],'wa:'+sender): say('Retrying delivery of your saved result. Your exam will not be formatted again.')
                else: say('No failed delivery is waiting.\n\n'+self.main(sender))
                return
            if body in ('1','4'):
                if self.v.active('wa:'+sender): say('Your earlier exam request is still active. Please wait for its result. Say Hi for the main menu.');return
                state={'state':'subject','kind':'format' if body=='1' else 'check'}
                text=self.subject_menu(state);save();say(text);return
            say(self.main(sender));return
        if state['state']=='subject':
            if body=='0': self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            choices=state.get('choices',[])
            if not body.isdecimal() or not 1<=int(body)<=len(choices): say('Please select a subject from this menu.\n'+self.subject_menu(state));save();return
            state['reference_id']=choices[int(body)-1]
            try:
                ref,_=self.v.resolve(state['reference_id']);state['reference_version']=ref['version']
                pending=state.get('pending_id')
                if pending and (self.v.s.data/'pending'/pending/'teacher.docx').is_file(): state['state']='confirm';text=self.confirm_menu(state)
                else: state['state']='upload';text=self.upload_menu()
            except UserError as e: state['state']='subject';text=str(e)+'\n'+self.subject_menu(state)
            save();say(text);return
        if state['state']=='upload' or (state['state']=='confirm' and message.get('type')=='document'):
            if body=='0': self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            if message.get('type')!='document': say(self.upload_menu());return
            document=message.get('document',{});name=filename(document.get('filename',''))
            try:
                if not name.lower().endswith('.docx'): raise UserError('Please send a Word .docx file. PDF and images are not supported.')
                data=self.v.meta.download(document['id'],name);validate_docx(data,name,self.v.s.max_bytes)
                event(log,'whatsapp.upload.validated',bytes=len(data),reference_id=state.get('reference_id'),replacing=bool(state.get('pending_id')))
                self.discard(state)
                pid=uuid.uuid4().hex;folder=self.v.s.data/'pending'/pid;folder.mkdir()
                (folder/'teacher.docx').write_bytes(data)
                state.update(pending_id=pid,filename=name,state='confirm')
                text=self.confirm_menu(state);save();say(text)
            except UserError as e:
                event(log,'whatsapp.upload.rejected',level=logging.WARNING,reason='document_validation',**error_fields(e))
                say(str(e)+'\n'+self.upload_menu())
            except Exception as exc:
                event(log,'whatsapp.upload.failed',level=logging.ERROR,**error_fields(exc))
                say('We could not download that file. Please attach the Word document again.')
            return
        if state['state']=='confirm':
            if body=='0': self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            if body=='2': state['state']='subject';text=self.subject_menu(state);save();say(text);return
            if body=='3': self.discard(state);state.pop('pending_id',None);state['state']='upload';save();say(self.upload_menu());return
            if body!='1': say(self.confirm_menu(state));return
            try:
                ref,_=self.v.resolve(state['reference_id'])
                if ref['version']!=state.get('reference_version'): raise UserError('The subject format changed. Please choose the subject again.')
                path=self.v.s.data/'pending'/state['pending_id']/'teacher.docx'
                if not path.is_file(): raise UserError('Your upload has expired. Please upload the file again.')
                row=self.v.submit('wa:'+sender,'whatsapp',state.get('kind','format'),state['reference_id'],state['filename'],path.read_bytes(),'wa:'+message_id)
                self.discard(state)
                state={'state':'processing','job_id':row['id']};save()
                say('CHECKING IN PROGRESS\nYour exam is being checked. We will send the result when ready.\n0. Cancel if it has not started\nSay Hi for the main menu.' if row['kind']=='check' else 'FORMATTING IN PROGRESS\nYour exam is being formatted. Please wait; we will send the Word file when ready.\n0. Cancel if it has not started\nSay Hi for the main menu.')
            except UserError as e:
                event(log,'whatsapp.confirmation.rejected',level=logging.WARNING,reference_id=state.get('reference_id'),**error_fields(e))
                say(str(e)+'\nSay Hi to choose your subject again.')
            return
        if state['state']=='processing':
            row=self.v.get(state['job_id'])
            if body=='0':
                if self.v.cancel(state['job_id'],'wa:'+sender): state={'state':'main'};save();say('Your queued request was cancelled.\n\n'+self.main(sender))
                else: say('Processing has already started, so it cannot be cancelled here. We will send the result when ready. Say Hi for the main menu.')
            elif row and row['state'] in ('completed','needs_changes','failed','uncertain'):
                self.v.set_finished_session(sender,row);say(row['message']+'\n'+self.v.result_menu(row))
            else: say('Your exam request is still active. Please wait. Say Hi for the main menu.')
            return
        if state['state'] in ('result','error'):
            row=self.v.get(state.get('job_id',''))
            if body=='0': self.discard(state);state={'state':'main'};save();say(self.main(sender));return
            if state['state']=='result' and body=='2': state['previous']='result';state['state']='help';save();say(HELP);return
            if (state['state']=='result' and body=='1') or (state['state']=='error' and body=='2'):
                state={'state':'subject','kind':row['kind'] if row else 'format'};text=self.subject_menu(state);save();say(text);return
            if state['state']=='error' and body=='3' and row:
                state={'state':'upload','kind':row['kind'],'reference_id':row['reference_id'],'reference_version':row['version']};save();say(self.upload_menu());return
            if state['state']=='error' and body=='1' and row:
                try:
                    retried=self.v.retry(row['id'],'wa:'+sender,'retry:'+message_id)
                    state={'state':'processing','job_id':retried['id']};save();say('Your retry is queued. Please wait; we will send the result when ready.')
                except UserError as e: say(str(e)+'\n0. Main Menu')
                return
            say(self.v.result_menu(row) if row else self.main(sender));return
        state={'state':'main'};save();say(self.main(sender))
