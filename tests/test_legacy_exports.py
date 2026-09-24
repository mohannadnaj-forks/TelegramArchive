"""Exports written by an earlier version (tests/fixtures/legacy, see make_legacy_exports.py) continue under this one.

The format they pin is result.json or its parts, export_state.json, export_journal.jsonl, the media
file names and the folder names. An export left in any state by that version must finish without
listing again what it listed, without downloading again what is on disk, and without changing the
records it already holds, other than filling in their files.
"""
import json
import os
import shutil
import unittest

from tests.support import ExportRun, message_id_of

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'legacy')
ALL_FILES = 30 + 12  # the basic chat's photos and videos among ids 1..90
FILE_FIELDS = ('photo', 'file', 'thumbnail', 'file_status')


def read_records(export: str) -> dict:
    records = {}
    names = ['result.json'] if os.path.exists(os.path.join(export, 'result.json')) else sorted(
        n for n in os.listdir(export) if n.startswith('result_part'))
    for name in names:
        with open(os.path.join(export, name), encoding='utf-8') as f:
            records.update({m['id']: m for m in json.load(f)['messages']})
    journal = os.path.join(export, 'export_journal.jsonl')
    if os.path.exists(journal):
        with open(journal, encoding='utf-8') as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    break
                records[record['id']] = record
    return records


def media_files(export: str) -> dict:
    files = {}
    for folder in ('photos', 'video_files'):
        path = os.path.join(export, folder)
        for name in os.listdir(path) if os.path.isdir(path) else []:
            files[f'{folder}/{name}'] = os.path.getsize(os.path.join(path, name))
    return files


class LegacyExport(ExportRun):
    def use_fixture(self, name: str) -> None:
        shutil.copytree(os.path.join(FIXTURES, name), self.out, ignore=shutil.ignore_patterns('made_by.json'))
        self.before = read_records(self.export_dir())
        self.files_before = {k: v for k, v in media_files(self.export_dir()).items() if not k.endswith('.tmp')}

    def assert_finished(self, count: int = 90) -> None:
        records = self.records()
        self.assertEqual(sorted(records), list(range(1, count + 1)))
        self.assertEqual(self.state()['listed'], [[1, count]])
        self.assertEqual(self.state()['run']['status'], 'complete')
        self.assertFalse(os.path.exists(self.path('export_journal.jsonl')))
        self.assertTrue(os.path.exists(self.path('data', 'index.js')))
        self.assertEqual([n for n in media_files(self.export_dir()) if n.endswith('.tmp')], [])
        for record_id, old in self.before.items():
            new = records[record_id]
            self.assertEqual({k: v for k, v in new.items() if k not in FILE_FIELDS},
                             {k: v for k, v in old.items() if k not in FILE_FIELDS}, record_id)
            if old.get('file_status', {}).get('state') == 'downloaded':
                self.assertEqual(new, old)

    def assert_no_file_downloaded_twice(self) -> None:
        for name in self.downloaded_files():
            self.assertNotIn(name, {os.path.basename(p) for p in self.files_before})
        for path, size in self.files_before.items():
            self.assertEqual(os.path.getsize(self.path(path)), size, path)


class ResumingLegacyExports(LegacyExport):
    def test_a_finished_export_lists_only_new_messages(self):
        self.use_fixture('complete')
        self.assertEqual(self.run_bot(count=70), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(70, 59, -1)))
        self.assert_finished(70)
        self.assertTrue(all(message_id_of(n) > 60 for n in self.downloaded_files()))
        self.assert_no_file_downloaded_twice()

    def test_a_listing_stopped_by_ctrl_c_continues_below_what_it_reached(self):
        self.use_fixture('stopped_listing')
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertEqual(self.listed_ids(), [90] + list(range(50, 0, -1)))
        self.assert_finished()
        self.assertEqual(self.file_states(), {'downloaded': ALL_FILES})

    def test_downloads_stopped_by_ctrl_c_continue_without_listing_again(self):
        self.use_fixture('stopped_downloading')
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertEqual(self.listed_ids(), [90])
        self.assert_finished()
        self.assert_no_file_downloaded_twice()
        self.assertEqual(self.file_states(), {'downloaded': ALL_FILES})

    def test_a_killed_listing_is_recovered_from_its_journal(self):
        self.use_fixture('killed_listing')
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertIn('Recovered 45 messages', self.output)
        self.assertEqual(self.listed_ids(), [90] + list(range(45, 0, -1)))
        self.assert_finished()

    def test_killed_downloads_are_recovered_from_the_journal_and_a_partial_file_is_fetched_again(self):
        self.use_fixture('killed_downloading')
        self.assertIn('photos/photo_84.jpg.tmp', media_files(self.export_dir()))
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertEqual(self.listed_ids(), [90])
        self.assertIn('photo_84.jpg', self.downloaded_files())
        self.assert_finished()
        self.assert_no_file_downloaded_twice()
        self.assertEqual(self.file_states(), {'downloaded': ALL_FILES})

    def test_an_export_split_into_parts_continues_in_parts(self):
        self.use_fixture('split')
        self.assertEqual(self.run_bot(count=100, env={'JSON_FILE_PAGE_SIZE': 20000}), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(100, 89, -1)))
        self.assertFalse(os.path.exists(self.path('result.json')))
        records = read_records(self.export_dir())
        self.assertEqual(sorted(records), list(range(1, 101)))
        for record_id, old in self.before.items():
            self.assertEqual(records[record_id], old)
        self.assert_no_file_downloaded_twice()

    def test_a_stopped_date_range_continues_and_a_full_run_fills_in_the_rest(self):
        self.use_fixture('date_range_stopped')
        self.assertEqual(self.run_bot('--since', '2024-01-02', '--until', '2024-01-03', count=90), 0, self.output)
        self.assertEqual(sorted(self.records()), list(range(24, 72)))
        self.assertFalse(set(self.listed_ids()) & set(range(62, 71)))
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assert_finished()

    def test_files_left_out_by_limits_and_settings_are_fetched_when_they_are_lifted(self):
        self.use_fixture('limits')
        self.assertEqual(self.before[48]['file_status']['state'], 'failed')
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertEqual(self.listed_ids(), [90])
        self.assert_finished()
        self.assert_no_file_downloaded_twice()
        self.assertEqual(self.file_states(), {'downloaded': ALL_FILES})


if __name__ == '__main__':
    unittest.main()
