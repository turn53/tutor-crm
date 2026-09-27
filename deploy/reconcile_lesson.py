"""Targeted, transactional repair. Run with workers stopped, after a verified backup."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app
import lesson_links


def repair(event_id, source_id):
    with lesson_links.transaction(app) as conn:
        before = app.all_data()
        metrics = app.metrics(before)
        item = next(x for x in before['schedule'] if x['id'] == event_id)
        source = next(x for x in before['lessons'] if x['id'] == source_id)
        target = lesson_links.linked(before, item)
        if not target:
            raise ValueError('Expected an existing calendar lesson')
        if source.get('merged_into') == target['id']:
            return {'already_repaired': True, 'target': target['id']}
        if not source.get('notes') or not source.get('zoom_uuid') or source.get('attendance') != 'needs_confirmation':
            raise ValueError('Source state changed; review before repair')
        dest = lesson_links.reconcile_event(app, item)
        after = app.all_data()
        assert app.metrics(after) == metrics, 'Financial metrics changed'
        for kind in app.KINDS - {'lessons'}:
            assert after[kind] == before[kind], 'Unrelated records changed: ' + kind
        untouched = lambda data: [x for x in data['lessons'] if x['id'] not in (source_id, target['id'])]
        assert untouched(after) == untouched(before), 'Other lessons changed'
        for key in ('minutes', 'charges', 'date', 'start_time', 'attendance', 'calendar_key'):
            assert dest.get(key) == target.get(key), 'Original accounting changed: ' + key
        for key in ('notes', 'raw_summary', 'zoom_uuid', 'youtube_url', 'youtube_urls'):
            if source.get(key):
                assert dest.get(key) == source[key], 'Source material lost: ' + key
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        return {'target': dest['id'], 'source': source_id, 'minutes': dest['minutes'],
                'zoom_minutes': dest.get('zoom_minutes'), 'amount': sum(x['amount'] for x in dest['charges']),
                'active_lessons': len([x for x in after['lessons'] if not x.get('deleted')]),
                'preserved_counts': {k: len(after[k]) for k in app.KINDS - {'lessons'}},
                'integrity': 'ok', 'financial_metrics': 'unchanged'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--event', required=True)
    parser.add_argument('--source', required=True)
    args = parser.parse_args()
    app.DB = Path(args.database)
    if not app.DB.is_file():
        raise SystemExit('Database does not exist')
    print(json.dumps(repair(args.event, args.source), ensure_ascii=False))
