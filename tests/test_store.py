"""The archive on disk (telegram_archive.store)."""
import os
import shutil
import sqlite3
import tempfile
import unittest

from telegram_archive.store import Archive, ArchiveVersionError, find_export_dir, is_export_dir


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
        self.archive = Archive(self.dir)

    def tearDown(self):
        self.archive.close()
        shutil.rmtree(self.dir, ignore_errors=True)

    def reopen(self) -> Archive:
        self.archive.close()
        self.archive = Archive(self.dir)
        return self.archive

    def item(self, i, state=None, size=None, **fields):
        media = {'media': [{'kind': 'photo', 'state': state, **({'size': size} if size else {})}]} if state else {}
        return {'id': str(i), 'date': '2024-01-02T10:00:00+00:00', **media, **fields}

    def put(self, i, *args, **fields):
        self.archive.put_item(self.item(i, *args, **fields), i)

    def test_a_new_archive_says_what_it_is(self):
        self.assertTrue(is_export_dir(self.dir))
        self.assertEqual((self.archive.get('format'), self.archive.get('version')), ('archive', 1))
        self.assertEqual(self.archive.count(), 0)

    def test_only_committed_changes_are_kept(self):
        self.put(1)
        self.archive.commit()
        self.put(2)
        self.assertEqual([r['id'] for r in self.reopen().items()], ['1'])

    def test_items_are_replaced_by_id_and_read_in_sort_order_with_their_media(self):
        for i in (9, 7, 2):
            self.put(i, 'pending', 5)
        self.put(7, text={'plain': 'edited'})
        self.assertEqual([r['id'] for r in self.archive.items()], ['2', '7', '9'])
        self.assertEqual([r['id'] for r in self.archive.items(newest_first=True)], ['9', '7', '2'])
        self.assertEqual(self.archive.item('7'), self.item(7, text={'plain': 'edited'}))
        self.assertEqual(self.archive.item('9'), self.item(9, 'pending', 5))
        self.assertEqual(self.archive.media_of('7'), [])
        self.assertIsNone(self.archive.item('8'))

    def test_files_are_counted_by_state(self):
        self.put(1, 'downloaded', 10)
        self.put(2, 'downloaded', 5)
        self.put(3, 'pending', 7)
        self.put(4)
        self.put(5, 'failed')
        self.assertEqual(self.archive.file_states(), {'downloaded': {'count': 2, 'bytes': 15},
                                                      'pending': {'count': 1, 'bytes': 7},
                                                      'failed': {'count': 1, 'bytes': 0}})
        self.assertEqual(self.archive.downloaded_bytes(), 15)
        self.assertEqual([(i, p) for i, p, date, m in self.archive.media_not_downloaded()], [('5', 0), ('3', 0)])
        self.archive.set_medium('3', 0, {'kind': 'photo', 'state': 'downloaded', 'size': 7})
        self.assertEqual(self.archive.downloaded_bytes(), 22)

    def test_values_and_runs(self):
        self.archive.set('account', {'name': 'Test'})
        run = self.archive.start_run({'status': 'running'})
        self.archive.update_run(run, {'status': 'complete'})
        self.archive.commit()
        self.assertEqual(self.reopen().get('account'), {'name': 'Test'})
        self.assertEqual(self.archive.runs(), [{'status': 'complete'}])

    def test_an_archive_of_another_version_is_refused(self):
        self.archive.close()
        db = sqlite3.connect(os.path.join(self.dir, 'archive.db'))
        db.execute("UPDATE archive SET value = '2' WHERE key = 'version'")
        db.commit()
        db.close()
        with self.assertRaisesRegex(ArchiveVersionError, 'version 2; this program reads version 1'):
            Archive(self.dir)


if __name__ == '__main__':
    unittest.main()
