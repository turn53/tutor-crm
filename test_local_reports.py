import copy
import io
import json
import os
import unittest
from unittest.mock import patch

import app
import local_reports as local
import test_automation
import zoom_sync

SOURCE = 'Изучали теорему Пифагора.\n\nУченик самостоятельно решил задачу.\n\nДомашнее задание: № 12.\n\nСледующий урок: практика.'
FACTS = {'topics': [{'text': 'Теорема Пифагора', 'source_ids': [1]}],
         'homework': [{'text': 'Решить № 12', 'source_ids': [3]}],
         }
TEXT = ('В прямоугольном треугольнике квадрат гипотенузы равен сумме квадратов катетов: c² = a² + b². '
        'Гипотенуза лежит напротив прямого угла. Формула применяется только к прямоугольному треугольнику. '
        'Если известны катеты a и b, гипотенузу находят по формуле c = √(a² + b²). '
        'Если известны гипотенуза c и катет a, второй катет равен b = √(c² − a²), причём c > a > 0. '
        'Сначала определяют гипотенузу и катеты, затем выбирают формулу и выполняют вычисления. '
        'Длины сторон должны быть выражены в одних единицах.')
NOTES = {'sections': [{'title': 'Теорема Пифагора', 'text': TEXT}]}


class LocalReportTests(unittest.TestCase):
    def test_topic_heading_does_not_publish_unchecked_definition(self):
        facts = {'topics':[{'text':'Математическое ожидание как среднее арифметическое выборки.', 'source_ids':[1]}], 'homework':[]}
        checked = local.validate_facts(facts,['Обсудили математическое ожидание.'])
        self.assertEqual(checked['topics'][0]['text'],'Математическое ожидание')

    def test_empty_filtered_topics_recover_without_repeating_full_extraction(self):
        import hashlib
        cached = {'version':local.VERSION,'source_hash':hashlib.sha256(SOURCE.encode()).hexdigest(),
                  'extracted':[{'topics':[],'homework':copy.deepcopy(FACTS['homework'])}]}
        with patch.object(local,'call_model',side_effect=[{'topics':FACTS['topics']}, NOTES, {'ok':True,'issues':[]}]) as model:
            report, _ = local.local_report(SOURCE,cached)
        self.assertEqual(model.call_count,3)
        self.assertEqual(model.call_args_list[0].args[0],local.THEMES_PROMPT)
        self.assertTrue(model.call_args_list[0].args[1]['SOURCE'])
        self.assertEqual(report['homework'],'Решить № 12')

    def test_action_owner_is_preserved_and_teacher_errands_excluded(self):
        source = '## Следующие шаги\n\n### Альберт\n\n- Повторить задачи 2 и 3.\n\n### Вячеслав\n\n- Отправить Альберту запись.\n\n## Сводка\n\nОбсудили дисперсию.'
        numbered = local.source_context(local.paragraphs(source))
        result = local.explicit_homework(numbered, 'Вячеслав')
        self.assertEqual(result, [{'text': 'Альберт: Повторить задачи 2 и 3.', 'source_ids': [3]}])
        self.assertEqual(numbered[4]['actor'], 'Вячеслав')

    def test_source_grounded_report_and_private_analysis(self):
        with patch.object(local, 'call_model', side_effect=[FACTS, NOTES, {'ok': True, 'issues': []}]):
            report, analysis = local.local_report(SOURCE)
        self.assertEqual(report['homework'], 'Решить № 12')
        self.assertEqual(report['understanding'], '')
        self.assertEqual(report['next'], '')
        self.assertNotIn('Самостоятельно', report['notes'])
        self.assertNotIn('source_ids', report['notes'])
        self.assertTrue(analysis['evidence'])

    def test_math_comparisons_are_not_rejected_as_html(self):
        notes = {'sections':[{'title':'Теорема Пифагора','text':TEXT+' При -1 < x < 1 значение y > 0.'}]}
        with patch.object(local, 'call_model', side_effect=[FACTS, notes, {'ok': True, 'issues': []}]):
            report, _ = local.local_report(SOURCE)
        self.assertIn('-1 < x < 1', report['notes'])

    def test_invented_homework_number_rejected(self):
        facts = copy.deepcopy(FACTS)
        facts['homework'][0]['text'] = 'Решить № 17'
        self.assertEqual(local.validate_facts(facts, local.paragraphs(SOURCE))['homework'], [])

    def test_invented_homework_number_in_words_is_omitted(self):
        facts = copy.deepcopy(FACTS)
        facts['homework'][0]['text'] = 'Решить семнадцатое задание'
        self.assertEqual(local.validate_facts(facts,local.paragraphs(SOURCE))['homework'],[])

    def test_spoken_task_numbers_match_digits(self):
        self.assertEqual(local.number_values('десятое, одиннадцатое и двенадцатое'), {'10', '11', '12'})
        self.assertEqual(local.number_values('пятнадцатое, шестнадцатое и восемнадцатое в пятницу'), {'15', '16', '18'})

    def test_transcript_preferred_without_waiting_for_summary(self):
        f = test_automation.AutomationTests(); f.setUp()
        try:
            f.config['provider'] = 'local'
            def request(url, data=None, headers=None):
                if url.endswith('/recordings'):
                    return {'recording_files': [{'recording_type': 'audio_transcript'}]}
                if 'meeting_summary' in url:
                    raise AssertionError('Summary must not be required when original speech is available')
                return f.fake(url, data, headers)
            with patch.object(zoom_sync, 'config', return_value=f.config), patch.object(zoom_sync, 'request', side_effect=request), \
                 patch.object(zoom_sync, 'download_transcripts', return_value=SOURCE), \
                 patch.object(zoom_sync, 'local_report', return_value=(test_automation.REPORT, {})) as model:
                zoom_sync.sync(app.all_data, app.save, f.store, app.resolve_zoom)
                self.assertEqual(model.call_args.args[0], SOURCE)
                self.assertNotIn('raw_transcript', f.lesson())
                with f.store.connect() as db:
                    payload = json.loads(db.execute('SELECT payload FROM zoom_jobs').fetchone()[0])
                self.assertEqual(payload['transcript'], SOURCE)
                self.assertEqual(f.lesson()['report_analysis']['source_kind'], 'transcript')
        finally:
            f.tearDown()

    def test_nonexistent_evidence_rejected(self):
        facts = copy.deepcopy(FACTS)
        facts['topics'][0]['source_ids'] = [100]
        with self.assertRaises(local.ReportUnavailable):
            local.validate_facts(facts, local.paragraphs(SOURCE))

    def test_missing_outcomes_are_not_invented(self):
        facts = copy.deepcopy(FACTS)
        facts.update(homework=[])
        with patch.object(local, 'call_model', side_effect=[facts, NOTES, {'ok': True, 'issues': []}]):
            report, _ = local.local_report(SOURCE)
        self.assertEqual(report['understanding'], '')
        self.assertIn('не удалось определить', report['homework'])

    def test_no_cloud_endpoint_can_receive_source(self):
        with patch.dict(os.environ, {'CRM_LOCAL_MODEL_URL': 'https://some-model.example'}), \
             patch('urllib.request.build_opener') as network:
            with self.assertRaises(local.ReportUnavailable):
                local.call_model('prompt', {'source': SOURCE}, local.NOTES_SCHEMA)
            network.assert_not_called()

    def test_cached_extraction_survives_generation_failure(self):
        saved = []
        with patch.object(local, 'call_model', side_effect=[FACTS, local.ReportUnavailable('retry')]):
            with self.assertRaises(local.ReportUnavailable):
                local.local_report(SOURCE, checkpoint=lambda work: saved.append(copy.deepcopy(work)))
        with patch.object(local, 'call_model', side_effect=[NOTES, {'ok': True, 'issues': []}]) as model:
            report, _ = local.local_report(SOURCE, saved[-1])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(report['homework'], 'Решить № 12')

    def test_changed_source_invalidates_cached_facts(self):
        cached = {'version': local.VERSION, 'source_hash': 'old', 'facts': FACTS, 'notes': NOTES}
        with patch.object(local, 'call_model', side_effect=[FACTS, NOTES, {'ok': True, 'issues': []}]) as model:
            local.local_report(SOURCE, cached)
        self.assertEqual(model.call_count, 3)

    def test_model_reasoning_is_not_published_as_report(self):
        response = {'choices':[{'finish_reason':'stop', 'message':{
            'content':json.dumps(FACTS), 'reasoning_content':'Internal deliberation'}}]}
        with patch.object(local.urllib.request, 'build_opener') as build:
            build.return_value.open.return_value = io.BytesIO(json.dumps(response).encode())
            result = local.call_model(local.AUDIT_PROMPT, {'SOURCE':SOURCE}, local.EXTRACT_SCHEMA, 1800)
            payload = json.loads(build.return_value.open.call_args.args[0].data)
        self.assertEqual(result,FACTS)
        self.assertTrue(payload['chat_template_kwargs']['enable_thinking'])
        self.assertGreater(payload['max_tokens'],1800)

    def test_compact_model_input_preserves_full_speech_and_roles(self):
        parts = ['Вячеслав: Понял определение?', 'Ученик: Не понял, почему вероятности складываются.']
        numbered = local.source_context(parts)
        text = local.model_input({'TEACHER':'Вячеслав','SOURCE':numbered})
        self.assertIn('[1] teacher Вячеслав:',text)
        self.assertIn('[2] pupil Ученик:',text)
        for p in numbered:self.assertIn(p['text'],text)
        self.assertLess(len(text),len(json.dumps({'TEACHER':'Вячеслав','SOURCE':numbered},ensure_ascii=False)))

    def test_invented_illustration_is_removed_but_theory_survives(self):
        text = 'Вероятность равна m/n для равновозможных исходов. Примером служит выпадение числа при броске монеты. Число исходов конечно.'
        clean = local.theory_text(text)
        self.assertNotIn('монеты',clean)
        self.assertIn('m/n',clean)
        self.assertIn('Число исходов конечно.',clean)

    def test_unsolicited_proof_is_removed_without_losing_theorem(self):
        text = 'Медиана к гипотенузе равна её половине: m=c/2. Для доказательства используется неверное утверждение про вписанную окружность. Гипотенуза якобы её диаметр.'
        self.assertEqual(local.theory_text(text),'Медиана к гипотенузе равна её половине: m=c/2.')

    def test_extra_chapter_cannot_become_ready(self):
        notes = {'sections':[{'title':'Условная вероятность','text':TEXT}]}
        saved = []
        with patch.object(local,'call_model',side_effect=[FACTS,notes]) as model:
            with self.assertRaises(local.ReportUnavailable):
                local.local_report(SOURCE,checkpoint=lambda w:saved.append(copy.deepcopy(w)))
        self.assertNotIn('notes',saved[-1])
        titles = model.call_args_list[1].args[2]['properties']['sections']['items']['properties']['title']['enum']
        self.assertEqual(titles,['Теорема Пифагора'])

    def test_placeholder_cannot_become_ready(self):
        with patch.object(local, 'call_model', side_effect=[FACTS, {'sections': [{'title': 'Тема', 'text': 'Не определено'}]}]):
            with self.assertRaises(local.ReportUnavailable):
                local.local_report(SOURCE)

    def test_tracking_links_are_removed_before_inference(self):
        result = local.paragraphs('## Ученик\n\n- [Решить № 12](https://tasks.zoom.us/?secret=private)')
        self.assertNotIn('https', ' '.join(result))
        self.assertIn('Решить № 12', ' '.join(result))

    def test_failed_content_check_retries_notes_without_losing_facts(self):
        saved = []
        with patch.object(local, 'call_model', side_effect=[FACTS, NOTES, {'ok': False, 'issues': ['Уточнить условие']}]):
            with self.assertRaises(local.ReportUnavailable):
                local.local_report(SOURCE, checkpoint=lambda work: saved.append(copy.deepcopy(work)))
        self.assertEqual(saved[-1]['facts'], FACTS)
        self.assertNotIn('notes', saved[-1])
        self.assertNotIn('audit', saved[-1])
        with patch.object(local, 'call_model', side_effect=[NOTES, {'ok': True, 'issues': []}]) as model:
            _, analysis = local.local_report(SOURCE, saved[-1])
        self.assertEqual(model.call_count, 2)
        self.assertEqual(analysis['automatic_check'], 'passed')

    def test_uncertain_homework_does_not_block_valid_notes(self):
        with patch.object(local, 'call_model', side_effect=[FACTS, NOTES, {'ok': False, 'issues': ['FACT: Поручение учителю не домашка']}]) as model:
            report, analysis = local.local_report(SOURCE)
        self.assertEqual(model.call_count,3)
        self.assertIn('не удалось определить',report['homework'])
        self.assertIn('Пифагора',report['notes'])
        self.assertTrue(analysis['warnings'])
        self.assertEqual(analysis['automatic_check'],'passed')

    def test_missing_homework_evidence_does_not_discard_valid_topic(self):
        facts = copy.deepcopy(FACTS)
        facts['homework'][0]['source_ids'] = [999]
        self.assertEqual(local.validate_facts(facts,local.paragraphs(SOURCE))['homework'],[])
        self.assertEqual(len(facts['topics']),1)

    def test_long_source_paragraphs_preserve_all_text(self):
        source = ('Ученик решал уравнение. '*200).strip()
        parts = local.paragraphs(source)
        self.assertTrue(all(len(x)<=2000 for x in parts))
        self.assertEqual(' '.join(parts), source)

    def test_unspoken_technical_topic_is_omitted(self):
        source = ['Разбирали простую вероятность и перестановки участников.']
        facts = {'topics':[{'text':'Вероятность','source_ids':[1]},
                           {'text':'Условная вероятность','source_ids':[1]}], 'homework':[]}
        result = local.validate_facts(facts,source)
        self.assertEqual([x['text'] for x in result['topics']],['Вероятность'])
        self.assertTrue(local.topic_supported('Перестановки',source))
        self.assertTrue(local.topic_supported('Дифференциальные уравнения',['Решали дифференциальное уравнение.']))

    def test_duplicate_topics_keep_one_title_and_both_sources(self):
        items = [{'text':'Вероятность','source_ids':[1]}, {'text':'Простая вероятность','source_ids':[2]}]
        self.assertEqual(local.distinct_topics(items),[{'text':'Простая вероятность','source_ids':[1,2]}])

    def test_optional_homework_cannot_become_required(self):
        facts = copy.deepcopy(FACTS)
        facts['homework'] = [{'text':'Решить новый вариант.', 'source_ids':[1]}]
        source = ['Учитель: По домашке можно вариант, на твое, усмотрение.']
        self.assertTrue(local.detail_issues(facts, source))
        facts['homework'][0]['text'] = 'По желанию решить новый вариант.'
        self.assertEqual(local.detail_issues(facts, source), [])
        facts['homework'][0]['text'] = 'Решить новый вариант.'
        self.assertEqual(local.detail_issues(facts, ['Домашка: обязательно решить вариант, способ на твое усмотрение.']), [])

    def test_simplified_schema_never_requests_understanding_or_next(self):
        self.assertEqual(set(local.EXTRACT_SCHEMA['properties']), {'topics','homework'})
        self.assertNotIn('observations',local.AUDIT_PROMPT)

    def test_interrupted_job_resumes_without_waiting_two_hours(self):
        f = test_automation.AutomationTests(); f.setUp()
        try:
            f.store.enqueue('job', {'local_work': {'facts': FACTS}})
            f.store.claim()
            self.assertIsNone(f.store.claim())
            f.store.recover_interrupted()
            task = f.store.claim()
            self.assertEqual(task['payload']['local_work']['facts'], FACTS)
        finally:
            f.tearDown()

    def test_pipeline_uses_local_model_and_keeps_charges(self):
        f = test_automation.AutomationTests(); f.setUp()
        try:
            f.config['provider'] = 'local'
            with patch.object(zoom_sync, 'config', return_value=f.config), \
                 patch.object(zoom_sync, 'request', side_effect=f.fake), \
                 patch.object(zoom_sync, 'local_report', return_value=(test_automation.REPORT, {'version': local.VERSION, 'evidence': {}, 'warnings': []})), \
                 patch.object(zoom_sync, 'codex_report') as codex, patch.object(zoom_sync, 'report') as ollama:
                before = app.metrics(app.all_data())
                zoom_sync.sync(app.all_data, app.save, f.store, app.resolve_zoom)
                self.assertEqual(app.metrics(app.all_data()), before)
                self.assertEqual(f.lesson()['report_provider'], 'local')
                self.assertEqual(f.lesson()['report_state'], 'ready')
                codex.assert_not_called(); ollama.assert_not_called()
        finally:
            f.tearDown()


if __name__ == '__main__':
    unittest.main()
