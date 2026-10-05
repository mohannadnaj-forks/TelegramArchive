"""File names and paths (hamstra_telegram.media); a stored path is never recomputed, so these only apply to new files."""
import unittest
from types import SimpleNamespace

from hamstra_telegram.media import MEDIA_KINDS, file_name, media_path, thumbnail_path

KINDS = {kind.kind: kind for kind in MEDIA_KINDS}


def media(file_name, mime_type='video/mp4'):
    return SimpleNamespace(file_name=file_name, mime_type=mime_type)


class Naming(unittest.TestCase):
    def test_invented_kurigram_name_is_replaced_by_the_id(self):
        self.assertEqual(file_name('362', media('video_2026-09-22_04-02-36.mp4'), KINDS['video']), '362.mp4')

    def test_without_a_name_the_extension_comes_from_a_fixed_table(self):
        self.assertEqual(file_name('7', media(None), KINDS['video']), '7.mp4')
        self.assertEqual(file_name('7', media(None, None), KINDS['photo']), '7.jpg')
        self.assertEqual(file_name('7', media(None, 'audio/ogg'), KINDS['voice']), '7.ogg')
        self.assertEqual(file_name('7', media(None, 'application/x-tgsticker'), KINDS['sticker']), '7.tgs')
        self.assertEqual(file_name('7', media(None, 'video/webm'), KINDS['sticker']), '7.webm')
        self.assertEqual(file_name('7', media(None, 'application/x-unknown'), KINDS['document']), '7')

    def test_real_name_is_kept_behind_the_id(self):
        self.assertEqual(file_name('347', media('IMG_5471.MP4'), KINDS['video']), '347_IMG_5471.MP4')

    def test_characters_invalid_on_exfat_and_windows_are_replaced(self):
        self.assertEqual(file_name('1', media('a<b>:"c/d\\e|f?g*.pdf', 'application/pdf'), KINDS['document']),
                         '1_a_b___c_d_e_f_g_.pdf')

    def test_very_long_names_are_cut(self):
        name = file_name('1', media('x' * 300 + '.mp4'), KINDS['video'])
        self.assertLessEqual(len(name), 140)
        self.assertTrue(name.endswith('.mp4'))

    def test_files_go_in_a_folder_per_month(self):
        self.assertEqual(media_path('2024-04', '5148.mp4'), 'media/2024-04/5148.mp4')
        self.assertEqual(thumbnail_path('2024-04', '5148'), 'media/2024-04/5148.thumb.jpg')


if __name__ == '__main__':
    unittest.main()
