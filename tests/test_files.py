"""The retried rename (telegram_archive.files.replace)."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

from telegram_archive import files


class Replace(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.source, self.destination = os.path.join(self.dir, 'a.tmp'), os.path.join(self.dir, 'a')
        open(self.source, 'w').close()
        self.waits = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def replace_failing(self, times: int):
        real = os.replace
        calls = []

        def fake(source, destination):
            calls.append(1)
            if len(calls) <= times:
                raise PermissionError(5, 'Access is denied')
            real(source, destination)

        with mock.patch('os.replace', fake):
            files.replace(self.source, self.destination, sleep=self.waits.append)
        return len(calls)

    def test_a_locked_destination_is_retried_with_a_backoff(self):
        self.assertEqual(self.replace_failing(3), 4)
        self.assertEqual(self.waits, [0.1, 0.2, 0.5])
        self.assertEqual(os.listdir(self.dir), ['a'])

    def test_a_destination_that_stays_locked_raises(self):
        with self.assertRaises(PermissionError):
            self.replace_failing(10)
        self.assertEqual(self.waits, [0.1, 0.2, 0.5, 1, 2])

    def test_other_errors_are_not_retried(self):
        with self.assertRaises(FileNotFoundError):
            files.replace(os.path.join(self.dir, 'missing'), self.destination, sleep=self.waits.append)
        self.assertEqual(self.waits, [])


if __name__ == '__main__':
    unittest.main()
