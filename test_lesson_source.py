import unittest
import datetime
from unittest.mock import patch
import lesson_source
from reporting import ReportUnavailable


class TranscriptTests(unittest.TestCase):
    def test_missing_transcript_is_given_time_to_appear_after_recording(self):
        payload={'start_time':'2026-09-22T10:00:00+03:00','minutes':65}
        early=datetime.datetime.fromisoformat('2026-09-22T11:15:00+03:00')
        late=datetime.datetime.fromisoformat('2026-09-22T12:10:00+03:00')
        self.assertTrue(lesson_source.awaiting_transcript(payload,early))
        self.assertFalse(lesson_source.awaiting_transcript(payload,late))

    def test_cues_are_removed_but_speakers_and_homework_are_preserved(self):
        raw = 'WEBVTT\r\n\r\n1\r\n00:00:01.000 --> 00:00:03.000\r\nПреподаватель: Дома реши четвертое.\r\n\r\n2\r\n00:00:04.000 --> 00:00:05.000\r\nУченик: Хорошо.\r\n'
        self.assertEqual(lesson_source.transcript_text(raw), 'Преподаватель: Дома реши четвертое.\n\nУченик: Хорошо.')

    def test_consecutive_turns_merge_without_losing_content(self):
        text = lesson_source.transcript_text('WEBVTT\nA: Первая часть.\n\n2\n00:00:02 --> 00:00:03\nA: Вторая часть.\nB: Ответ.')
        self.assertEqual(text, 'A: Первая часть. Вторая часть.\n\nB: Ответ.')

    def test_numeric_answer_is_not_confused_with_cue_number(self):
        raw = 'WEBVTT\n\n1\n00:00:01.000 --> 00:00:02.000\n12\n\n2\n00:00:03.000 --> 00:00:04.000\nУченик: Ответ двенадцать.'
        self.assertEqual(lesson_source.transcript_text(raw), '12\n\nУченик: Ответ двенадцать.')

    def test_pending_transcript_is_retried_without_using_weak_summary(self):
        with self.assertRaises(ReportUnavailable):
            lesson_source.download_transcripts({'recording_files': [{'recording_type': 'audio_transcript', 'status': 'processing'}]}, 'not-logged')

    def test_no_transcript_does_not_download_video(self):
        with patch('urllib.request.build_opener') as network:
            self.assertEqual(lesson_source.download_transcripts({'recording_files':[{'recording_type':'audio_only'}]}, 'not-logged'), '')
            network.assert_not_called()
