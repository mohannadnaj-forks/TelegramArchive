"""python bot.py in a subprocess, for what only a real process shows: Ctrl-C, a kill, the session lock
between two processes, where the session lives, and running without a .env."""
import os
import shutil
import subprocess
import sys
import unittest

from tests.support import SubprocessRun
from tests.test_legacy_exports import FIXTURES, read_records


class EndToEnd(SubprocessRun):
    def test_ctrl_c_during_listing_keeps_what_was_listed(self):
        self.assertEqual(self.run_bot(count=350, interrupt_after_listed=120), 0, self.output)
        self.assertIn('Stopping after the current message', self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(231, 351)))
        self.assertEqual(self.state()['run']['status'], 'stopped')
        self.assertEqual(self.run_bot(count=350), 0, self.output)
        self.assertEqual(self.state()['listed'], [[1, 350]])

    def test_ctrl_c_during_downloads_resumes_without_downloading_twice(self):
        self.assertEqual(self.run_bot(interrupt_after_downloads=10), 0, self.output)
        first = self.downloaded_files()
        self.assertEqual(self.state()['run']['stage'], 'downloading')
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertFalse(set(first) & set(self.downloaded_files()))
        self.assertEqual(self.file_states(), {'downloaded': 83 + 34})

    def test_a_killed_run_is_recovered_from_the_journal(self):
        self.assertEqual(self.run_bot(checkpoint_seconds=0, kill_after_listed=120), 9)
        self.assertEqual(len(read_records(self.export_dir())), 120)
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertIn('Recovered 120 messages', self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(1, 251)))

    def test_an_export_killed_by_e7faf7f_during_downloads_finishes(self):
        shutil.copytree(os.path.join(FIXTURES, 'killed_downloading'), self.out, ignore=shutil.ignore_patterns('made_by.json'))
        self.assertEqual(self.run_bot(count=90), 0, self.output)
        self.assertEqual(self.file_states(), {'downloaded': 42})
        self.assertFalse(os.path.exists(self.path('export_journal.jsonl')))

    def test_the_viewer_is_rebuilt_from_the_command_line(self):
        self.run_bot(count=30)
        os.remove(self.path('index.html'))
        self.assertEqual(self.run_bot('--viewer-only', chat=''), 2)
        self.assertEqual(self.run_bot('--viewer-only', self.export_dir(), chat=''), 0, self.output)
        self.assertIn('Rebuilt viewer for Test: 30 messages', self.output)
        self.assertTrue(os.path.exists(self.path('index.html')))


class Session(SubprocessRun):
    def test_the_session_lives_next_to_the_program(self):
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        [client] = self.calls('client')
        self.assertEqual(client['name'], 'my_bot')
        self.assertEqual(os.path.realpath(client['workdir']), os.path.realpath(os.path.join(self.program, '.telegram')))

    def test_a_second_run_on_the_same_login_stops_at_once(self):
        lock_dir = os.path.join(self.program, '.telegram')
        os.makedirs(lock_dir)
        with open(os.path.join(lock_dir, 'my_bot.lock'), 'a+') as held:
            if os.name == 'nt':
                import msvcrt
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_bot(count=3), 1)
        self.assertIn('Another export is already running with this Telegram login', self.output)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.calls('client'), [])

    def test_help_and_the_viewer_need_no_settings(self):
        env = {'PATH': os.environ.get('PATH', ''), 'SYSTEMROOT': os.environ.get('SYSTEMROOT', ''),
               'PYTHONIOENCODING': 'utf-8'}
        bot = os.path.join(self.program, 'bot.py')
        result = subprocess.run([sys.executable, bot, '--help'], env=env, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--viewer-only', result.stdout)
        shutil.copytree(os.path.join(FIXTURES, 'complete'), self.out, ignore=shutil.ignore_patterns('made_by.json'))
        result = subprocess.run([sys.executable, bot, '--viewer-only', self.export_dir()], env=env,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(self.path('data', 'index.js')))
        self.assertFalse(os.path.exists(os.path.join(self.program, '.telegram')))


if __name__ == '__main__':
    unittest.main()
