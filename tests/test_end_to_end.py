"""python bot.py in a subprocess, for what only a real process shows: Ctrl-C, a kill, the session lock
between two processes, where the session lives, and running without settings."""
import os
import shutil
import subprocess
import sys
import unittest

from tests.support import SubprocessRun


class EndToEnd(SubprocessRun):
    def test_ctrl_c_during_listing_keeps_what_was_listed(self):
        self.assertEqual(self.run_bot(count=350, interrupt_after_listed=120), 0, self.output)
        self.assertIn('Stopping after the current message', self.output)
        self.assertEqual(self.item_ids(), list(range(231, 351)))
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

    def test_a_killed_run_keeps_what_it_saved(self):
        self.assertEqual(self.run_bot(checkpoint_seconds=0, kill_after_listed=120), 9)
        self.assertEqual(len(self.item_ids()), 120)
        self.assertEqual(self.state()['run']['status'], 'running')
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.item_ids(), list(range(1, 251)))

    def test_the_viewer_is_rebuilt_from_the_command_line(self):
        self.run_bot(count=30)
        os.remove(self.path('index.html'))
        self.assertEqual(self.run_bot('--viewer-only', chat=''), 2)
        self.assertEqual(self.run_bot('--viewer-only', self.export_dir(), chat=''), 0, self.output)
        self.assertIn('Rebuilt viewer for Test: 30 messages', self.output)
        self.assertTrue(os.path.exists(self.path('index.html')))


class Session(SubprocessRun):
    def test_missing_api_settings_are_explained_before_anything_is_created(self):
        self.assertEqual(self.run_bot(count=3, env={'API_ID': '', 'API_HASH': ''}), 1)
        self.assertIn('API_ID and API_HASH are not set', self.output)
        self.assertNotIn('Traceback', self.output)
        self.assertFalse(os.path.exists(self.out))
        self.assertIn(os.path.join(self.config, 'settings.env'), self.output)
        self.assertFalse(os.path.exists(self.config))

    def test_the_session_lives_in_the_config_folder_and_not_next_to_the_program(self):
        before = sorted(os.listdir(self.program))
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        [client] = self.calls('client')
        self.assertEqual(client['name'], 'account')
        self.assertEqual(os.path.realpath(client['workdir']), os.path.realpath(self.config))
        self.assertEqual(sorted(name for name in os.listdir(self.program) if name != '__pycache__'), before)

    def test_config_dir_on_the_command_line_wins_over_the_environment(self):
        chosen = os.path.join(self.dir, 'chosen')
        self.assertEqual(self.run_bot('--config-dir', chosen, count=3), 0, self.output)
        [client] = self.calls('client')
        self.assertEqual(os.path.realpath(client['workdir']), os.path.realpath(chosen))
        self.assertFalse(os.path.exists(self.config))

    def test_settings_come_from_the_config_folder_and_the_environment_wins(self):
        os.makedirs(self.config)
        with open(os.path.join(self.config, 'settings.env'), 'w', encoding='utf-8') as f:
            f.write('API_ID=7\nAPI_HASH=saved\nMEDIA_EXPORT_PHOTOS=False\nMEDIA_EXPORT_VIDEOS=False\n')
        with open(os.path.join(self.program, '.env'), 'w', encoding='utf-8') as f:
            f.write('MEDIA_EXPORT_PHOTOS=True\n')
        self.assertEqual(self.run_bot(count=30, env={'API_ID': '', 'API_HASH': '', 'MEDIA_EXPORT_PHOTOS': '',
                                                     'MEDIA_EXPORT_VIDEOS': 'True'}), 0, self.output)
        kinds = {medium['kind'] for item in self.archive()['items'] for medium in item.get('media', [])
                 if medium['state'] == 'downloaded'}
        self.assertEqual(kinds, {'video'})

    def test_a_second_run_on_the_same_login_stops_at_once(self):
        os.makedirs(self.config)
        with open(os.path.join(self.config, 'account.lock'), 'a+') as held:
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
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        shutil.rmtree(self.config)
        result = subprocess.run([sys.executable, bot, '--viewer-only', self.export_dir()], env=env,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(self.path('data', 'index.js')))
        self.assertFalse(os.path.exists(self.config))


if __name__ == '__main__':
    unittest.main()
