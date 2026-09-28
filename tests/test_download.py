"""The retry policy of one download (telegram_archive.download.Downloader)."""
import asyncio
import functools
import inspect
import logging
import os
import shutil
import tempfile
import unittest

from pyrogram.errors import FileReferenceExpired, FloodWait

from telegram_archive.download import Downloader, LastWarning, LowDiskSpace
from telegram_archive.settings import Settings


class ScriptedClient:
    """download_media follows a script, one entry per attempt: 'ok', 'zero', a number (a FloodWait) or an exception."""

    def __init__(self, script):
        self.script = list(script)
        self.attempts = 0

    async def download_media(self, file_id, file_name, progress=None, progress_args=()):
        self.attempts += 1
        step = self.script.pop(0) if self.script else 'ok'
        if isinstance(step, int):
            raise FloodWait(value=step)
        if isinstance(step, tuple):  # the library's retry warning, then the error it raises
            logging.getLogger('pyrogram.session.session').warning(step[0])
            raise step[1]
        if isinstance(step, Exception):
            raise step
        for current in (1, 2, 3):
            if progress is None:
                continue
            args = (current * 1024 ** 2, 3 * 1024 ** 2, *progress_args)
            if inspect.iscoroutinefunction(progress):
                await progress(*args)
            else:
                await asyncio.get_running_loop().run_in_executor(None, functools.partial(progress, *args))
        with open(file_name, 'wb') as f:
            f.write(b'' if step == 'zero' else b'data')


class NoProgress:
    def __init__(self):
        self.postfixes = []

    def set_postfix(self, **kwargs):
        self.postfixes.append(kwargs)


class Downloads(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.destination = os.path.join(self.dir, 'video_1.mp4')
        self.waits = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def fetch(self, script, free=10 ** 12, attempts=None, heartbeat=None, **settings):
        async def sleep(seconds):
            self.waits.append(seconds)

        ticks = iter(range(0, 10 ** 6, 10))
        self.client = ScriptedClient(script)
        self.progress = NoProgress()
        downloader = Downloader(self.client, Settings(**settings), sleep=sleep, free_bytes=lambda path: free,
                                clock=lambda: next(ticks))
        return asyncio.run(downloader.fetch('video:1', self.destination, self.progress, attempts, heartbeat))

    def test_a_file_on_disk_is_not_fetched(self):
        open(self.destination, 'w').close()
        self.assertEqual(self.fetch(['ok']), (True, None))
        self.assertEqual(self.client.attempts, 0)

    def test_download_goes_through_a_temporary_file(self):
        self.assertEqual(self.fetch(['ok']), (True, None))
        self.assertEqual(os.listdir(self.dir), ['video_1.mp4'])

    def test_a_failed_download_leaves_nothing_behind(self):
        self.assertEqual(self.fetch([ConnectionError('gone')] * 5), (False, 'gone'))
        self.assertEqual(os.listdir(self.dir), [])

    def test_flood_waits_are_waited_out(self):
        self.assertEqual(self.fetch([30, 'ok']), (True, None))
        self.assertEqual(self.waits, [30])

    def test_a_flood_wait_over_the_maximum_gives_up_at_once(self):
        self.assertEqual(self.fetch([30], flood_wait_max_sleep=10), (False, 'FloodWait of 30s exceeds FLOOD_WAIT_MAX_SLEEP'))
        self.assertEqual(self.waits, [])

    def test_errors_back_off_exponentially_up_to_the_retry_count(self):
        ok, error = self.fetch([ConnectionError('gone')] * 7, download_max_retries=7)
        self.assertEqual((ok, error), (False, 'gone'))
        self.assertEqual(self.waits, [2, 4, 8, 16, 32, 60])

    def test_zero_byte_downloads_are_taken_as_a_hidden_flood_wait(self):
        self.assertEqual(self.fetch(['zero'] * 5), (False, 'zero bytes written'))
        self.assertEqual(self.waits, [2, 120, 8, 120])
        self.assertEqual(os.listdir(self.dir), [])

    def test_an_expired_file_reference_is_not_retried(self):
        with self.assertRaises(FileReferenceExpired):
            self.fetch([FileReferenceExpired(), 'ok'])
        self.assertEqual((self.client.attempts, self.waits, os.listdir(self.dir)), (1, [], []))

    def test_an_attempt_count_overrides_the_setting(self):
        self.assertEqual(self.fetch([ConnectionError('gone')] * 5, attempts=1), (False, 'gone'))
        self.assertEqual((self.client.attempts, self.waits), (1, []))

    def test_the_error_behind_a_failed_request_is_reported(self):
        step = ('[3] Retrying "upload.GetFile" due to: Request timed out', TimeoutError('Failed to invoke "upload.GetFile" after 10 retries'))
        ok, error = self.fetch([step] * 5)
        self.assertFalse(ok)
        self.assertEqual(error, 'Failed to invoke "upload.GetFile" after 10 retries (last error: Request timed out)')
        self.assertFalse([h for h in logging.getLogger('pyrogram').handlers if isinstance(h, LastWarning)])

    def test_progress_shows_the_bytes_and_speed_and_beats(self):
        beats = []
        self.assertEqual(self.fetch(['ok'], heartbeat=lambda: beats.append(1)), (True, None))
        self.assertEqual(len(beats), 3)
        self.assertIn({'file': 'video_1.mp4', 'status': '1/3 MB, 0.1 MB/s'}, self.progress.postfixes)

    def test_low_disk_space_stops_before_downloading(self):
        with self.assertRaises(LowDiskSpace):
            self.fetch(['ok'], free=100 * 1024 ** 2, min_free_disk_mb=2048)
        self.assertEqual(self.client.attempts, 0)


if __name__ == '__main__':
    unittest.main()
