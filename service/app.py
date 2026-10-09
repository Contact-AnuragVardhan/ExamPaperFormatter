import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI,Request,HTTPException,UploadFile,File,Form
from fastapi.responses import FileResponse,JSONResponse,PlainTextResponse
from fastapi.staticfiles import StaticFiles
import records
from .config import Settings
from .core import Service
from .files import UserError,validate_docx
from .limits import BodyLimit

WEB=Path(__file__).resolve().parent.parent/'web'

def create_app(settings=None,service=None,start_workers=True):
    settings=settings or Settings.load()
    service=service or Service(settings)
    @asynccontextmanager
    async def lifespan(app):
        if not settings.teacher_code or not settings.admin_password:
            raise RuntimeError('Set WEB_TEACHER_ACCESS_CODE and ADMIN_PASSWORD in .env before starting the service.')
        if settings.app_env=='prod' and not settings.wa_async:
            raise RuntimeError('WHATSAPP_ASYNC must be true in production so webhooks acknowledge promptly.')
        if settings.wa_http_timeout<=0:
            raise RuntimeError('WHATSAPP_HTTP_TIMEOUT must be positive.')
        if start_workers: service.start()
        yield
        if start_workers: service.close()
    app=FastAPI(title='Exam Formatter REV2',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.add_middleware(BodyLimit,limit=settings.max_bytes+1024*1024)
    app.state.service=service
    @app.exception_handler(UserError)
    async def user_error(_,e): return JSONResponse({'detail':str(e)},status_code=400)
    @app.middleware('http')
    async def guards(request,call_next):
        length=request.headers.get('content-length')
        if length and (not length.isdigit() or int(length)>settings.max_bytes+1024*1024):
            return JSONResponse({'detail':'Request exceeds the upload limit.'},status_code=413)
        # Browser mutations must originate from this application; no permissive CORS.
        if request.method in ('POST','PUT','PATCH','DELETE') and request.url.path not in ('/webhook','/webhooks/whatsapp'):
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc!=request.headers.get('host'):
                return JSONResponse({'detail':'Invalid request origin.'},status_code=403)
            if request.headers.get('x-exam-client')!='web':
                return JSONResponse({'detail':'Missing request header.'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith('/api/'): response.headers['Cache-Control']='no-store'
        return response
    def auth(request,admin=False):
        token=request.cookies.get('exam_session','')
        row=service.db.one('SELECT * FROM auth WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),time.time()))
        if not row: raise HTTPException(401,'Please sign in.')
        if admin and row['role']!='admin': raise HTTPException(403,'Administrator access required.')
        return row
    def owned(request,jid):
        who=auth(request);row=service.get(jid)
        if not row or (row['owner']!=who['owner'] and who['role']!='admin'): raise HTTPException(404,'Exam request not found.')
        return row
    async def read_upload(upload):
        data=await upload.read(settings.max_bytes+1)
        await upload.close()
        validate_docx(data,upload.filename or '',settings.max_bytes)
        return data
    @app.get('/')
    def home(): return FileResponse(WEB/'index.html')
    app.mount('/static',StaticFiles(directory=WEB),name='static')
    @app.get('/health')
    def health(): return {'status':'ok'}
    @app.post('/api/login')
    async def login(request:Request):
        ip=request.client.host if request.client else 'unknown'
        if service.db.limited('login:'+ip,15,300): raise HTTPException(429,'Too many sign-in attempts. Try again in five minutes.')
        payload=await request.json();role=payload.get('role','teacher')
        expected=settings.admin_password if role=='admin' else settings.teacher_code
        supplied=str(payload.get('password',''))
        if role not in ('teacher','admin') or not expected or not hmac.compare_digest(supplied,expected): raise HTTPException(401,'Incorrect access code or password.')
        token=secrets.token_urlsafe(32);owner='web:'+secrets.token_hex(16)
        service.db.execute('INSERT INTO auth VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),owner,role,time.time()+86400*7))
        response=JSONResponse({'role':role})
        response.set_cookie('exam_session',token,httponly=True,secure=settings.secure_cookie,samesite='strict',max_age=86400*7)
        return response
    @app.get('/api/me')
    def me(request:Request): return {'role':auth(request)['role'],'max_upload_mb':settings.max_upload_mb}
    @app.post('/api/logout')
    def logout(request:Request):
        token=request.cookies.get('exam_session','');service.db.execute('DELETE FROM auth WHERE token=?',(hashlib.sha256(token.encode()).hexdigest(),))
        response=JSONResponse({'ok':True});response.delete_cookie('exam_session');return response
    @app.get('/api/references')
    def references(request:Request): auth(request);return service.references()
    @app.get('/api/jobs')
    def jobs(request:Request):
        who=auth(request)
        return [service.public(r) for r in service.db.all('SELECT * FROM jobs WHERE owner=? ORDER BY created DESC LIMIT 100',(who['owner'],))]
    @app.post('/api/jobs')
    async def submit(request:Request,reference_id:str=Form(...),kind:str=Form(...),submission_id:str=Form(...),reference_version:str=Form(...),file:UploadFile=File(...)):
        who=auth(request);data=await read_upload(file)
        with service.lock:
            ref,_=service.resolve(reference_id)
            if ref['version']!=reference_version: raise UserError('The subject format changed. Refresh and select the subject again.')
            row=service.submit(who['owner'],'web',kind,reference_id,file.filename,data,submission_id)
        return service.public(row)
    @app.get('/api/jobs/{jid}')
    def get_job(jid:str,request:Request): return service.public(owned(request,jid))
    @app.get('/api/jobs/{jid}/download')
    def download(jid:str,request:Request):
        row=owned(request,jid)
        if not row['output'] or not Path(row['output']).is_file(): raise HTTPException(404,'No result file is available.')
        return FileResponse(row['output'],filename=Path(row['output']).name,media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Cache-Control':'no-store'})
    @app.post('/api/jobs/{jid}/cancel')
    def cancel(jid:str,request:Request):
        row=owned(request,jid)
        return {'cancelled':service.cancel(jid,row['owner'])}
    @app.post('/api/jobs/{jid}/retry')
    async def retry(jid:str,request:Request):
        row=owned(request,jid);payload=await request.json()
        return service.public(service.retry(jid,row['owner'],str(payload.get('submission_id',''))))
    @app.get('/api/admin/overview')
    def overview(request:Request):
        auth(request,True)
        today,month,total,unpriced=records.cost_totals()
        return {'jobs':[service.admin_public(r) for r in service.db.all('SELECT * FROM jobs ORDER BY created DESC LIMIT 200')],
                'activity':records.read_log(),'cost':{'today':today,'month':month,'total':total,'unpriced':unpriced,'estimated':True},
                'whatsapp_configured':settings.whatsapp_ready,'references':service.references()}
    @app.post('/api/admin/references')
    async def add_reference(request:Request,subject:str=Form(...),grade:str=Form(...),enabled:str=Form('false'),file:UploadFile=File(...)):
        auth(request,True);subject=subject.strip();grade=grade.strip()
        if not subject or len(subject)>80 or not grade or len(grade)>20: raise UserError('Enter a subject and class.')
        data=await read_upload(file)
        with service.lock:
            _,ref=records.save_reference(subject,grade,data)
            service.db.execute('INSERT OR REPLACE INTO reference_settings VALUES(?,?)',(ref.path.stem,int(enabled=='true')))
        return {'ok':True}
    @app.post('/api/admin/references/{rid}/enabled')
    async def enable_reference(rid:str,request:Request):
        auth(request,True);body=await request.json()
        if not any(r['id']==rid for r in service.references()): raise HTTPException(404,'Reference not found.')
        service.db.execute('INSERT OR REPLACE INTO reference_settings VALUES(?,?)',(rid,int(body.get('enabled') is True)))
        return {'ok':True}
    @app.get('/api/admin/references/{rid}/download')
    def download_reference(rid:str,request:Request):
        auth(request,True)
        ref=next((r for r in service.references() if r['id']==rid),None)
        if not ref or not ref['available']: raise HTTPException(404,'Reference not found.')
        return FileResponse(records.REFERENCES/(rid+'.docx'),filename=f'Reference_{records._filename_piece(ref["subject"])}_{records._filename_piece(ref["grade"])}.docx')
    @app.delete('/api/admin/references/{rid}')
    def delete_reference(rid:str,request:Request):
        auth(request,True)
        with service.lock:
            ref=next((r for r in service.references() if r['id']==rid),None)
            if not ref: raise HTTPException(404,'Reference not found.')
            records.delete_reference(ref['subject'],ref['grade'])
        return {'ok':True}
    @app.post('/api/admin/jobs/{jid}/delivery')
    def redeliver(jid:str,request:Request):
        auth(request,True);row=service.get(jid)
        if not row: raise HTTPException(404,'Request not found.')
        return {'queued':service.retry_delivery(jid,row['owner'])}
    @app.post('/api/admin/jobs/{jid}/resolve')
    async def resolve_uncertain(jid:str,request:Request):
        auth(request,True);row=service.get(jid)
        if not row or row['state']!='uncertain': raise UserError('Only unresolved requests can be reconciled.')
        remote=json.loads(row['remote'] or '{}')
        if remote.get('response_id'):
            service.update(jid,state='queued',message='Resuming the existing response.')
            return {'ok':True,'message':'Resuming the existing response without a new formatting request.'}
        body=await request.json()
        if body.get('confirmed_no_remote_request') is not True: raise UserError('Check the OpenAI dashboard and confirm no remote request remains active before releasing this request.')
        service.update(jid,state='failed',retryable=1,message='Administrator confirmed that no remote request remains active. You may retry.')
        return {'ok':True}
    @app.get('/webhooks/whatsapp',include_in_schema=False)
    @app.get('/webhook')
    def verify(request:Request):
        q=request.query_params
        if q.get('hub.mode')=='subscribe' and settings.wa_verify_token and hmac.compare_digest(q.get('hub.verify_token',''),settings.wa_verify_token):
            return PlainTextResponse(q.get('hub.challenge',''))
        raise HTTPException(403,'Webhook verification failed.')
    @app.post('/webhooks/whatsapp',include_in_schema=False)
    @app.post('/webhook')
    async def webhook(request:Request):
        if not settings.whatsapp_ready: raise HTTPException(503,'WhatsApp is not configured.')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>1024*1024: raise HTTPException(413,'Webhook too large.')
        if not service.meta.signature_valid(bytes(body),request.headers.get('x-hub-signature-256')): raise HTTPException(401,'Invalid signature.')
        try: payload=json.loads(body)
        except ValueError: raise HTTPException(400,'Invalid JSON.')
        allowed={s.strip().lstrip('+') for s in settings.wa_allowed_senders.split(',') if s.strip()}
        accepted=[]
        for entry in payload.get('entry',[]):
            for change in entry.get('changes',[]):
                value=change.get('value',{})
                if str(value.get('metadata',{}).get('phone_number_id',''))!=settings.wa_phone_id: continue
                for status in value.get('statuses',[]): service.delivery_status(status.get('id'),status.get('status'))
                for message in value.get('messages',[]):
                    sender=str(message.get('from',''));mid=str(message.get('id',''))
                    if not sender.isdigit() or not mid or (allowed and sender not in allowed): continue
                    # Dedupe before counting limits; provider webhook retries are free.
                    if service.db.one('SELECT id FROM inbox WHERE id=?',(mid,)): continue
                    if service.db.limited('wa:'+sender,60,60): continue
                    service.db.execute('INSERT OR IGNORE INTO inbox(id,sender,payload,created) VALUES(?,?,?,?)',(mid,sender,json.dumps(message),time.time()))
                    accepted.append(mid)
        # Local debug compatibility with the original WHATSAPP_ASYNC flag.
        # Paid formatter jobs always run in the separate job worker.
        if not settings.wa_async:
            for mid in accepted: service.handle_incoming(mid)
        # Production uses durable enqueue only; media/formatting do not block.
        return {'ok':True}
    return app
