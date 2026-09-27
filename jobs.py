"""Durable Zoom task queue in the same SQLite file as the CRM.

The application runs one scheduler. Leases recover tasks after an interrupted run.
"""
import json
import sqlite3
import time
from contextlib import contextmanager


class JobStore:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS zoom_jobs (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                available_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
                message TEXT NOT NULL DEFAULT '')''')
            db.execute('CREATE INDEX IF NOT EXISTS zoom_jobs_due ON zoom_jobs(status,available_at)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enqueue(self, key, payload):
        with self.connect() as db:
            # Preserve retry schedule and cached source on repeated discovery.
            db.execute('INSERT OR IGNORE INTO zoom_jobs(id,payload,updated_at) VALUES(?,?,?)',
                       (key, json.dumps(payload, ensure_ascii=False), time.time()))

    def recover_interrupted(self):
        """Called once at process startup; this CRM has one background scheduler."""
        with self.connect() as db:
            db.execute("UPDATE zoom_jobs SET status='retry',available_at=0,message='Продолжаем подготовку материалов' WHERE status='running'")

    def claim(self):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM zoom_jobs WHERE status IN ('pending','retry','running') AND available_at<=? ORDER BY available_at,updated_at LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            # CPU inference may take longer than the old 20-minute lease.
            db.execute("UPDATE zoom_jobs SET status='running',attempts=attempts+1,available_at=?,updated_at=? WHERE id=?", (now + 7200, now, row['id']))
            return {**dict(row), 'payload': json.loads(row['payload']), 'attempts': row['attempts'] + 1}

    def cache(self, key, payload):
        with self.connect() as db:
            db.execute('UPDATE zoom_jobs SET payload=?,updated_at=? WHERE id=?',
                       (json.dumps(payload, ensure_ascii=False), time.time(), key))

    def finish(self, key, message='Конспект готов'):
        with self.connect() as db:
            db.execute("UPDATE zoom_jobs SET status='done',message=?,updated_at=? WHERE id=?", (message, time.time(), key))

    def retry(self, task, message):
        delay = min(21600, 300 * 2 ** min(task['attempts'] - 1, 7))
        with self.connect() as db:
            db.execute("UPDATE zoom_jobs SET status='retry',message=?,available_at=?,updated_at=? WHERE id=?", (message, time.time() + delay, time.time(), task['id']))

    def overview(self):
        with self.connect() as db:
            counts = {r['status']: r['n'] for r in db.execute('SELECT status,count(*) AS n FROM zoom_jobs GROUP BY status')}
            rows = db.execute("SELECT id,payload,status,message,available_at FROM zoom_jobs WHERE status!='done' ORDER BY updated_at DESC LIMIT 30").fetchall()
        items = []
        for row in rows:
            payload = json.loads(row['payload'])
            items.append({k: row[k] for k in ('id', 'status', 'message', 'available_at')} | {
                'date': payload.get('date', ''), 'target': payload.get('target', ''),
                'kind': payload.get('kind', '')})
        return {'counts': counts, 'items': items}
