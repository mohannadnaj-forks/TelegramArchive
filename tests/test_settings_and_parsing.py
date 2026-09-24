"""Settings from the environment, and parsing of the command line's values."""
import argparse
import unittest
from datetime import datetime

from telegram_archive.cli import parse_chat, parse_date, parse_size
from telegram_archive.export import format_size, merge_ranges
from telegram_archive.settings import Settings
from telegram_archive.telegram import api_chat_id


class SettingsFromEnvironment(unittest.TestCase):
    def test_defaults(self):
        settings = Settings.from_env({})
        self.assertEqual(settings, Settings())
        self.assertFalse(any(settings.media.values()))
        self.assertEqual((settings.checkpoint_seconds, settings.min_free_disk_mb, settings.resume_enabled), (10, 2048, True))

    def test_values(self):
        settings = Settings.from_env({'MEDIA_EXPORT_VOICE_MESSAGES': 'true', 'MEDIA_EXPORT_PHOTOS': 'False',
                                      'CHAT_EXPORT_SUPER_GROUPS': '1', 'RESUME_ENABLED': 'no',
                                      'JSON_FILE_PAGE_SIZE': '5000000 # bytes', 'API_ID': '12'})
        self.assertTrue(settings.media['voice_messages'])
        self.assertFalse(settings.media['photos'])
        self.assertTrue(settings.chats['super_group'])
        self.assertFalse(settings.resume_enabled)
        self.assertEqual(settings.json_file_page_size, 5000000)
        self.assertEqual(settings.api_id, '12')

    def test_page_size_none_as_in_env_example(self):
        self.assertIsNone(Settings.from_env({'JSON_FILE_PAGE_SIZE': 'None # Bytes. None for unlimited'}).json_file_page_size)


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
