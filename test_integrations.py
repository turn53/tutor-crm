import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch
import app
import backup_service
import calendar_sync as cal
import google_services as google
import video_sync as video
import zoom_sync
import test_automation


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.previous=app.DB
        app.DB=Path(self.tmp.name)/'test.sqlite3'; app.seed()
        self.event={'id':'ev1','summary':'Математика','start':{'dateTime':'2026-09-18T10:00:00+03:00'},
            'end':{'dateTime':'2026-09-18T11:00:00+03:00'},'recurringEventId':'series','etag':'v1'}
        cal.import_event(app,'primary',self.event)

    def tearDown(self): app.DB=self.previous; self.tmp.cleanup()
    def item(self): return next(x for x in app.all_data()['schedule'] if x['event_id']=='ev1')

    def test_planned_not_billed_completed_idempotent_cancel_reverses(self):
        before=app.metrics(app.all_data())['hours']
        cal.bind(app,self.item()['id'],student='vera')
        self.assertEqual(app.metrics(app.all_data())['hours'],before)
        cal.attendance(app,self.item()['id'],'completed')
        cal.attendance(app,self.item()['id'],'completed')
        self.assertEqual(app.metrics(app.all_data())['hours'],before+1)
        self.assertEqual(len([x for x in app.all_data()['lessons'] if x.get('calendar_key')]),1)
        cal.attendance(app,self.item()['id'],'cancelled')
        self.assertEqual(app.metrics(app.all_data())['hours'],before)

    def test_series_binding_applies_to_new_occurrences(self):
        cal.bind(app,self.item()['id'],group='ege',series=True)
        cal.import_event(app,'primary',{**self.event,'id':'ev2'})
        self.assertTrue(all(x['group']=='ege' for x in app.all_data()['schedule']))

    def test_completed_move_preserves_financial_history_and_flags_conflict(self):
        cal.bind(app,self.item()['id'],student='vera');cal.attendance(app,self.item()['id'],'completed')
        lesson=next(x for x in app.all_data()['lessons'] if x.get('calendar_key'))
        cal.import_event(app,'primary',{**self.event,'start':{'dateTime':'2026-09-19T10:00:00+03:00'}})
        self.assertTrue(self.item()['needs_review'])
        self.assertEqual(next(x for x in app.all_data()['lessons'] if x['id']==lesson['id']),lesson)

    def test_cancelled_remote_event_does_not_erase_paid_history(self):
        cal.bind(app,self.item()['id'],student='vera');cal.attendance(app,self.item()['id'],'completed')
        before=app.metrics(app.all_data())['hours']
        cal.import_event(app,'primary',{'id':'ev1','status':'cancelled'})
        self.assertTrue(self.item()['needs_review']);self.assertEqual(app.metrics(app.all_data())['hours'],before)

    def test_description_keeps_original_and_replaces_only_crm_block(self):
        original='Ссылка и заметки владельца'
        first=cal.description(original,'Первый отчёт')
        second=cal.description(first,'Исправленный отчёт')
        self.assertIn(original,second);self.assertNotIn('Первый отчёт',second)
        self.assertEqual(second.count(cal.START),1)

    def test_binding_cannot_change_completed_participants(self):
        cal.bind(app,self.item()['id'],student='vera');cal.attendance(app,self.item()['id'],'completed')
        with self.assertRaises(ValueError): cal.bind(app,self.item()['id'],student='anna')

    def test_only_public_notes_exported(self):
        text=cal.public_notes({'topic':'Векторы','notes':'Формула','homework':'№ 2','understanding':'PRIVATE','raw_summary':'PRIVATE'})
        self.assertNotIn('PRIVATE',text)

    def test_backup_restore_integrity_and_records(self):
        file=backup_service.backup(app.DB,Path(self.tmp.name)/'backups')
        old=app.all_data();app.DB=file
        self.assertEqual(app.all_data(),old)

    def test_calendar_sync_publishes_after_completion_with_etag(self):
        cal.bind(app,self.item()['id'],student='vera');cal.attendance(app,self.item()['id'],'completed')
        l=next(x for x in app.all_data()['lessons'] if x.get('calendar_key'));app.save('lessons',{**l,'notes':'Краткая теория'})
        def fake(service,path,**kw):
            if 'timeMin=' in path:return {'items':[self.event]}
            if kw.get('method')=='PATCH':
                self.assertEqual(kw['headers']['If-Match'],'v1');self.assertIn('Старая запись',kw['body']['description']);return {}
            return {**self.event,'description':'Старая запись'}
        with patch.object(google,'config',return_value={'calendar_enabled':True,'publish_notes':True}),patch.object(google,'access_token',return_value='fake'),patch.object(google,'api',side_effect=fake) as api:
            cal.sync(app);cal.sync(app)
            self.assertEqual(sum(x.kwargs.get('method')=='PATCH' for x in api.call_args_list),1)


