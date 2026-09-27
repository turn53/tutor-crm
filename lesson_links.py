"""Atomic calendar/Zoom identity reconciliation. Recording length is never billing time."""
import datetime as dt
import json
import hashlib
from contextlib import contextmanager

MOSCOW = dt.timezone(dt.timedelta(hours=3))
REPORT_FIELDS = ('topic', 'notes', 'homework', 'next', 'understanding', 'review',
                 'reviewed', 'report_locked', 'report_state', 'report_provider',
                 'report_analysis', 'report_error')


class LinkConflict(ValueError):
    pass


def instant(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise LinkConflict('Для связи занятия нужен часовой пояс')
    return parsed


@contextmanager
def transaction(a):
    with a.LOCK, a.connect() as conn:
        previous = getattr(a.LOCAL, 'connection', None)
        a.LOCAL.connection = conn
        try:
            yield conn
        finally:
            a.LOCAL.connection = previous


def canonical(a, identifier):
    lessons = {x['id']: x for x in a.all_data()['lessons']}
    seen = set()
    while identifier in lessons and lessons[identifier].get('merged_into'):
        if identifier in seen:
            raise LinkConflict('Повреждена связь объединённых занятий')
        seen.add(identifier)
        identifier = lessons[identifier]['merged_into']
    return lessons.get(identifier)


def same_target(left, right):
    return (left.get('student', '') == right.get('student', '')
            and left.get('group', '') == right.get('group', ''))


def near(left, right):
    return abs((instant(left) - instant(right)).total_seconds()) <= 1800


def matching_events(data, lesson):
    start = lesson.get('zoom_start_time') or lesson.get('start_time')
    if not start:
        return []
    return [x for x in data['schedule'] if not x.get('deleted') and not x.get('all_day')
            and (x.get('student') or x.get('group')) and same_target(x, lesson)
            and near(x['start'], start)]


def linked(data, item):
    matches = [x for x in data['lessons'] if not x.get('deleted')
               and x.get('calendar_key') == item['id']]
    if len(matches) > 1:
        raise LinkConflict('К событию привязано несколько занятий. Нужна проверка')
    return matches[0] if matches else None


def scheduled_lesson(a, item):
    if item.get('all_day') or item.get('remote_cancelled') or item.get('status') == 'cancelled':
        raise LinkConflict('Событие отменено или не содержит времени занятия')
    minutes = round((instant(item['end']) - instant(item['start'])).total_seconds() / 60)
    identifier = 'lesson-' + item['id']
    if any(x['id'] == identifier for x in a.all_data()['lessons']):
        raise LinkConflict('Занятие находится в корзине. Сначала восстанови его')
    return a.save('lessons', {
        'id': identifier, 'calendar_key': item['id'],
        'student': item.get('student', ''), 'group': item.get('group', ''),
        'date': str(instant(item['start']).astimezone(MOSCOW).date()),
        'start_time': item['start'], 'scheduled_end': item['end'], 'minutes': minutes,
        'billing_basis': 'schedule', 'topic': item['title'], 'notes': '',
        'attendance': 'completed' if item.get('status') == 'completed' else 'needs_confirmation',
        'report_state': 'waiting_source'})


def merge(a, dest, source, conn):
    """Only merge an unconfirmed import; preserve both originals in an audit table."""
    if source['id'] == dest['id']:
        return dest
    if source.get('attendance') != 'needs_confirmation' or source.get('calendar_key'):
        raise LinkConflict('Найдено второе подтверждённое или связанное занятие. Нужна проверка')
    if not same_target(dest, source) or (dest.get('zoom_uuid') and dest['zoom_uuid'] != source.get('zoom_uuid')):
        raise LinkConflict('Материалы относятся к разным занятиям. Нужна проверка')
    if dest.get('notes') and source.get('notes') and dest['notes'] != source['notes']:
        raise LinkConflict('В обеих записях есть разные конспекты. Нужна проверка перед объединением')
    conn.execute('CREATE TABLE IF NOT EXISTS lesson_merges(source_id TEXT PRIMARY KEY, target_id TEXT NOT NULL, before_json TEXT NOT NULL, created_at TEXT NOT NULL)')
    conn.execute('INSERT INTO lesson_merges VALUES(?,?,?,?)', (
        source['id'], dest['id'], json.dumps({'target': dest, 'source': source}, ensure_ascii=False),
        dt.datetime.now(dt.timezone.utc).isoformat()))
    merged = dict(dest)
    if not dest.get('report_locked') and not dest.get('reviewed') and not dest.get('notes'):
        merged.update({k: source[k] for k in REPORT_FIELDS if k in source})
    for key in ('zoom_uuid', 'recording', 'raw_summary', 'raw_transcript', 'zoom_start_time', 'zoom_minutes'):
        if source.get(key):
            merged[key] = source[key]
    merged.setdefault('zoom_start_time', source.get('start_time'))
    merged.setdefault('zoom_minutes', source.get('minutes'))
    urls = list(dict.fromkeys(dest.get('youtube_urls', []) + source.get('youtube_urls', [])
                             + [x for x in (dest.get('youtube_url'), source.get('youtube_url')) if x]))
    if urls:
        merged.update(youtube_urls=urls, youtube_url=urls[0])
    merged = a.save('lessons', merged)
    a.save('lessons', {**source, 'deleted': True, 'merged_into': dest['id'],
                      'deleted_at': dt.datetime.now(dt.timezone.utc).isoformat()})
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('zoom_jobs', 'video_jobs'):
        if table not in tables:
            continue
        for row in conn.execute('SELECT id,payload FROM ' + table).fetchall():
            payload = json.loads(row['payload'])
            if payload.get('lesson_id') == source['id'] or (table == 'zoom_jobs' and payload.get('uuid') == source.get('zoom_uuid')):
                payload['lesson_id'] = dest['id']
                conn.execute('UPDATE ' + table + ' SET payload=? WHERE id=?', (json.dumps(payload, ensure_ascii=False), row['id']))
    return merged


def reconcile_event(a, item, create=False):
    with transaction(a) as conn:
        data = a.all_data()
        item = next(x for x in data['schedule'] if x['id'] == item['id'])
        dest = linked(data, item)
        if item.get('needs_review'):
            raise LinkConflict('Время события изменилось после привязки материалов или проведения. Проверь занятие')
        if item.get('all_day') or item.get('remote_cancelled') or item.get('status') == 'cancelled':
            return dest
        sources = [x for x in data['lessons'] if not x.get('deleted') and x.get('zoom_uuid')
                   and not x.get('calendar_key') and same_target(x, item)
                   and (x.get('zoom_start_time') or x.get('start_time'))
                   and near(item['start'], x.get('zoom_start_time') or x['start_time'])]
        if len(sources) > 1:
            raise LinkConflict('На это время найдено несколько записей Zoom. Нужна проверка')
        if sources:
            events = matching_events(data, sources[0])
            if len(events) != 1:
                raise LinkConflict('Запись Zoom подходит к нескольким событиям. Нужна проверка')
        if not dest and (create or sources):
            dest = scheduled_lesson(a, item)
        if dest and sources:
            dest = merge(a, dest, sources[0], conn)
        if dest and not dest.get('billing_basis'):
            dest = a.save('lessons', {**dest, 'billing_basis': 'schedule', 'scheduled_end': item['end']})
        return dest


def resolve_zoom(a, payload):
    """Re-evaluate current identity on EVERY attempt, including cached results."""
    with transaction(a):
        data = a.all_data()
        candidates = [x for x in data['lessons'] if x.get('zoom_uuid') == payload['uuid']]
        active = [x for x in candidates if not x.get('deleted')]
        if len(active) > 1:
            raise LinkConflict('Одна запись Zoom связана с несколькими уроками')
        current = active[0] if active else (canonical(a, candidates[0]['id']) if candidates else None)
        if current and current.get('deleted'):
            return current  # Do not resurrect a deliberately deleted import.
        if current and current.get('calendar_key'):
            item = next((x for x in data['schedule'] if x['id'] == current['calendar_key']), None)
            if item and item.get('needs_review'):
                raise LinkConflict('Расписание изменилось после привязки Zoom. Нужна проверка')
            return a.save('lessons', {**current, 'zoom_start_time': payload['start_time'],
                                      'zoom_minutes': payload['minutes'],
                                      'recording': payload.get('recording') or current.get('recording', '')})
        target = {'student': payload['target'] if payload['kind'] == 'students' else '',
                  'group': payload['target'] if payload['kind'] == 'groups' else '',
                  'start_time': payload['start_time']}
        events = matching_events(data, target)
        if len(events) > 1:
            raise LinkConflict('Запись Zoom подходит к нескольким событиям. Нужна проверка')
        if events:
            item = events[0]
            if item.get('remote_cancelled') or item.get('status') == 'cancelled':
                raise LinkConflict('Подходящий урок отменён. Проверь его перед привязкой Zoom')
            current = reconcile_event(a, item, create=True)
            if current.get('zoom_uuid') and current['zoom_uuid'] != payload['uuid']:
                raise LinkConflict('У этого урока уже есть другая запись Zoom. Нужна проверка')
        elif not current:
            # No timetable means no inferred price from recording duration.
            current = a.save('lessons', {
                **target, 'id': 'zoom-' + hashlib.sha256(payload['uuid'].encode()).hexdigest()[:24],
                'date': payload['date'], 'minutes': 60, 'billing_basis': 'unassigned',
                'attendance': 'needs_confirmation', 'topic': 'Материалы урока готовятся',
                'report_state': 'waiting_source', 'notes': ''})
        return a.save('lessons', {**current, 'zoom_uuid': payload['uuid'],
                                  'zoom_start_time': payload['start_time'],
                                  'zoom_minutes': payload['minutes'], 'recording': payload.get('recording', '')})
