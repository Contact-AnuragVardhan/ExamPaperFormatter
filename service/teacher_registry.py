"""Teacher identity lookup and administrator-managed registration in Teacher Helper."""
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

log = logging.getLogger('exam.teacher_registry')

SCHOOL_NAME = 'Parivaar School'
PREFERRED_LANGUAGE = 'Hinglish'


class TeacherRegistryUnavailable(Exception):
    pass


class TeacherAlreadyExists(Exception):
    pass


class InvalidTeacherDetails(ValueError):
    pass


def normalized_number(number):
    value = str(number or '').strip()
    if not value or any(ch not in '+-() 0123456789' for ch in value):
        return ''
    digits = ''.join(ch for ch in value if ch.isdigit())
    return digits if 7 <= len(digits) <= 15 else ''


def valid_teacher_details(whatsapp_number, teacher_name, default_grade, default_subject):
    digits = normalized_number(whatsapp_number)
    if not digits:
        raise InvalidTeacherDetails('Enter a valid WhatsApp number with country code (7–15 digits).')
    details = {'whatsapp_number': '+' + digits}
    for field, value, label, max_length in (
        ('teacher_name', teacher_name, 'Teacher name', 255),
        ('default_grade', default_grade, 'Default grade', 100),
        ('default_subject', default_subject, 'Default subject', 100),
    ):
        if not isinstance(value, str):
            raise InvalidTeacherDetails(f'{label} is required.')
        cleaned = value.strip()
        if not cleaned or len(cleaned) > max_length or any(ord(c) < 32 or ord(c) == 127 for c in cleaned):
            raise InvalidTeacherDetails(f'{label} must be 1–{max_length} characters on one line.')
        details[field] = cleaned
    return details


class TeacherRegistry:
    def __init__(self, database_url):
        self.database_url = (database_url or '').strip()

    def _connect(self, write=False):
        url = self.database_url
        if url.startswith('sqlite:///'):
            path = Path(unquote(url[len('sqlite:///'):])).expanduser().resolve()
            if not path.is_file():
                raise TeacherRegistryUnavailable('Teacher registry is unavailable.')
            mode = 'rw' if write else 'ro'
            db = sqlite3.connect(f'{path.as_uri()}?mode={mode}', uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            return db, '?'
        if url.startswith(('postgresql://', 'postgres://', 'postgresql+psycopg://')):
            try:
                import psycopg
            except ImportError as exc:
                raise TeacherRegistryUnavailable('PostgreSQL driver is missing.') from exc
            url = url.replace('postgresql+psycopg://', 'postgresql://', 1)
            return psycopg.connect(url, connect_timeout=5), '%s'
        raise TeacherRegistryUnavailable('Teacher registry is not configured.')

    def _query(self, statement, arguments):
        try:
            db, marker = self._connect()
            with closing(db), db:
                cursor = db.execute(statement.replace('?', marker), arguments)
                row = cursor.fetchone()
                return {'id': int(row[0]), 'whatsapp_number': row[1]} if row else None
        except TeacherRegistryUnavailable:
            raise
        except Exception as exc:
            log.warning('Teacher registry lookup failed: %s', type(exc).__name__)
            raise TeacherRegistryUnavailable('Teacher registry is unavailable.') from exc

    def find_by_number(self, number):
        digits = normalized_number(number)
        if not digits:
            return None
        return self._query(
            "SELECT id, whatsapp_number FROM teacher_profile "
            "WHERE whatsapp_number IN (?, ?) OR REPLACE(whatsapp_number, '+', '') = ? "
            "ORDER BY id LIMIT 1",
            (f'+{digits}', digits, digits),
        )

    def find_by_id(self, teacher_id):
        if not str(teacher_id).isdigit():
            return None
        return self._query(
            'SELECT id, whatsapp_number FROM teacher_profile WHERE id = ? LIMIT 1',
            (int(teacher_id),),
        )

    def add_teacher(self, whatsapp_number, teacher_name, default_grade, default_subject):
        details = valid_teacher_details(whatsapp_number, teacher_name, default_grade, default_subject)
        if self.find_by_number(details['whatsapp_number']):
            raise TeacherAlreadyExists('This WhatsApp number is already registered.')
        try:
            db, marker = self._connect(write=True)
            with closing(db), db:
                now = datetime.now(timezone.utc).replace(tzinfo=None)
                statement = (
                    'INSERT INTO teacher_profile '
                    '(whatsapp_number, teacher_name, default_grade, default_subject, '
                    'school_name, preferred_language, created_at, updated_at) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id'
                )
                row = db.execute(statement.replace('?', marker), (
                    details['whatsapp_number'], details['teacher_name'], details['default_grade'],
                    details['default_subject'], SCHOOL_NAME, PREFERRED_LANGUAGE, now, now,
                )).fetchone()
                details.update(id=int(row[0]), school_name=SCHOOL_NAME, preferred_language=PREFERRED_LANGUAGE)
                return details
        except sqlite3.IntegrityError as exc:
            if 'unique' in str(exc).lower():
                raise TeacherAlreadyExists('This WhatsApp number is already registered.') from exc
            raise TeacherRegistryUnavailable('Teacher registration is temporarily unavailable.') from exc
        except TeacherRegistryUnavailable:
            raise
        except Exception as exc:
            if type(exc).__name__ == 'UniqueViolation':
                raise TeacherAlreadyExists('This WhatsApp number is already registered.') from exc
            log.warning('Teacher registration failed: %s', type(exc).__name__)
            raise TeacherRegistryUnavailable('Teacher registration is temporarily unavailable.') from exc
