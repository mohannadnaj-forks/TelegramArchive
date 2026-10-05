"""Settings from the environment, and parsing of the command line's values."""
import argparse
import io
import logging
import os
import shutil
import stat
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from hamstra_telegram.cli import configure_streams, library_handler, parse_chat, parse_date, parse_size
from hamstra_telegram.export import format_size, merge_ranges
from hamstra_telegram.settings import Settings, ask_api_pair, default_config_dir, find_config_dir, read_settings, save_api_pair
from hamstra_telegram.telegram import api_chat_id


class SettingsFromEnvironment(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env({})
        self.assertEqual(settings, Settings())
        self.assertFalse(any(settings.media.values()))
        self.assertEqual((settings.checkpoint_seconds, settings.min_free_disk_mb, settings.resume_enabled), (10, 2048, True))

    def test_values(self):
        settings = Settings.from_env({'MEDIA_EXPORT_VOICE_MESSAGES': 'true', 'MEDIA_EXPORT_PHOTOS': 'False',
                                      'CHAT_EXPORT_SUPER_GROUPS': '1', 'RESUME_ENABLED': 'no', 'API_ID': '12'})
        self.assertTrue(settings.media['voice_messages'])
        self.assertFalse(settings.media['photos'])
        self.assertTrue(settings.chats['super_group'])
        self.assertFalse(settings.resume_enabled)
        self.assertEqual(settings.api_id, '12')


class ConfigFolder(unittest.TestCase):
    home = os.path.expanduser('~')

    def test_the_default_follows_the_platform(self):
        self.assertEqual(default_config_dir({'APPDATA': r'C:\Users\me\AppData\Roaming'}, windows=True),
                         os.path.join(r'C:\Users\me\AppData\Roaming', 'hamstra', 'telegram'))
        self.assertEqual(default_config_dir({}, windows=True),
                         os.path.join(self.home, 'AppData', 'Roaming', 'hamstra', 'telegram'))
        self.assertEqual(default_config_dir({}, windows=False), os.path.join(self.home, '.config', 'hamstra', 'telegram'))
        absolute = os.path.abspath('xdg')
        self.assertEqual(default_config_dir({'XDG_CONFIG_HOME': absolute}, windows=False),
                         os.path.join(absolute, 'hamstra', 'telegram'))
        self.assertEqual(default_config_dir({'XDG_CONFIG_HOME': 'relative'}, windows=False),
                         os.path.join(self.home, '.config', 'hamstra', 'telegram'))

    def test_the_option_wins_over_the_variable_which_wins_over_the_default(self):
        env = {'HAMSTRA_TELEGRAM_CONFIG_DIR': os.path.join('~', 'from-env')}
        self.assertEqual(find_config_dir('from-option', env), os.path.abspath('from-option'))
        self.assertEqual(find_config_dir(None, env), os.path.join(self.home, 'from-env'))
        self.assertEqual(find_config_dir(None, {}), default_config_dir({}))


class SettingsFile(unittest.TestCase):
    def setUp(self):
        self.parent = tempfile.mkdtemp(prefix='hamstra-telegram-test-')
        self.dir = os.path.join(self.parent, 'hamstra', 'telegram')
        self.addCleanup(shutil.rmtree, self.parent, ignore_errors=True)

    def test_a_first_save_writes_every_setting_with_the_pair_filled_in(self):
        path = save_api_pair(self.dir, '12345', 'a' * 32)
        self.assertEqual(path, os.path.join(self.dir, 'settings.env'))
        saved = read_settings(self.dir, {})
        self.assertEqual((saved['API_ID'], saved['API_HASH']), ('12345', 'a' * 32))
        settings = Settings.from_env(saved)
        self.assertEqual([kind for kind, on in settings.media.items() if on], ['photos', 'stickers'])
        self.assertEqual(settings.min_free_disk_mb, 2048)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_a_later_save_keeps_the_other_settings(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, 'settings.env'), 'w', encoding='utf-8') as f:
            f.write('# mine\nMEDIA_EXPORT_VIDEOS=True\nAPI_ID=\n')
        save_api_pair(self.dir, '7', 'b' * 32)
        with open(os.path.join(self.dir, 'settings.env'), encoding='utf-8') as f:
            self.assertEqual(f.read(), f"# mine\nMEDIA_EXPORT_VIDEOS=True\nAPI_ID=7\nAPI_HASH={'b' * 32}\n")

    def test_the_environment_wins_unless_its_value_is_empty(self):
        os.makedirs(self.dir)
        with open(os.path.join(self.dir, 'settings.env'), 'w', encoding='utf-8') as f:
            f.write('API_ID=7\nCHECKPOINT_SECONDS=30\nDOWNLOAD_PATH=/saved\n')
        merged = read_settings(self.dir, {'CHECKPOINT_SECONDS': '5', 'API_ID': '', 'OTHER': 'kept'})
        self.assertEqual(merged, {'API_ID': '7', 'CHECKPOINT_SECONDS': '5', 'DOWNLOAD_PATH': '/saved', 'OTHER': 'kept'})

    def test_a_missing_file_leaves_the_environment_as_it_is(self):
        self.assertEqual(read_settings(self.dir, {'API_ID': '3'}), {'API_ID': '3'})
        self.assertFalse(os.path.exists(self.dir))

    def test_asking_repeats_until_the_answers_have_the_right_shape(self):
        answers = iter(['abc', ' 12345 ', 'short', 'A1' * 16])
        said = []
        self.assertEqual(ask_api_pair(lambda prompt: next(answers), said.append), ('12345', 'a1' * 16))
        self.assertIn('https://my.telegram.org', said[0])
        self.assertEqual(len(said), 3)


