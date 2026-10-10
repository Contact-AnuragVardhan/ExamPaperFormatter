"""Console events with correlation IDs and bounded, redacted metadata."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import logging
import os
import re
import sys

_context = ContextVar('exam_log_context', default={})
_secrets = set()
SECRET_NAMES = ('OPENAI_API_KEY','WHATSAPP_TOKEN','WHATSAPP_ACCESS_TOKEN',
    'WHATSAPP_APP_SECRET','VERIFY_TOKEN','WHATSAPP_VERIFY_TOKEN',
    'ADMIN_PASSWORD','WEB_TEACHER_ACCESS_CODE')


def register_secrets(*values):
    _secrets.update(str(v) for v in values if v)


def safe_value(value):
    if value is None or isinstance(value,(int,float,bool)): return value
    text = str(value)
    secrets = _secrets | {os.getenv(name,'').strip() for name in SECRET_NAMES}
    for secret in sorted(secrets,key=len,reverse=True):
        if secret: text = text.replace(secret,'[redacted]')
    text = re.sub(r'sk-[A-Za-z0-9_-]+','[redacted]',text)
    text = re.sub(r'(?i)Bearer\s+[^\s\"\'<>]+','Bearer [redacted]',text)
    return text[:250]


def actor_key(value):
    """Stable correlation without printing a teacher's phone/session identifier."""
    return hashlib.sha256(str(value).encode()).hexdigest()[:12]


@contextmanager
def log_context(**fields):
    token = _context.set(_context.get() | fields)
    try: yield
    finally: _context.reset(token)


def event(logger,name,level=logging.INFO,**fields):
    if not logger.isEnabledFor(level): return
    values = {k:safe_value(v) for k,v in (_context.get() | fields).items()}
    logger.log(level,'event=%s %s',name,json.dumps(values,ensure_ascii=True,separators=(',',':')))


def error_fields(exc):
    """API codes explain failures without logging provider bodies or exam text."""
    fields = {'error_type':type(exc).__name__,'http_status':getattr(exc,'status_code',None),
              'provider_request_id':getattr(exc,'request_id',None)}
    body = getattr(exc,'body',None)
    if isinstance(body,dict):
        error = body.get('error',body)
        if isinstance(error,dict):
            for key in ('code','param','type','error_subcode'):
                value = error.get(key)
                if isinstance(value,int) or (isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',value)):
                    fields['provider_'+key] = value
    return fields


class AccessLogFilter(logging.Filter):
    def filter(self,record):
        # Uvicorn otherwise includes hub.verify_token in the GET query string.
        if isinstance(record.args,tuple) and len(record.args)==5:
            values = list(record.args)
            values[2] = str(values[2]).split('?',1)[0]
            record.args = tuple(values)
        return True


def configure_logging(level='INFO'):
    logger = logging.getLogger('exam')
    logger.setLevel(getattr(logging,str(level).upper(),logging.INFO))
    logger.propagate = False
    for handler in list(logger.handlers):
        if getattr(handler,'exam_console',False):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.exam_console = True
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logger.addHandler(handler)
    # Third-party DEBUG output can contain HTTP bodies, headers and media URLs.
    for name in ('openai','httpx','httpcore'):
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger('uvicorn.access')
    if not any(isinstance(f,AccessLogFilter) for f in access.filters):
        access.addFilter(AccessLogFilter())
