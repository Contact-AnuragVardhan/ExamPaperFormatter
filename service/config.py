"""Use the existing ExamPaperFormatter environment names first.

Names from the initial REV2 package remain fallback aliases. Empty values never
mask a configured alias, and no credentials are copied from another project.
"""
from dataclasses import dataclass
from pathlib import Path
import os
import records


def env_value(name,alias=None,default=''):
    value=os.getenv(name,'').strip()
    if not value and alias: value=os.getenv(alias,'').strip()
    return value or default


@dataclass
class Settings:
    data: Path
    teacher_code: str = ''
    admin_password: str = ''
    secure_cookie: bool = False
    max_upload_mb: int = 15
    session_minutes: int = 30
    retention_days: int = 7
    wa_token: str = ''
    wa_phone_id: str = ''
    wa_app_secret: str = ''
    wa_verify_token: str = ''
    wa_version: str = 'v25.0'
    wa_allowed_senders: str = ''
    wa_http_timeout: int = 60
    wa_async: bool = True
    app_env: str = 'dev'
    log_level: str = 'INFO'

    @classmethod
    def load(cls):
        records.load_env()
        app_env=env_value('APP_ENV',default='dev').lower()
        return cls(data=Path(env_value('EXAM_DATA_ROOT','EXAM_DATA_DIR',str(records.ROOT/'data'))).resolve(),
            teacher_code=env_value('WEB_TEACHER_ACCESS_CODE'),
            admin_password=env_value('ADMIN_PASSWORD'),
            secure_cookie=env_value('COOKIE_SECURE',default='true' if app_env=='prod' else 'false').lower()=='true',
            max_upload_mb=int(env_value('MAX_UPLOAD_MB',default='15')),
            session_minutes=int(env_value('SESSION_MINUTES',default='30')),
            retention_days=int(env_value('RETENTION_DAYS',default='7')),
            wa_token=env_value('WHATSAPP_TOKEN','WHATSAPP_ACCESS_TOKEN'),
            wa_phone_id=env_value('WHATSAPP_PHONE_NUMBER_ID'),
            wa_app_secret=env_value('WHATSAPP_APP_SECRET'),
            wa_verify_token=env_value('VERIFY_TOKEN','WHATSAPP_VERIFY_TOKEN'),
            wa_version=env_value('WHATSAPP_GRAPH_VERSION','WHATSAPP_API_VERSION','v25.0'),
            wa_allowed_senders=env_value('WHATSAPP_ALLOWED_SENDERS'),
            wa_http_timeout=int(env_value('WHATSAPP_HTTP_TIMEOUT',default='60')),
            wa_async=env_value('WHATSAPP_ASYNC',default='true').lower() in ('1','true','yes','on'),
            app_env=app_env,
            log_level=env_value('LOG_LEVEL',default='INFO').upper())

    @property
    def max_bytes(self): return self.max_upload_mb * 1024 * 1024
    @property
    def whatsapp_ready(self):
        return all([self.wa_token,self.wa_phone_id,self.wa_app_secret,self.wa_verify_token,self.wa_version])