class ZoomCalendarTests(unittest.TestCase):
    def test_zoom_fills_existing_calendar_lesson_without_double_charge(self):
        f=test_automation.AutomationTests();f.setUp()
        try:
            cal.import_event(app,'primary',{'id':'event','summary':'Урок','start':{'dateTime':'2026-09-18T01:30:00+03:00'},'end':{'dateTime':'2026-09-18T02:30:00+03:00'}})
            item=app.all_data()['schedule'][0];cal.bind(app,item['id'],group='ege');cal.attendance(app,item['id'],'completed')
            before=app.metrics(app.all_data())['hours']; f.run_sync()
            self.assertEqual(app.metrics(app.all_data())['hours'],before)
            lesson=f.lesson();self.assertEqual(lesson['calendar_key'],item['id']);self.assertEqual(lesson['report_state'],'ready')
            self.assertEqual(len([l for l in app.all_data()['lessons'] if l.get('calendar_key') or l.get('zoom_uuid')]),1)
        finally:f.tearDown()


class OAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.prev=app.DB;self.prevconfig=google.CONFIG
        app.DB=Path(self.tmp.name)/'db.sqlite3';google.CONFIG=Path(self.tmp.name)/'google.local.json'
        google.update({'client_id':'id','client_secret':'secret'})
    def tearDown(self):app.DB=self.prev;google.CONFIG=self.prevconfig;self.tmp.cleanup()
    def test_callback_requires_same_browser_state_and_is_single_use(self):
        url,state=google.begin('calendar','http://localhost',app.connect)
        self.assertIn('code_challenge=',url)
        with self.assertRaises(ValueError):google.complete({'state':[state],'code':['code']},'wrong','http://localhost',app.connect)
        with patch.object(google,'token_request',return_value={'refresh_token':'secret-refresh-value','scope':' '.join(google.SCOPES['calendar'])}):
            google.complete({'state':[state],'code':['code']},state,'http://localhost',app.connect)
            with self.assertRaises(ValueError):google.complete({'state':[state]},state,'http://localhost',app.connect)
        self.assertNotIn('secret-refresh-value',json.dumps(google.status()))
        self.assertNotIn('secret-refresh-value',json.dumps(app.all_data()))


class VideoTests(unittest.TestCase):
    def test_one_video_layout_per_segment(self):
        files=[{'id':str(i),'recording_start':'same','file_type':'MP4','status':'completed','recording_type':kind} for i,kind in enumerate(['gallery_view','shared_screen_with_speaker_view'])]
        self.assertEqual(video.select_files(files)[0]['id'],'1')
    def test_resumable_upload_recovers_lost_final_response_without_second_insert(self):
        job={'payload':{'session':'https://www.googleapis.com/upload/test','size':100},'id':'x','attempts':1,'status':'uploading'}
        with patch.object(google,'request',return_value=(200,{}, {'id':'video123'})) as request,patch.object(video,'put'):
            video.upload(None,job,Path('does-not-exist'),'token','private')
        self.assertEqual(request.call_count,1);self.assertEqual(job['payload']['video_id'],'video123')
    def test_upload_resumes_from_acknowledged_byte(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'video.mp4';path.write_bytes(b'abcdefgh')
            job={'payload':{'session':'https://www.googleapis.com/upload/test','size':8},'id':'x','attempts':1,'status':'uploading'}
            with patch.object(google,'request',side_effect=[(308,{'Range':'bytes=0-3'},{}),(200,{}, {'id':'video123'})]) as req,patch.object(video,'put'):
                video.upload(None,job,path,'token','private')
            self.assertEqual(req.call_args.kwargs['body'],b'efgh');self.assertEqual(req.call_args.kwargs['headers']['Content-Range'],'bytes 4-7/8')
    def test_google_transport_refuses_external_session_url(self):
        with self.assertRaises(ValueError):google.request('youtube','https://attacker.invalid/upload',token='secret')


class GatewayTests(unittest.TestCase):
    def test_gateway_blocks_app_page_without_gateway_secret(self):
        with patch.object(app,'GATEWAY','x'*40),patch.object(app,'ORIGIN','https://crm.example.test'):
            with app.LocalHTTPServer(('127.0.0.1',0),app.Handler) as server:
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                url='http://127.0.0.1:'+str(server.server_port)+'/'
                try:
                    with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(urllib.request.Request(url,headers={'Host':'crm.example.test'}))
                    self.assertEqual(caught.exception.code,403)
                    req=urllib.request.Request(url,headers={'Host':'crm.example.test','X-CRM-Gateway':'x'*40})
                    with urllib.request.urlopen(req) as r:self.assertEqual(r.status,200)
                finally:server.shutdown();thread.join()

if __name__=='__main__':unittest.main()
