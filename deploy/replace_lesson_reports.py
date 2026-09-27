"""Apply evaluated reports atomically, preserving previous reports and all accounting."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
import lesson_links
from reporting import FIELDS, validate_report
import local_reports


def replace(folder):
    folder = Path(folder)
    cases = json.loads((folder/'cases.json').read_text('utf-8'))
    with lesson_links.transaction(app) as db:
        before = app.all_data()
        metrics = app.metrics(before)
        db.execute('CREATE TABLE IF NOT EXISTS report_history(id INTEGER PRIMARY KEY, lesson_id TEXT NOT NULL, saved_at TEXT NOT NULL, data TEXT NOT NULL)')
        changed = []
        for case in cases:
            old = next(x for x in before['lessons'] if x['id'] == case['id'])
            if old.get('deleted') or old.get('report_locked') or old.get('reviewed'):
                raise ValueError('Manual edits or lesson state changed: ' + old['id'])
            if old.get('raw_summary') != case['raw_summary']:
                raise ValueError('Source changed: ' + old['id'])
            result = json.loads((folder/(case['id']+'.json')).read_text('utf-8'))
            report = validate_report(result['report'])
            if old.get('raw_transcript') and old['raw_transcript'] != case.get('raw_transcript'):
                raise ValueError('Transcript changed: ' + old['id'])
            digest = hashlib.sha256((case.get('raw_transcript') or old['raw_summary']).encode()).hexdigest()
            if result['analysis']['source_hash'] != digest or result['analysis']['version'] != local_reports.VERSION:
                raise ValueError('Evaluated output does not match source/version')
            if result['analysis'].get('automatic_check') != 'passed' or result['analysis'].get('check_scope') != 'facts_and_notes':
                raise ValueError('Automatic content check has not completed')
            if old.get('report_provider') == 'local' and all(old.get(k) == report[k] for k in FIELDS):
                continue
            # Protect text edits made after the evaluation snapshot, even if not locked.
            if any(old.get(k) != case.get(k) for k in FIELDS):
                raise ValueError('Report changed after snapshot: ' + old['id'])
            db.execute('INSERT INTO report_history(lesson_id,saved_at,data) VALUES(?,?,?)',
                (old['id'], datetime.datetime.now(datetime.timezone.utc).isoformat(), json.dumps(old, ensure_ascii=False)))
            app.save('lessons', {**old, **report, 'report_provider': 'local',
                'report_analysis': result['analysis'], 'report_state': 'ready', 'reviewed': False, 'report_error': ''})
            for row in db.execute('SELECT id,payload FROM zoom_jobs').fetchall():
                payload = json.loads(row['payload'])
                if payload.get('uuid') == old.get('zoom_uuid'):
                    if case.get('raw_transcript'):
                        payload.update(transcript=case['raw_transcript'],transcript_checked=True)
                    payload.update(lesson_id=old['id'], result=report, result_provider='local', report_analysis=result['analysis'])
                    db.execute("UPDATE zoom_jobs SET payload=?,status='done',message='Конспект готов' WHERE id=?", (json.dumps(payload, ensure_ascii=False), row['id']))
            changed.append(old['id'])
        after = app.all_data()
        assert app.metrics(after) == metrics, 'Accounting changed'
        for kind in app.KINDS - {'lessons'}:
            assert before[kind] == after[kind], 'Unrelated records changed'
        for old in before['lessons']:
            new = next(x for x in after['lessons'] if x['id'] == old['id'])
            for key in ('student', 'group', 'date', 'minutes', 'charges', 'attendance', 'calendar_key',
                        'zoom_uuid', 'recording', 'youtube_url', 'youtube_urls', 'raw_summary'):
                assert new.get(key) == old.get(key), 'Lesson identity/materials changed'
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return {'changed': changed, 'accounting': 'unchanged', 'integrity': 'ok'}


def queue_report_upgrades(from_date, provider='rules'):
    """Queue only untouched automatic reports that arrived during the release rehearsal."""
    datetime.date.fromisoformat(from_date)
    with lesson_links.transaction(app) as db:
        before = app.all_data()
        metrics = app.metrics(before)
        db.execute('CREATE TABLE IF NOT EXISTS report_history(id INTEGER PRIMARY KEY, lesson_id TEXT NOT NULL, saved_at TEXT NOT NULL, data TEXT NOT NULL)')
        jobs = [(row['id'],json.loads(row['payload'])) for row in db.execute('SELECT id,payload FROM zoom_jobs').fetchall()]
        queued, skipped = [], []
        for old in before['lessons']:
            if old.get('date','')<from_date or old.get('report_provider')!=provider or old.get('report_state')!='ready' or old.get('deleted'):
                continue
            matches = [(key,p) for key,p in jobs if old.get('zoom_uuid') and p.get('uuid')==old['zoom_uuid']]
            if old.get('reviewed') or old.get('report_locked') or len(matches)!=1:
                skipped.append(old['id']);continue
            key,payload = matches[0]
            if payload.get('result_provider')!=provider or any(old.get(k)!=payload.get('result',{}).get(k) for k in FIELDS):
                skipped.append(old['id']);continue
            db.execute('INSERT INTO report_history(lesson_id,saved_at,data) VALUES(?,?,?)',
                (old['id'],datetime.datetime.now(datetime.timezone.utc).isoformat(),json.dumps(old,ensure_ascii=False)))
            # Keep the previous text visible while its replacement is prepared.
            app.save('lessons',{**old,'report_state':'queued','report_error':''})
            for field in ('result','result_provider','report_analysis','local_work'):
                payload.pop(field,None)
            payload['lesson_id']=old['id']
            db.execute("UPDATE zoom_jobs SET payload=?,status='pending',attempts=0,available_at=0,message='Готовим материалы' WHERE id=?",
                (json.dumps(payload,ensure_ascii=False),key))
            queued.append(old['id'])
        after = app.all_data()
        assert app.metrics(after)==metrics, 'Accounting changed'
        for kind in app.KINDS-{'lessons'}:
            assert before[kind]==after[kind], 'Unrelated records changed'
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        return {'queued':queued,'skipped':skipped,'accounting':'unchanged'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--results', required=True)
    parser.add_argument('--queue-from', help='Also queue untouched old-provider reports received since this ISO date')
    args = parser.parse_args()
    app.DB = Path(args.database)
    if not app.DB.is_file(): raise SystemExit('Database does not exist')
    result = replace(args.results)
    if args.queue_from:result['recent_reports']=queue_report_upgrades(args.queue_from)
    print(json.dumps(result))
