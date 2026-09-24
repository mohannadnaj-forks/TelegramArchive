"""The viewer's index and chunks, built directly (telegram_archive.viewer.viewer_index)."""
import unittest

from telegram_archive.viewer import VIEWER_CHUNK_MESSAGES, plain_text, viewer_index


def message(i, day='2024-01-01', **fields):
    return {'id': i, 'date': f'{day}T10:00:00', **fields}


class Index(unittest.TestCase):
    def test_a_busy_month_is_split_into_numbered_chunks(self):
        messages = [message(i) for i in range(1, VIEWER_CHUNK_MESSAGES * 2 + 2)]
        index, chunks, search = viewer_index({'name': 'x', 'messages': messages}, None)
        [month] = index['months']
        self.assertEqual([c['name'] for c in month['chunks']], ['2024-01.1', '2024-01.2', '2024-01.3'])
        self.assertEqual([c['count'] for c in month['chunks']], [2000, 2000, 1])
        self.assertEqual(month['chunks'][1]['first_id'], 2001)
        self.assertEqual(sorted(chunks), ['2024-01.1', '2024-01.2', '2024-01.3'])
        self.assertEqual(index['chat'], {'name': 'x'})
        self.assertIsNone(index['updated'])

    def test_kinds_and_missing_files_are_counted(self):
        messages = [
            message(1, photo='photos/a.jpg', file_status={'state': 'downloaded', 'size': 1}),
            message(2, file='x', media_type='animation', file_status={'state': 'pending', 'size': 1}),
            message(3, file='files/a.pdf', file_status={'state': 'too_large', 'size': 9, 'limit': 1}),
            message(4, text=''),
            message(5, day='2024-03-05', text='later'),
        ]
        index, chunks, search = viewer_index({'messages': messages}, '2024-04-01T00:00:00')
        january, march = index['months']
        self.assertEqual({k: january[k] for k in ('count', 'photo', 'video', 'other', 'text', 'missing')},
                         {'count': 4, 'photo': 1, 'video': 1, 'other': 1, 'text': 1, 'missing': 2})
        self.assertEqual(march['key'], '2024-03')
        self.assertEqual(search[-1], [5, '2024-03-05T10:00', 'later'])

    def test_plain_text(self):
        self.assertEqual(plain_text([{'type': 'bold', 'text': 'a'}, 'a b']), 'a b')
        self.assertEqual(plain_text(None), '')
        self.assertEqual(plain_text([]), '')


if __name__ == '__main__':
    unittest.main()
