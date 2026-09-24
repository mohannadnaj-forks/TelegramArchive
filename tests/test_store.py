"""The export folder on disk (telegram_archive.store)."""
import json
import os
import shutil
import tempfile
import unittest

from telegram_archive.store import ExportFolder, find_export_dir, is_export_dir


class Folders(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, *names):
        for name in names:
            os.makedirs(os.path.join(self.dir, name))

    def test_the_newest_own_folder_is_continued(self):
        self.make('ChatExport_durov_2024-01-01', 'ChatExport_durov_2025-01-01', 'ChatExport_durov_bot_2026-01-01',
                  'ChatExport_durov_2026-01-01 copy')
        self.assertEqual(os.path.basename(find_export_dir(self.dir, 'durov', True)), 'ChatExport_durov_2025-01-01')

    def test_a_new_folder_is_dated_today(self):
        self.make('ChatExport_durov_2024-01-01')
        self.assertRegex(os.path.basename(find_export_dir(self.dir, 'durov', False)), r'^ChatExport_durov_\d{4}-\d{2}-\d{2}$')
        self.assertRegex(os.path.basename(find_export_dir(self.dir, 'telegram', True)), r'^ChatExport_telegram_')

    def test_glob_characters_in_paths(self):
        output = os.path.join(self.dir, 'a [b] *')
        os.makedirs(os.path.join(output, 'ChatExport_x[1]_2024-01-01'))
        self.assertEqual(find_export_dir(output, 'x[1]', True), os.path.join(output, 'ChatExport_x[1]_2024-01-01'))


class Saving(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.folder = ExportFolder(self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def chat(self, count):
        return {'name': 'Test', 'messages': [{'id': i, 'text': 'x' * 50} for i in range(count, 0, -1)]}

    def test_an_empty_folder_holds_no_export(self):
        self.assertEqual(self.folder.load(), {})
        self.assertFalse(is_export_dir(self.dir))
        self.assertIsNone(self.folder.updated())

    def test_result_json_is_sorted_and_read_back(self):
        self.folder.write_result(self.chat(5), None)
        self.assertEqual(sorted(os.listdir(self.dir)), ['result.json'])
        self.assertEqual([m['id'] for m in self.folder.load()['messages']], [1, 2, 3, 4, 5])
        self.assertTrue(is_export_dir(self.dir))

    def test_parts_and_back(self):
        self.folder.write_result(self.chat(100), 2000)
        names = sorted(os.listdir(self.dir))
        self.assertNotIn('result.json', names)
        self.assertGreater(len(names), 2)
        loaded = self.folder.load()
        self.assertEqual(loaded['name'], 'Test')
        self.assertEqual([m['id'] for m in loaded['messages']], list(range(1, 101)))
        self.folder.write_result(self.chat(100), None)
        self.assertEqual(os.listdir(self.dir), ['result.json'])

    def test_the_journal_adds_to_and_replaces_records(self):
        self.folder.write_result(self.chat(3), None)
        self.folder.append_journal([{'id': 2, 'text': 'edited'}, {'id': 4, 'text': 'new'}])
        self.folder.append_journal([{'id': 4, 'text': 'newer'}])
        records = {m['id']: m['text'] for m in self.folder.load()['messages']}
        self.assertEqual(records, {1: 'x' * 50, 2: 'edited', 3: 'x' * 50, 4: 'newer'})
        self.folder.remove_journal()
        self.assertEqual(len(self.folder.load()['messages']), 3)

    def test_a_line_cut_short_is_skipped_and_the_next_append_starts_a_new_line(self):
        self.folder.append_journal([{'id': 1}])
        with open(self.folder.journal_path, 'a', encoding='utf-8') as f:
            f.write('{"id": 2, "te')
        self.folder.append_journal([{'id': 3}])
        self.assertEqual(sorted(self.folder.read_journal()), [1, 3])

    def test_state_round_trip_and_an_unreadable_state(self):
        self.folder.write_state({'listed': [[1, 5]]})
        self.assertEqual(self.folder.load_state(), {'listed': [[1, 5]]})
        with open(self.folder.state_path, 'w', encoding='utf-8') as f:
            f.write('{broken')
        self.assertEqual(self.folder.load_state(), {})

    def test_writes_leave_no_temporary_files(self):
        self.folder.write_result(self.chat(3), None)
        self.folder.write_state({})
        self.assertEqual(sorted(os.listdir(self.dir)), ['export_state.json', 'result.json'])
        with open(os.path.join(self.dir, 'result.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['name'], 'Test')


if __name__ == '__main__':
    unittest.main()
