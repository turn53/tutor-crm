import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import app
from jobs import JobStore
import reporting
import zoom_sync

REPORT = dict(topic='Векторы', notes='AB = (xB − xA, yB − yA)',
              understanding='Не зафиксировано', homework='Повторить формулы', next='Скалярное произведение')


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_db = app.DB
        app.DB = Path(self.tmp.name) / 'crm.sqlite3'
        app.seed()
        group = next(x for x in app.all_data()['groups'] if x['id'] == 'ege')
        app.save('groups', {**group, 'zoom_id': '123'})
        self.store = JobStore(app.DB)
        self.config = {'account_id':'x','client_id':'x','client_secret':'x','user_id':'x',
                       'provider':'codex','enabled':True}
        self.source = 'Изучали координаты вектора и его длину.'

    def tearDown(self):
        app.DB = self.previous_db
        self.tmp.cleanup()

    def fake(self, url, data=None, headers=None):
        if 'oauth/token' in url: return {'access_token':'secret-do-not-log'}
        if '/recordings?' in url:
            return {'meetings':[{'id':123,'uuid':'test-uuid','start_time':'2026-09-17T22:30:00Z',
                                'duration':60,'share_url':'https://zoom.us/test'}]}
        if '/past_meetings/' in url: return {'participants_count':2,'duration':60}
        return {'summary_content':self.source}

    def run_sync(self, model=None, request=None):
        with patch.object(zoom_sync,'config',return_value=self.config), \
             patch.object(zoom_sync,'request',side_effect=request or self.fake), \
             patch.object(zoom_sync,'codex_report',side_effect=model or (lambda source: REPORT)):
            zoom_sync.sync(app.all_data, app.save, self.store)

    def lesson(self):
        return next(x for x in app.all_data()['lessons'] if x.get('zoom_uuid'))

    def make_due(self):
        with self.store.connect() as db: db.execute('UPDATE zoom_jobs SET available_at=0')

    def test_reset_cutoff_blocks_old_recordings_but_allows_new(self):
        self.config['import_from']='2026-09-18T02:00:00+03:00'
        self.run_sync()
        self.assertFalse(any(x.get('zoom_uuid') for x in app.all_data()['lessons']))
        self.assertEqual(self.store.overview()['counts'], {})
        self.config['import_from']='2026-09-18T01:30:00+03:00'
        self.run_sync()
        self.assertEqual(self.lesson()['zoom_uuid'], 'test-uuid')

    def test_failed_model_keeps_lesson_source_and_retries_after_restart(self):
        def failed(source): raise reporting.ReportUnavailable('Лимит подписки')
        self.run_sync(model=failed)
        self.assertEqual(self.lesson()['raw_summary'], self.source)
        self.assertEqual(self.store.overview()['counts'], {'retry':1})
        self.assertEqual(self.lesson()['attendance'], 'needs_confirmation')
        self.store = JobStore(app.DB)  # recreate scheduler storage
        self.make_due()
        self.run_sync()
        self.assertEqual(self.lesson()['notes'], REPORT['notes'])
        self.assertEqual(self.store.overview()['counts'], {'done':1})
        self.run_sync()
        self.assertEqual(len([x for x in app.all_data()['lessons'] if x.get('zoom_uuid')]), 1)

    def test_cached_source_processed_when_zoom_is_offline(self):
        def failed(source): raise reporting.ReportUnavailable('Лимит подписки')
        self.run_sync(model=failed)
        self.make_due()
        def offline(*args, **kwargs): raise OSError('offline')
        self.run_sync(request=offline)
        self.assertEqual(self.lesson()['report_state'], 'ready')

    def test_manual_edit_during_generation_is_not_overwritten(self):
        def edited(source):
            lesson=self.lesson()
            app.save('lessons',{**lesson,'notes':'Мой конспект','report_locked':True})
            return REPORT
        self.run_sync(model=edited)
        self.assertEqual(self.lesson()['notes'], 'Мой конспект')

    def test_deleted_lesson_is_not_resurrected_by_background_result(self):
        def removed(source):
            app.trash('lessons',self.lesson()['id'])
            return REPORT
        self.run_sync(model=removed)
        self.assertTrue(self.lesson()['deleted'])
        self.run_sync()
        self.assertTrue(self.lesson()['deleted'])

    def test_moscow_date_and_unconfirmed_lesson_not_counted(self):
        before=app.metrics(app.all_data())
        self.run_sync()
        self.assertEqual(self.lesson()['date'], '2026-09-18')
        self.assertEqual(app.metrics(app.all_data())['hours'], before['hours'])

    def test_fixed_and_per_lesson_months_do_not_double_charge(self):
        self.run_sync()
        bills=app.all_data()['bills']
        fixed=next(b for b in bills if b['student']=='anna')
        per_lesson=next(b for b in bills if b['student']=='boris')
        app.save('bills',{**per_lesson,'period':'2026-09','calculation':'lessons'})
        l=self.lesson()
        app.save('lessons',{**l,'attendance':'completed'})
        current=self.lesson()
        app.save('lessons',{**current,'attendance':'completed'})
        metrics=app.metrics(app.all_data())
        self.assertEqual(next(b['amount'] for b in metrics['bills'] if b['id']==fixed['id']),fixed['amount'])
        expected=sum(c['amount'] for x in app.all_data()['lessons'] if x['date'][:7]=='2026-09' for c in x['charges'] if c['student']=='boris')
        self.assertEqual(next(b['amount'] for b in metrics['bills'] if b['student']=='boris'),expected)
        app.save('lessons',{**self.lesson(),'attendance':'cancelled'})
        metrics=app.metrics(app.all_data())
        self.assertEqual(next(b['amount'] for b in metrics['bills'] if b['student']=='boris'),expected-1000)

    def test_stale_edit_rejected_including_after_trash(self):
        old=app.all_data()['lessons'][0]
        app.save('lessons',{**old,'notes':'Обновлено'})
        with self.assertRaises(ValueError): app.save('lessons',old)
        current=next(x for x in app.all_data()['lessons'] if x['id']==old['id'])
        app.trash('lessons',current['id'])
        with self.assertRaises(ValueError): app.save('lessons',current)

    def test_repeated_discovery_preserves_backoff(self):
        def failed(source): raise reporting.ReportUnavailable('Недоступно')
        self.run_sync(model=failed)
        with self.store.connect() as db: before=dict(db.execute('SELECT * FROM zoom_jobs').fetchone())
        self.run_sync(model=failed)
        with self.store.connect() as db: after=dict(db.execute('SELECT * FROM zoom_jobs').fetchone())
        self.assertEqual(after['attempts'],before['attempts'])
        self.assertEqual(after['available_at'],before['available_at'])

    def test_expired_running_job_recovers(self):
        self.store.enqueue('test',{'date':'2026-09-18'})
        self.assertIsNotNone(self.store.claim())
        self.assertIsNone(self.store.claim())
        self.make_due()
        self.assertEqual(self.store.claim()['attempts'],2)

    def test_worker_runs_immediately_without_browser(self):
        stop=threading.Event()
        class Wake:
            def wait(self, interval): stop.set()
            def clear(self): pass
        with patch.object(zoom_sync,'WAKE',Wake()), patch.object(zoom_sync,'config',return_value=self.config), patch.object(zoom_sync,'sync') as sync:
            zoom_sync.worker(app.all_data,app.save,self.store,stop)
            sync.assert_called_once_with(app.all_data,app.save,self.store)


