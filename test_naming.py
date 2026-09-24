import sys
import unittest
from types import SimpleNamespace

sys.argv = ['bot.py', 'x']
_ns = {}
exec(compile(open('bot.py', encoding='utf-8').read().split('def parse_chat(')[0], 'bot', 'exec'), _ns)
media_file_name = _ns['media_file_name']


def media(file_name, mime_type='video/mp4'):
    return SimpleNamespace(file_name=file_name, mime_type=mime_type)


class NamingIsStableAcrossRuns(unittest.TestCase):
    def test_invented_kurigram_name_is_replaced_by_message_id(self):
        self.assertEqual(media_file_name(362, media('video_2026-09-22_04-02-36.mp4'), 'video', '.mp4'), 'video_362.mp4')
        self.assertEqual(media_file_name(362, media('video_2026-09-23_11-00-00.mp4'), 'video', '.mp4'), 'video_362.mp4')

    def test_missing_name_uses_kind_and_id(self):
        self.assertEqual(media_file_name(7, media(None), 'video', '.mp4'), 'video_7.mp4')
        self.assertEqual(media_file_name(7, media(None, None), 'photo', '.jpg'), 'photo_7.jpg')
        self.assertEqual(media_file_name(7, media(None, 'audio/ogg'), 'voice', '.ogg'), 'voice_7.oga')

    def test_real_name_is_kept_behind_the_id(self):
        self.assertEqual(media_file_name(347, media('IMG_5471.MP4'), 'video', '.mp4'), '347_IMG_5471.MP4')

    def test_characters_invalid_on_exfat_and_windows_are_replaced(self):
        self.assertEqual(media_file_name(1, media('a<b>:"c/d\\e|f?g*.pdf', 'application/pdf'), 'document', ''), '1_a_b___c_d_e_f_g_.pdf')

    def test_very_long_names_are_cut(self):
        name = media_file_name(1, media('x' * 300 + '.mp4'), 'video', '.mp4')
        self.assertLessEqual(len(name), 140)
        self.assertTrue(name.endswith('.mp4'))


if __name__ == '__main__':
    unittest.main()
