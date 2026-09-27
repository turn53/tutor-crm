import unittest
from unittest.mock import patch
from rule_reports import rule_report, analyze_source
import test_automation
import app
import zoom_sync


class RuleReportsTests(unittest.TestCase):
    def test_narrow_reference_and_explicit_assignments(self):
        result = rule_report('Изучали координаты вектора.\nДомашнее задание: № 5–7\nСледующий урок: Скалярное произведение')
        self.assertIn('x₂ − x₁', result['notes'])
        self.assertNotIn('cos φ', result['notes'])
        self.assertEqual(result['homework'], '№ 5–7')
        self.assertEqual(result['next'], 'Скалярное произведение')
        self.assertIn('Не зафиксировано', result['understanding'])

    def test_unknown_topic_no_invented_material(self):
        result = rule_report('Обсуждали строение клетки. Учитель объяснил материал.')
        self.assertEqual('Теоретические сведения не определены.', result['notes'])
        self.assertEqual(result['homework'], 'Не зафиксировано')
        self.assertNotIn('Учитель', result['notes'])

    def test_negative_and_future_mentions_not_covered(self):
        for source in ('Скалярное произведение не разбирали.', 'Будем изучать скалярное произведение.', 'Домашнее задание: повторить скалярное произведение'):
            self.assertNotIn('cos φ', rule_report(source)['notes'])

    def test_markdown_sections_and_other_heading(self):
        result = rule_report('## Домашнее задание\n№ 12\n## Организация\nНаписать родителю')
        self.assertEqual(result['homework'], '№ 12')
        self.assertNotIn('родителю', result['notes'])

    def test_zoom_blank_lines_and_markdown_task_links(self):
        source = '## Краткое резюме\n\nИзучали координаты вектора.\n\n## Следующие шаги\n\n### Ученик\n\n- [Выполните домашнее задание по второму и четвертому типам задач по векторам, отправьте преподавателю по мере выполнения.](https://tasks.zoom.us/test)\n\n### Учитель\n- Подготовить домашнее задание по теореме Пифагора.\n\n## Сводка\n\nИзучали длину вектора.'
        result = rule_report(source)
        self.assertIn('по второму и четвертому типам', result['homework'])
        self.assertNotIn('https', result['homework'])
        self.assertNotIn('отправьте', result['homework'])
        self.assertNotIn('Пифагора', result['notes'])
        self.assertIn('Длина вектора', result['notes'])

    def test_narrative_homework_and_no_future_assignment(self):
        self.assertEqual(rule_report('На дом задали решить № 12–14.')['homework'], 'решить № 12–14.')
        self.assertEqual(rule_report('Учитель пообещал выдать домашнее задание позже.')['homework'], 'Не зафиксировано')
        self.assertEqual(rule_report('На дом не задали решить № 12.')['homework'], 'Не зафиксировано')

    def test_geometry_and_clean_student_notes(self):
        result = rule_report('Обсудили прямые линии, отрезки и лучи. Изучали прямые углы и биссектрисы.')
        self.assertIn('90°', result['notes'])
        self.assertIn('Биссектриса', result['notes'])
        for phrase in ('Черновик', 'Zoom', 'проверь', 'по словам', 'не восстановлены'):
            self.assertNotIn(phrase, result['notes'])

    def test_blank_line_after_explicit_heading(self):
        result = rule_report('## Домашнее задание\n\n№ 12\n\n## Организация\nНаписать родителю')
        self.assertEqual(result['homework'], '№ 12')

    def test_private_evidence_not_student_notes(self):
        source = 'Преподаватель объяснил теорему Виета. Ученик ошибался.'
        self.assertIn('Преподаватель', analyze_source(source)['evidence']['Теорема Виета'])
        self.assertNotIn('Преподаватель', rule_report(source)['notes'])
        self.assertEqual(rule_report(source)['understanding'], 'Не зафиксировано')

    def test_future_and_homework_sections_do_not_prove_coverage(self):
        for source in ('Следующий урок:\n\nТеорема Виета', '## Следующие шаги\n\n### Ученик\nПовторить теорему Виета', 'Теорему Виета не изучали.'):
            self.assertNotIn('x₁ + x₂', rule_report(source)['notes'])


class RulesIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_automation.AutomationTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_rules_pipeline_never_calls_model_or_subprocess(self):
        f = self.fixture
        f.config['provider'] = 'rules'
        with patch.object(zoom_sync, 'config', return_value=f.config), patch.object(zoom_sync, 'request', side_effect=f.fake), patch.object(zoom_sync, 'codex_report') as codex, patch.object(zoom_sync, 'report') as ollama, patch('reporting.subprocess.run') as process:
            zoom_sync.sync(app.all_data, app.save, f.store)
        codex.assert_not_called()
        ollama.assert_not_called()
        process.assert_not_called()
        lesson = f.lesson()
        self.assertEqual(lesson['report_provider'], 'rules')
        self.assertEqual(lesson['raw_summary'], f.source)
        self.assertEqual(lesson['attendance'], 'needs_confirmation')
        self.assertFalse(lesson['reviewed'])
        self.assertIn('x₂ − x₁', lesson['notes'])
        self.assertEqual(lesson['report_analysis']['version'], 'rules-2')
        self.assertTrue(lesson['report_analysis']['evidence'])


if __name__ == '__main__': unittest.main()
