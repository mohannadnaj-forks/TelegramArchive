"""Which files the downloading pass fetches, and how file states are counted."""
import unittest
from datetime import datetime

from telegram_archive import file_status as fs

ENABLED = {'photos': True, 'videos': False}


def record(status, date='2024-01-02T10:00:00', key='photo'):
    return {'id': 1, 'date': date, key: fs.NOT_INCLUDED.get(status['state'], 'photos/photo_1.jpg'), 'file_status': status}


class Wanted(unittest.TestCase):
    def wanted(self, status, max_file_size=100, since=None, until=None, **kwargs):
        return fs.is_wanted(record(status, **kwargs), ENABLED, max_file_size, since, until)

    def test_states_that_are_fetched(self):
        self.assertTrue(self.wanted(fs.pending(10)))
        self.assertTrue(self.wanted(fs.failed('x')))
        self.assertTrue(self.wanted(fs.total_limit(10, 5)))
        self.assertFalse(self.wanted(fs.downloaded(10)))
        self.assertFalse(fs.is_wanted({'id': 1, 'date': '2024-01-01T00:00:00'}, ENABLED, 0, None, None))

    def test_a_switched_off_kind_is_fetched_once_it_is_switched_on(self):
        self.assertTrue(self.wanted(fs.disabled('MEDIA_EXPORT_PHOTOS')))
        self.assertFalse(self.wanted(fs.disabled('MEDIA_EXPORT_VIDEOS')))

    def test_a_large_file_is_fetched_once_the_limit_allows_it(self):
        self.assertFalse(self.wanted(fs.too_large(500, 100)))
        self.assertTrue(self.wanted(fs.too_large(500, 100), max_file_size=1000))
        self.assertTrue(self.wanted(fs.too_large(500, 100), max_file_size=0))

    def test_only_files_inside_the_date_range(self):
        day = datetime(2024, 1, 2)
        self.assertTrue(self.wanted(fs.pending(1), since=day, until=datetime(2024, 1, 3)))
        self.assertFalse(self.wanted(fs.pending(1), since=datetime(2024, 1, 3)))
        self.assertFalse(self.wanted(fs.pending(1), until=day))


class Decisions(unittest.TestCase):
    def test_before_download(self):
        self.assertEqual(fs.before_download(10, False, 'MEDIA_EXPORT_VIDEOS', 0), fs.disabled('MEDIA_EXPORT_VIDEOS'))
        self.assertEqual(fs.before_download(10, True, 'MEDIA_EXPORT_VIDEOS', 5), fs.too_large(10, 5))
        self.assertIsNone(fs.before_download(10, True, 'MEDIA_EXPORT_VIDEOS', 0))

    def test_set_status_writes_the_path_or_the_reason(self):
        r = {}
        fs.set_status(r, 'file', fs.downloaded(3), 'files/a.pdf')
        self.assertEqual(r, {'file': 'files/a.pdf', 'file_status': {'state': 'downloaded', 'size': 3}})
        fs.set_status(r, fs.path_key(r), fs.failed('boom'))
        self.assertEqual(r['file'], '(File not included. Download failed; run again to retry.)')


class Counting(unittest.TestCase):
    def test_count_states_and_downloaded_bytes(self):
        records = [record(fs.downloaded(10)), record(fs.downloaded(10)), record(fs.pending(7)),
                   record(fs.failed('x')), {'id': 9, 'text': 'no media'}]
        self.assertEqual(fs.count_states(records), {'downloaded': {'count': 2, 'bytes': 20},
                                                   'pending': {'count': 1, 'bytes': 7},
                                                   'failed': {'count': 1, 'bytes': 0}})
        self.assertEqual(fs.downloaded_bytes(records), 10, 'one file on disk, counted once')


if __name__ == '__main__':
    unittest.main()
