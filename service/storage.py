import json
import sqlite3
import time
from contextlib import contextmanager

class Store:
    def __init__(self, data):
        self.data=data
        data.mkdir(parents=True,exist_ok=True)
        self.path=data/'service.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, channel TEXT NOT NULL,
              kind TEXT NOT NULL, reference_id TEXT NOT NULL, subject TEXT NOT NULL,
              grade TEXT NOT NULL, version TEXT NOT NULL, filename TEXT NOT NULL,
              state TEXT NOT NULL, result_status TEXT, message TEXT, output TEXT,
              retryable INTEGER NOT NULL DEFAULT 0, remote TEXT, delivery TEXT,
              idem TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
              UNIQUE(owner,idem));
            CREATE TABLE IF NOT EXISTS sessions (sender TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS auth (token TEXT PRIMARY KEY, owner TEXT NOT NULL, role TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS inbox (id TEXT PRIMARY KEY, sender TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (id INTEGER PRIMARY KEY AUTOINCREMENT, sender TEXT NOT NULL, kind TEXT NOT NULL,
              payload TEXT NOT NULL, job_id TEXT, dedupe TEXT UNIQUE, state TEXT NOT NULL DEFAULT 'queued',
              attempts INTEGER NOT NULL DEFAULT 0, due REAL NOT NULL, meta_id TEXT, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS reference_settings (id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS rates (key TEXT PRIMARY KEY, count INTEGER NOT NULL, until REAL NOT NULL);
            ''')
            # Upgrade existing REV2 installations without resetting their jobs.
            columns={row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            if 'diagnostic' not in columns:
                db.execute('ALTER TABLE jobs ADD COLUMN diagnostic TEXT')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.row_factory=sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback(); raise
        finally: db.close()

    def one(self,sql,args=()):
        with self.connect() as db:
            row=db.execute(sql,args).fetchone()
            return dict(row) if row else None
    def all(self,sql,args=()):
        with self.connect() as db: return [dict(r) for r in db.execute(sql,args).fetchall()]
    def execute(self,sql,args=()):
        with self.connect() as db: return db.execute(sql,args).rowcount
    def session(self,sender,minutes):
        row=self.one('SELECT * FROM sessions WHERE sender=?',(sender,))
        if row and time.time()-row['updated']<minutes*60: return json.loads(row['data'])
        return {'state':'main'}
    def save_session(self,sender,data):
        self.execute('INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(sender) DO UPDATE SET data=excluded.data,updated=excluded.updated',
                     (sender,json.dumps(data),time.time()))
    def emit(self,sender,text,job_id=None,dedupe=None):
        self.enqueue(sender,'text',{'text':text},job_id,dedupe)
    def enqueue(self,sender,kind,payload,job_id=None,dedupe=None):
        self.execute('INSERT OR IGNORE INTO outbox(sender,kind,payload,job_id,dedupe,due,created) VALUES(?,?,?,?,?,?,?)',
                     (sender,kind,json.dumps(payload),job_id,dedupe,time.time(),time.time()))
    def limited(self,key,maximum,seconds):
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM rates WHERE key=?',(key,)).fetchone()
            n=(row['count']+1) if row and row['until']>now else 1
            until=row['until'] if row and row['until']>now else now+seconds
            db.execute('INSERT OR REPLACE INTO rates VALUES(?,?,?)',(key,n,until))
            return n>maximum
