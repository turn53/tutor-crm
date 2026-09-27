import hashlib
import json
from pathlib import Path
import unittest
import app
from deploy.replace_lesson_reports import replace, queue_report_upgrades
import local_reports
import test_automation


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_automation.AutomationTests()
        self.fixture.setUp()
        self.fixture.run_sync()
        self.folder = Path(self.fixture.tmp.name)/'evaluated'
        self.folder.mkdir()
        self.lesson = self.fixture.lesson()
        self.source = 'Ученик: самостоятельное решение задачи.'
        self.case = {**self.lesson, 'raw_transcript': self.source}
        (self.folder/'cases.json').write_text(json.dumps([self.case]), 'utf-8')
        self.result = {'report': {**test_automation.REPORT, 'notes': 'Новый проверенный конспект'},
                       'analysis': {'source_hash': hashlib.sha256(self.source.encode()).hexdigest(),
                                    'version': local_reports.VERSION, 'automatic_check': 'passed', 'check_scope':'facts_and_notes'}}
        self.write_result()

    def tearDown(self):
        self.fixture.tearDown()

    def write_result(self):
        (self.folder/(self.lesson['id']+'.json')).write_text(json.dumps(self.result), 'utf-8')

    def test_replacement_preserves_accounting_and_keeps_original_history(self):
        before = app.all_data()
        metrics = app.metrics(before)
        result = replace(self.folder)
        self.assertEqual(result['changed'], [self.lesson['id']])
        after = app.all_data()
        self.assertEqual(app.metrics(after), metrics)
        for kind in app.KINDS-{'lessons'}:
            self.assertEqual(before[kind], after[kind])
        self.assertNotIn('raw_transcript', self.fixture.lesson())
        with app.connect() as db:
            original = json.loads(db.execute('SELECT data FROM report_history').fetchone()[0])
            payload = json.loads(db.execute('SELECT payload FROM zoom_jobs').fetchone()[0])
            self.assertEqual(payload['transcript'], self.source)
        self.assertEqual(original, self.lesson)
        self.assertEqual(replace(self.folder)['changed'], [])

    def test_manual_changes_after_snapshot_are_never_overwritten(self):
        app.save('lessons', {**self.lesson, 'homework': 'Задание исправлено преподавателем'})
        before = app.all_data()
        with self.assertRaises(ValueError):
            replace(self.folder)
        self.assertEqual(app.all_data(), before)

    def test_unchecked_output_cannot_replace_live_report(self):
        self.result['analysis'].pop('automatic_check')
        self.write_result()
        before = app.all_data()
        with self.assertRaises(ValueError):
            replace(self.folder)
        self.assertEqual(app.all_data(), before)

    def test_reports_received_during_rehearsal_are_queued_without_erasing_text_or_charges(self):
        before=app.all_data()
        result=queue_report_upgrades(self.lesson['date'],provider='codex')
        self.assertEqual(result['queued'],[self.lesson['id']])
        self.assertEqual(app.metrics(app.all_data()),app.metrics(before))
        after=self.fixture.lesson()
        self.assertEqual(after['notes'],self.lesson['notes'])
        self.assertEqual(after['charges'],self.lesson['charges'])
        task=self.fixture.store.claim()
        self.assertNotIn('result',task['payload'])
        self.assertEqual(task['payload']['summary'],self.fixture.source)
        self.assertEqual(queue_report_upgrades(self.lesson['date'],provider='codex')['queued'],[])

    def test_upgrade_queue_does_not_touch_manual_report_edits(self):
        app.save('lessons',{**self.lesson,'homework':'Ручное задание'})
        before=app.all_data()
        result=queue_report_upgrades(self.lesson['date'],provider='codex')
        self.assertEqual(result['queued'],[])
        self.assertEqual(result['skipped'],[self.lesson['id']])
        self.assertEqual(app.all_data(),before)