class Parsing(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(parse_size('0'), 0)
        self.assertEqual(parse_size('1050'), 1050)
        self.assertEqual(parse_size('20K'), 20 * 1024)
        self.assertEqual(parse_size('1.5g'), int(1.5 * 1024 ** 3))
        self.assertEqual(parse_size('200 MB'), 200 * 1024 ** 2)
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_size('lots')

    def test_dates(self):
        self.assertEqual(parse_date('2024-02-29'), datetime(2024, 2, 29))
        with self.assertRaises(argparse.ArgumentTypeError):
            parse_date('2024-02-30')

    def test_chats(self):
        self.assertEqual(parse_chat(' https://t.me/durov/12?single '), 'durov')
        self.assertEqual(parse_chat('telegram.me/durov'), 'durov')
        self.assertEqual(parse_chat('@durov'), 'durov')
        self.assertEqual(parse_chat('-1001234'), -1001234)
        self.assertEqual(parse_chat('1234'), 1234)

    def test_ids_for_the_api(self):
        self.assertEqual(api_chat_id(1234), -1001234)
        self.assertEqual(api_chat_id(-1001234), -1001234)
        self.assertEqual(api_chat_id(-555), -555)
        self.assertEqual(api_chat_id('durov'), 'durov')


class OutputStreams(unittest.TestCase):
    def test_output_is_utf8_and_line_buffered_when_redirected(self):
        out, err = (io.TextIOWrapper(io.BytesIO(), encoding='cp1252') for _ in range(2))
        with mock.patch('sys.stdout', out), mock.patch('sys.stderr', err):
            configure_streams()
        self.assertEqual((out.encoding, out.line_buffering, err.encoding, err.line_buffering), ('utf-8', True, 'utf-8', True))


class LibraryLogging(unittest.TestCase):
    def test_errors_are_printed_except_the_send_failure_at_disconnect(self):
        stream = io.StringIO()
        handler = library_handler(stream)
        log = logging.getLogger('pyrogram.test.tcp')
        log.propagate = False
        log.addHandler(handler)
        try:
            log.error('Send failed: ConnectionResetError Connection lost')
            log.warning('[1] Retrying "upload.GetFile" due to: Request timed out')
            log.error('Server sent transport error: 404 (auth key not found)')
        finally:
            log.removeHandler(handler)
        self.assertEqual(stream.getvalue().count('\n'), 1)
        self.assertIn('transport error', stream.getvalue())


class Helpers(unittest.TestCase):
    def test_merge_ranges(self):
        self.assertEqual(merge_ranges([[5, 9], [1, 3], [4, 4], [20, 30], [25, 26]]), [[1, 9], [20, 30]])
        self.assertEqual(merge_ranges([]), [])

    def test_format_size(self):
        self.assertEqual(format_size(0), '0 B')
        self.assertEqual(format_size(2048), '2 KB')
        self.assertEqual(format_size(5 * 1024 ** 3), '5.0 GB')
        self.assertEqual(format_size(3 * 1024 ** 4), '3,072.0 GB')


if __name__ == '__main__':
    unittest.main()
