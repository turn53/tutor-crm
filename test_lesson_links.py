import copy
import json
import threading
import unittest
import urllib.error
from unittest.mock import patch

import app
import calendar_sync as cal
import google_services as google
import lesson_links as links
import video_sync
import zoom_sync
from jobs import JobStore
import test_automation
from test_automation import REPORT


class LessonLinksTests(unittest.TestCase):
    def setUp(self):
        self.f = test_automation.AutomationTests()
        self.f.setUp()
        self.event = {'id': 'event', 'summary': 'Урок', 'etag': 'v1',
                      'start': {'dateTime': '2026-09-18T01:30:00+03:00'},
                      'end': {'dateTime': '2026-09-18T02:30:00+03:00'}}
        self.payload = {'uuid': 'test-uuid', 'kind': 'groups', 'target': 'ege',
                        'start_time': '2026-09-18T01:32:10+03:00', 'date': '2026-09-18',
                        'minutes': 62, 'recording': 'https://zoom.us/test'}

    def tearDown(self):
        self.f.tearDown()

    def event_item(self, completed=False):
        cal.import_event(app, 'primary', self.event)
        item = app.all_data()['schedule'][0]
        cal.bind(app, item['id'], group='ege')
        if completed:
            cal.attendance(app, item['id'], 'completed')
        return next(x for x in app.all_data()['schedule'] if x['id'] == item['id'])

    def lessons(self):
        return [x for x in app.all_data()['lessons'] if not x.get('deleted')
                and (x.get('zoom_uuid') or x.get('calendar_key'))]

    def sync(self, model=None, request=None):
        with patch.object(zoom_sync, 'config', return_value=self.f.config), \
             patch.object(zoom_sync, 'request', side_effect=request or self.f.fake), \
             patch.object(zoom_sync, 'codex_report', side_effect=model or (lambda _: REPORT)):
            zoom_sync.sync(app.all_data, app.save, self.f.store, app.resolve_zoom)

    def assert_hour(self, lesson):
        self.assertEqual(lesson['minutes'], 60)
        self.assertEqual(sum(c['amount'] for c in lesson['charges']), 2000)
        self.assertEqual(lesson['billing_basis'], 'schedule')

    def test_queued_before_completion_rechecks_identity(self):
        with patch.object(zoom_sync, 'config', return_value=self.f.config):
            zoom_sync.discover(self.f.fake, app.all_data, self.f.store)
        item = self.event_item(completed=True)
        self.sync()
        self.sync()
        self.assertEqual(len(self.lessons()), 1)
        lesson = self.lessons()[0]
        self.assertEqual(lesson['calendar_key'], item['id'])
        self.assertEqual(lesson['notes'], REPORT['notes'])
        self.assertEqual(lesson['attendance'], 'completed')
        self.assert_hour(lesson)

    def test_recording_lengths_never_change_scheduled_charge(self):
        self.event_item(completed=True)
        for minutes in (55, 60, 62, 65, 70):
            with self.subTest(minutes=minutes):
                lesson = app.resolve_zoom({**self.payload, 'minutes': minutes})
                self.assert_hour(lesson)
                self.assertEqual(lesson['zoom_minutes'], minutes)
        self.assertEqual(len(self.lessons()), 1)

    def test_zoom_first_with_existing_schedule_uses_schedule(self):
        item = self.event_item()
        self.sync()
        lesson = self.lessons()[0]
        self.assert_hour(lesson)
        self.assertEqual(lesson['attendance'], 'needs_confirmation')
        cal.attendance(app, item['id'], 'completed')
        self.assertEqual(len(self.lessons()), 1)
        self.assertEqual(self.lessons()[0]['notes'], REPORT['notes'])

    def test_zoom_before_calendar_merges_ready_report_and_redirects_jobs(self):
        self.sync()
        source = self.lessons()[0]
        app.save('lessons', {**source, 'youtube_urls': ['https://www.youtube.com/watch?v=test'],
                            'youtube_url': 'https://www.youtube.com/watch?v=test'})
        video_sync.initialise(app)
        with app.connect() as db:
            db.execute("INSERT INTO video_jobs(id,payload,status) VALUES(?,?,'ready')",
                       ('video', json.dumps({'lesson_id': source['id'], 'video_id': 'test'})))
        item = self.event_item(completed=True)
        dest = self.lessons()[0]
        self.assertEqual(len(self.lessons()), 1)
        self.assertEqual(dest['calendar_key'], item['id'])
        self.assertEqual(dest['notes'], REPORT['notes'])
        self.assertEqual(dest['youtube_url'], 'https://www.youtube.com/watch?v=test')
        self.assert_hour(dest)
        with app.connect() as db:
            for table in ('zoom_jobs', 'video_jobs'):
                self.assertEqual(json.loads(db.execute('SELECT payload FROM '+table).fetchone()[0])['lesson_id'], dest['id'])
            self.assertEqual(db.execute('SELECT count(*) FROM lesson_merges').fetchone()[0], 1)
        self.assertTrue(links.canonical(app, source['id'])['id'] == dest['id'])
        with self.assertRaises(ValueError):
            app.trash('lessons', source['id'], restore=True)
        self.f.store = JobStore(app.DB)
        self.sync()
        self.assertEqual(len(self.lessons()), 1)

    def test_reconcile_existing_duplicate_preserves_completed_financial_snapshot(self):
        item = self.event_item(completed=True)
        dest = self.lessons()[0]
        source = app.save('lessons', {'id': 'legacy-zoom', 'student': '', 'group': 'ege',
                          'date': self.payload['date'], 'start_time': self.payload['start_time'],
                          'minutes': 62, 'zoom_uuid': self.payload['uuid'], 'attendance': 'needs_confirmation',
                          'notes': REPORT['notes'], 'raw_summary': 'source', 'report_state': 'ready'})
        before = app.metrics(app.all_data())
        merged = links.reconcile_event(app, item)
        self.assertEqual(merged['charges'], dest['charges'])
        self.assertEqual(merged['minutes'], 60)
        self.assertEqual(merged['zoom_minutes'], 62)
        self.assertEqual(merged['raw_summary'], 'source')
        self.assertEqual(app.metrics(app.all_data()), before)
        self.assertEqual(len(self.lessons()), 1)

    def test_no_schedule_does_not_infer_bill_from_zoom(self):
        lesson = app.resolve_zoom(self.payload)
        self.assertEqual(lesson['billing_basis'], 'unassigned')
        self.assertEqual(lesson['zoom_minutes'], 62)
        with self.assertRaises(ValueError):
            app.save('lessons', {**lesson, 'attendance': 'completed'})

    def test_ambiguous_schedule_creates_no_lesson(self):
        self.event_item()
        cal.import_event(app, 'primary', {**self.event, 'id': 'second'})
        second = next(x for x in app.all_data()['schedule'] if x['event_id'] == 'second')
        cal.bind(app, second['id'], group='ege')
        with self.assertRaises(links.LinkConflict):
            app.resolve_zoom(self.payload)
        self.assertEqual(self.lessons(), [])

    def test_two_zoom_sessions_do_not_overwrite_each_other(self):
        self.event_item()
        app.resolve_zoom(self.payload)
        with self.assertRaises(links.LinkConflict):
            app.resolve_zoom({**self.payload, 'uuid': 'another-session'})
        self.assertEqual(self.lessons()[0]['zoom_uuid'], 'test-uuid')

    def test_moved_unconfirmed_materials_cannot_confirm_old_date(self):
        item = self.event_item()
        app.resolve_zoom(self.payload)
        cal.import_event(app, 'primary', {**self.event,
            'start': {'dateTime': '2026-09-19T01:30:00+03:00'},
            'end': {'dateTime': '2026-09-19T02:30:00+03:00'}})
        with self.assertRaises(ValueError):
            cal.attendance(app, item['id'], 'completed')
        self.assertEqual(self.lessons()[0]['attendance'], 'needs_confirmation')

    def test_end_time_change_also_requires_review(self):
        item = self.event_item(completed=True)
        cal.import_event(app, 'primary', {**self.event, 'end': {'dateTime': '2026-09-18T03:00:00+03:00'}})
        self.assertTrue(app.all_data()['schedule'][0]['needs_review'])
        self.assert_hour(self.lessons()[0])

    def test_deleted_import_not_resurrected(self):
        source = app.resolve_zoom(self.payload)
        app.trash('lessons', source['id'])
        self.event_item()
        self.assertTrue(app.resolve_zoom(self.payload)['deleted'])
        self.assertEqual(self.lessons(), [])

    def test_manual_report_is_preserved(self):
        self.event_item(completed=True)
        lesson = self.lessons()[0]
        app.save('lessons', {**lesson, 'notes': 'Мой конспект', 'report_locked': True})
        self.sync()
        self.assertEqual(self.lessons()[0]['notes'], 'Мой конспект')
        self.assertEqual(self.lessons()[0]['zoom_uuid'], 'test-uuid')

    def test_merge_conflict_rolls_back(self):
        item = self.event_item(completed=True)
        dest = self.lessons()[0]
        app.save('lessons', {**dest, 'notes': 'Мой конспект', 'report_locked': True})
        app.save('lessons', {'id': 'source', 'group': 'ege', 'student': '', 'date': '2026-09-18',
                            'minutes': 62, 'start_time': self.payload['start_time'], 'zoom_uuid': 'test-uuid',
                            'notes': 'Другой конспект', 'attendance': 'needs_confirmation'})
        before = copy.deepcopy(app.all_data())
        with self.assertRaises(links.LinkConflict):
            links.reconcile_event(app, item)
        self.assertEqual(app.all_data(), before)

    def test_simultaneous_completion_and_zoom_create_one_lesson(self):
        item = self.event_item()
        barrier = threading.Barrier(2)
        errors = []
        def run(fn):
            try:
                barrier.wait(timeout=5)
                fn()
            except Exception as e:
                errors.append(e)
        workers = [threading.Thread(target=run, args=(lambda: cal.attendance(app, item['id'], 'completed'),)),
                   threading.Thread(target=run, args=(lambda: app.resolve_zoom(self.payload),))]
        for worker in workers: worker.start()
        for worker in workers: worker.join(timeout=10)
        self.assertFalse(any(w.is_alive() for w in workers))
        self.assertEqual(errors, [])
        self.assertEqual(len(self.lessons()), 1)
        self.assertEqual(self.lessons()[0]['attendance'], 'completed')
        self.assert_hour(self.lessons()[0])

    def test_retry_with_cached_result_routes_to_calendar(self):
        self.sync(model=lambda _: (_ for _ in ()).throw(zoom_sync.ReportUnavailable('Later')))
        source = self.lessons()[0]
        with self.f.store.connect() as db:
            row = db.execute('SELECT * FROM zoom_jobs').fetchone()
            payload = json.loads(row['payload'])
        payload.update(result=REPORT, result_provider='codex')
        self.f.store.cache(row['id'], payload)
        self.event_item(completed=True)
        self.f.make_due()
        self.sync()
        self.assertEqual(len(self.lessons()), 1)
        self.assertEqual(self.lessons()[0]['notes'], REPORT['notes'])
        self.assertNotEqual(self.lessons()[0]['id'], source['id'])

    def test_cached_source_still_processed_when_zoom_is_offline(self):
        self.event_item(completed=True)
        self.sync(model=lambda _: (_ for _ in ()).throw(zoom_sync.ReportUnavailable('Later')))
        self.f.make_due()
        def offline(*args, **kwargs):
            raise OSError('offline')
        self.sync(request=offline)
        self.assertEqual(self.lessons()[0]['notes'], REPORT['notes'])

    def test_cancelled_occurrence_is_not_silently_reactivated(self):
        item = self.event_item()
        cal.attendance(app, item['id'], 'cancelled')
        with self.assertRaises(links.LinkConflict):
            app.resolve_zoom(self.payload)
        self.assertEqual(self.lessons(), [])

    def test_ninety_minute_schedule_ignores_seventy_minute_recording(self):
        self.event['end'] = {'dateTime': '2026-09-18T03:00:00+03:00'}
        self.event_item(completed=True)
        lesson = app.resolve_zoom({**self.payload, 'minutes': 70})
        self.assertEqual(lesson['minutes'], 90)
        self.assertEqual(sum(x['amount'] for x in lesson['charges']), 3000)

    def test_weekly_occurrences_remain_separate(self):
        self.event['recurringEventId'] = 'weekly-series'
        item = self.event_item(completed=True)
        cal.bind(app, item['id'], group='ege', series=True)
        cal.import_event(app, 'primary', {**self.event, 'id': 'next-week',
            'start': {'dateTime': '2026-09-25T01:30:00+03:00'},
            'end': {'dateTime': '2026-09-25T02:30:00+03:00'}})
        lesson = app.resolve_zoom(self.payload)
        self.assertEqual(lesson['calendar_key'], item['id'])
        next_week = next(x for x in app.all_data()['schedule'] if x['event_id'] == 'next-week')
        self.assertEqual(next_week['group'], 'ege')
        self.assertEqual(next_week['status'], 'planned')
        self.assertIsNone(links.linked(app.all_data(), next_week))

    def test_stale_video_worker_cannot_restore_old_lesson_link(self):
        source = app.resolve_zoom(self.payload)
        video_sync.initialise(app)
        with app.connect() as db:
            db.execute("INSERT INTO video_jobs(id,payload,status) VALUES(?,?,'processing')",
                       ('video', json.dumps({'lesson_id': source['id'], 'video_id': 'test'})))
        stale = video_sync.jobs(app)[0]
        self.event_item(completed=True)
        video_sync.put(app, stale, 'ready')
        self.assertEqual(video_sync.jobs(app)[0]['payload']['lesson_id'], self.lessons()[0]['id'])

    def test_ready_merge_publishes_once_without_private_evidence(self):
        self.sync()
        item = self.event_item(completed=True)
        published = []
        def api(service, path, **kw):
            if 'timeMin=' in path: return {'items': [self.event]}
            if kw.get('method') == 'PATCH': published.append(kw['body']['description'])
            return {**self.event, 'description': 'Ссылка на урок'}
        with patch.object(google, 'config', return_value={'calendar_enabled': True, 'publish_notes': True}), \
             patch.object(google, 'access_token', return_value='fake'), patch.object(google, 'api', side_effect=api):
            cal.sync(app)
            cal.sync(app)
        self.assertEqual(len(published), 1)
        self.assertIn('Ссылка на урок', published[0])
        self.assertIn(REPORT['notes'], published[0])
        self.assertNotIn(self.f.source, published[0])
        self.assertEqual(len(self.lessons()), 1)

    def test_publishing_failure_does_not_block_next_event(self):
        first = self.event_item(completed=True)
        second_event = {**self.event, 'id': 'second',
                        'start': {'dateTime': '2026-09-18T03:30:00+03:00'},
                        'end': {'dateTime': '2026-09-18T04:30:00+03:00'}}
        cal.import_event(app, 'primary', second_event)
        second = next(x for x in app.all_data()['schedule'] if x['event_id'] == 'second')
        cal.bind(app, second['id'], group='ege')
        cal.attendance(app, second['id'], 'completed')
        for lesson in self.lessons():
            app.save('lessons', {**lesson, 'notes': 'Теория'})
        published = []
        def api(service, path, **kw):
            if 'timeMin=' in path: return {'items': [self.event, second_event]}
            if '/event' in path and '/second' not in path:
                raise urllib.error.HTTPError(path, 403, 'forbidden', {}, None)
            if kw.get('method') == 'PATCH': published.append(path)
            return {**second_event, 'description': 'Личная заметка'}
        with patch.object(google, 'config', return_value={'calendar_enabled': True, 'publish_notes': True}), \
             patch.object(google, 'access_token', return_value='fake'), patch.object(google, 'api', side_effect=api):
            cal.sync(app)
        self.assertEqual(len(published), 1)
        first = next(x for x in app.all_data()['schedule'] if x['id'] == first['id'])
        self.assertIn('403', first['publish_error'])


if __name__ == '__main__':
    unittest.main()
