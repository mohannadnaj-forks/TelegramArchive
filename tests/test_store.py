"""The archive on disk (hamstra_telegram.store)."""
import os
import shutil
import sqlite3
import tempfile
import unittest

from hamstra_telegram.store import Archive, ArchiveVersionError, find_archive, is_export_dir, new_archive_dir, read_account


class Folders(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='hamstra-telegram-test-')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def make(self, name, account=None):
        archive = Archive(os.path.join(self.dir, name))
        if account:
            archive.set('account', account)
            archive.commit()
        archive.close()

    def test_archives_are_found_by_their_account(self):
        self.make('b', {'id': 'channel1'})
        self.make('a', {'id': 'channel2'})
        os.makedirs(os.path.join(self.dir, 'not an archive'))
        self.assertEqual(read_account(os.path.join(self.dir, 'b')), {'id': 'channel1'})
        self.assertEqual(find_archive(self.dir, lambda a: a['id'] == 'channel1'), os.path.join(self.dir, 'b'))
        self.assertIsNone(find_archive(self.dir, lambda a: a['id'] == 'channel3'))
        self.assertIsNone(find_archive(os.path.join(self.dir, 'missing'), lambda a: True))

    def test_new_folders_do_not_reuse_a_name(self):
        self.assertEqual(new_archive_dir(self.dir, 'telegram-durov'), os.path.join(self.dir, 'telegram-durov'))
        self.make('telegram-durov')
        self.assertEqual(new_archive_dir(self.dir, 'telegram-durov'), os.path.join(self.dir, 'telegram-durov-2'))
        self.assertEqual(new_archive_dir(self.dir, 'a:b'), os.path.join(self.dir, 'a_b'))


class Saving(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='hamstra-telegram-test-')
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
