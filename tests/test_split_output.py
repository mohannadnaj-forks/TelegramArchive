"""JSON_FILE_PAGE_SIZE: result.json split into result_part1.json, result_part2.json, ..."""
import json
import os
import unittest

from tests.support import ExportRun


class SplitOutput(ExportRun):
    def parts(self) -> list:
        names = sorted((n for n in os.listdir(self.export_dir()) if n.startswith('result_part')),
                       key=lambda n: int(n[len('result_part'):-len('.json')]))
        return [self.read_json(n) for n in names]

    def test_parts_hold_consecutive_messages_and_the_chat_fields(self):
        self.assertEqual(self.run_bot(env={'JSON_FILE_PAGE_SIZE': 20000}), 0, self.output)
        self.assertFalse(os.path.exists(self.path('result.json')))
        parts = self.parts()
        self.assertGreater(len(parts), 3)
        ids = [m['id'] for part in parts for m in part['messages']]
        self.assertEqual(ids, list(range(1, 251)))
        for number, part in enumerate(parts, 1):
            self.assertEqual({k: v for k, v in part.items() if k != 'messages'},
                             {'username': 'testchat', 'name': 'Test', 'type': 'public_channel', 'id': '1234'})
            size = os.path.getsize(self.path(f'result_part{number}.json'))
            self.assertLessEqual(size, 20000 + 2000, 'a part passes the page size by at most one message')

    def test_a_split_export_continues_and_leftover_parts_are_removed(self):
        self.run_bot(count=200, env={'JSON_FILE_PAGE_SIZE': 20000})
        before = len(self.parts())
        self.assertEqual(self.run_bot(count=220, env={'JSON_FILE_PAGE_SIZE': 60000}), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(220, 199, -1)))
        parts = self.parts()
        self.assertLess(len(parts), before)
        self.assertEqual([m['id'] for part in parts for m in part['messages']], list(range(1, 221)))

    def test_the_viewer_is_built_from_the_parts(self):
        self.run_bot(env={'JSON_FILE_PAGE_SIZE': 20000})
        with open(self.path('data', 'index.js'), encoding='utf-8') as f:
            index = json.loads(f.read().removeprefix('archiveIndex(').removesuffix(');\n'))
        self.assertEqual(index['total'], 250)
        self.assertIsNotNone(index['updated'])

    def test_switching_back_to_one_file_keeps_the_messages_listed_since(self):
        self.run_bot(count=100, env={'JSON_FILE_PAGE_SIZE': 20000})
        self.assertEqual(self.run_bot(count=150), 0, self.output)
        self.assertEqual(self.run_bot(count=150), 0, self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(1, 151)))
        self.assertEqual(self.parts(), [])

    def test_switching_to_parts_keeps_the_messages_listed_since(self):
        self.run_bot(count=100)
        self.assertEqual(self.run_bot(count=150, env={'JSON_FILE_PAGE_SIZE': 20000}), 0, self.output)
        self.assertFalse(os.path.exists(self.path('result.json')))
        self.assertEqual(self.run_bot(count=150, env={'JSON_FILE_PAGE_SIZE': 20000}), 0, self.output)
        self.assertEqual([m['id'] for part in self.parts() for m in part['messages']], list(range(1, 151)))


if __name__ == '__main__':
    unittest.main()
