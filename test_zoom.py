import unittest
from unittest.mock import patch
import tempfile, datetime
from pathlib import Path
import app, zoom_sync

class ZoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); app.DB=Path(self.tmp.name)/'test.db';app.seed()
        s=next(x for x in app.all_data()['groups'] if x['id']=='ege');s['zoom_id']='123';app.save('groups',s)
    def tearDown(self): self.tmp.cleanup()
    def test_import_deduplicates_recording_uuid(self):
        def fake(url,data=None,headers=None):
            if 'oauth/token' in url: return {'access_token':'test'}
            if '/recordings?' in url:return {'meetings':[{'id':123,'uuid':'abc/123','start_time':'2026-09-15T09:00:00Z','duration':60,'recording_files':[{'status':'completed'}],'share_url':'https://zoom.us/rec/share/test'}]}
            if '/past_meetings/' in url:return {'participants_count':3,'duration':60}
            return {'summary_content':'Изучали дроби.'}
        with patch.object(zoom_sync,'config',return_value={'account_id':'x','client_id':'x','client_secret':'x','user_id':'x','provider':'none'}),patch.object(zoom_sync,'request',side_effect=fake):
            zoom_sync.sync(app.all_data,app.save);zoom_sync.sync(app.all_data,app.save)
        lessons=[x for x in app.all_data()['lessons'] if x.get('zoom_uuid')]
        self.assertEqual(len(lessons),1);self.assertEqual(lessons[0]['group'],'ege');self.assertEqual(lessons[0]['notes'],'')
    def test_uuid_encoding(self):
        self.assertEqual(zoom_sync.uuid_path('/abc'),' %252Fabc'.strip())
        self.assertEqual(zoom_sync.uuid_path('abc/def'),'abc%2Fdef')
    def test_legacy_summary_keeps_homework_and_details(self):
        result=zoom_sync.summary_text({'summary_overview':'Дроби','summary_details':[{'label':'Результат','summary':'Сложение освоено'}],'next_steps':['Домашка: 1, 2']})
        self.assertIn('Сложение освоено',result)
        self.assertIn('Домашка: 1, 2',result)
    def test_modern_summary_not_duplicated(self):
        self.assertEqual(zoom_sync.summary_text({'summary_content':'Полная сводка','summary_overview':'Старая сводка'}),'Полная сводка')
    def test_edited_legacy_summary_wins(self):
        self.assertEqual(zoom_sync.summary_text({'summary_overview':'Старое','edited_summary':{'summary_details':'Исправлено','next_steps':['Задание 3']}}),'Исправлено\n\nЗадание 3')
    def test_no_model_does_not_invent_homework(self):
        r=zoom_sync.report('Домашнее задание неизвестно','');self.assertEqual(r['homework'],'Не извлечено')
    def test_invalid_model_report_rejected(self):
        with patch.object(zoom_sync,'request',return_value={'response':'{"topic":"Векторы"}'}):
            with self.assertRaises(ValueError): zoom_sync.report('Векторы','test-model')
    def test_long_summary_not_silently_truncated(self):
        with patch.object(zoom_sync,'request') as request:
            with self.assertRaises(ValueError): zoom_sync.report('а'*9001,'test-model')
            request.assert_not_called()
    def test_lecture_does_not_prove_student_understanding(self):
        response='{"topic":"Векторы","understanding":"Ученик всё усвоил","homework":"Не зафиксировано","next":"Практика","notes":"Координаты вектора"}'
        with patch.object(zoom_sync,'request',return_value={'response':response}):
            result=zoom_sync.report('Преподаватель объяснил координаты вектора. На следующем уроке практика.','test-model')
        self.assertEqual(result['understanding'],'Не зафиксировано')

if __name__=='__main__':unittest.main()
