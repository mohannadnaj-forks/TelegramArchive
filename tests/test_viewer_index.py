"""The viewer's month index and chunks, built from items directly (hamstra_telegram.viewer)."""
import unittest

from hamstra_telegram.viewer import VIEWER_CHUNK_ITEMS, month_entry, search_text, split_chunks


def item(i, day='2024-01-01', **fields):
    return {'id': str(i), 'date': f'{day}T10:00:00+00:00', **fields}


def medium(kind, state='downloaded'):
    return {'media': [{'kind': kind, 'state': state, 'path': 'media/2024-01/x'}]}


class Index(unittest.TestCase):
    def test_a_busy_month_is_split_into_numbered_chunks(self):
        items = [item(i) for i in range(1, VIEWER_CHUNK_ITEMS * 2 + 2)]
        parts = split_chunks(items)
        entry = month_entry('2024-01', items, parts)
        self.assertEqual([c['name'] for c in entry['chunks']], ['2024-01.1', '2024-01.2', '2024-01.3'])
        self.assertEqual([c['count'] for c in entry['chunks']], [2000, 2000, 1])
        self.assertEqual(entry['chunks'][1]['first_id'], '2001')

    def test_a_group_is_not_split_between_chunks(self):
        items = [item(i, group='7' if 1999 <= i <= 2002 else None) for i in range(1, 4001)]
        self.assertEqual([len(p) for p in split_chunks(items)], [2002, 1998])

    def test_kinds_and_missing_files_are_counted(self):
        items = [item(1, **medium('photo')), item(2, **medium('animation', 'pending')),
                 item(3, **medium('document', 'too_large')), item(4, **medium('contact', 'disabled')), item(5)]
        entry = month_entry('2024-01', items, split_chunks(items))
        self.assertEqual({k: entry[k] for k in ('count', 'photo', 'video', 'other', 'text', 'missing')},
                         {'count': 5, 'photo': 1, 'video': 1, 'other': 2, 'text': 1, 'missing': 3})

    def test_search_text(self):
        self.assertEqual(search_text(item(1, text={'plain': 'a b'}, forward={'from': {'name': 'C'}})), 'a b C')
        self.assertEqual(search_text(item(1)), '')


if __name__ == '__main__':
    unittest.main()