class SubscriptionTests(unittest.TestCase):
    def test_second_server_cannot_share_port(self):
        with app.LocalHTTPServer(('127.0.0.1',0),app.Handler) as server:
            with self.assertRaises(OSError):
                duplicate=app.LocalHTTPServer(server.server_address,app.Handler)
                duplicate.server_close()

    def test_api_login_never_starts_generation(self):
        with patch('reporting.shutil.which',return_value='codex'), patch('reporting.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='Logged in using API key')) as run:
            with self.assertRaises(reporting.ReportUnavailable): reporting.codex_report('Векторы')
            self.assertEqual(run.call_count,1)

    def test_subscription_invocation_has_no_api_keys_or_shell(self):
        calls=[]
        def fake(command, **kwargs):
            calls.append((command,kwargs))
            if command[1:]==['login','status']:
                return SimpleNamespace(returncode=0,stdout='',stderr='Logged in using ChatGPT')
            Path(command[command.index('-o')+1]).write_text(json.dumps(REPORT),encoding='utf-8')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        with patch('reporting.shutil.which',return_value='codex'), patch('reporting.subprocess.run',side_effect=fake), patch.dict(os.environ,{'OPENAI_API_KEY':'secret','CODEX_API_KEY':'secret','ZOOM_SECRET':'secret'}):
            result=reporting.codex_report('Векторы')
        command,kwargs=calls[-1]
        self.assertEqual(result['notes'],REPORT['notes'])
        self.assertIn('forced_login_method="chatgpt"',command)
        self.assertIn('features.shell_tool=false',command)
        self.assertIn('read-only',command)
        for key in ('OPENAI_API_KEY','CODEX_API_KEY','ZOOM_SECRET'): self.assertNotIn(key,kwargs['env'])
        self.assertFalse(kwargs.get('shell',False))


if __name__=='__main__': unittest.main()
