"""The files written for the viewer (index.html, data/*.js), and --viewer-only."""
import json
import os
import shutil
import unittest

from tests.support import ROOT, ExportRun


def read_js(path: str, call: str):
    with open(path, encoding='utf-8') as f:
        content = f.read()
    assert content.startswith(call + '(') and content.endswith(');\n'), content[:80]
    return json.loads('[' + content[len(call) + 1:-3] + ']')


class ViewerRun(ExportRun):
    def index(self) -> dict:
        return read_js(self.path('data', 'index.js'), 'archiveIndex')[0]

    def chunk(self, name: str) -> list:
        chunk_name, records = read_js(self.path('data', f'{name}.js'), 'archiveChunk')
        self.assertEqual(chunk_name, name)
        return records

    def search(self) -> list:
        return read_js(self.path('data', 'search.js'), 'archiveSearch')[0]

    def viewer_only(self, *targets) -> int:
        return self.run_bot('--viewer-only', *targets, chat='')


class ViewerData(ViewerRun):
    def test_months_counts_and_chunks(self):
        self.assertEqual(self.run_bot(count=1500, env={'MEDIA_EXPORT_VIDEOS': 'False'}), 0, self.output)
        index = self.index()
        self.assertEqual(index['source'], 'telegram')
        self.assertEqual(index['chat'], {'username': 'testchat', 'name': 'Test', 'type': 'public_channel', 'id': '1234'})
        self.assertEqual(index['total'], 1500)
        self.assertRegex(index['updated'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$')
        self.assertEqual([m['key'] for m in index['months']], ['2024-01', '2024-02', '2024-03'])
        january = index['months'][0]
        # ids 1..743 are in January (hour 744 is 1 February 00:00)
        photos = len(range(3, 744, 3))
        videos = len([i for i in range(1, 744) if i % 5 == 0 and i % 3])
        self.assertEqual(january, {
            'key': '2024-01', 'count': 743, 'video': videos, 'photo': photos, 'other': 0, 'text': 743 - photos - videos,
            'missing': videos,
            'chunks': [{'name': '2024-01', 'count': 743, 'first_id': 1, 'last_id': 743,
                        'first_date': '2024-01-01T01:00:00', 'last_date': '2024-01-31T23:00:00', 'missing': videos}]})
        records = {m['id']: m for m in self.result()['messages']}
        self.assertEqual(self.chunk('2024-01'), [records[i] for i in range(1, 744)])
        self.assertEqual(self.search()[:2], [[1, '2024-01-01T01:00', 'message 1'], [2, '2024-01-01T02:00', 'message 2']])
        self.assertEqual(self.search()[2], [3, '2024-01-01T03:00', ''])
        with open(os.path.join(ROOT, '_index.html'), encoding='utf-8') as template, \
                open(self.path('index.html'), encoding='utf-8') as page:
            self.assertEqual(page.read(), template.read())

    def test_search_rows_join_text_caption_and_forwarded_from(self):
        env = {'MEDIA_EXPORT_DOCUMENTS': 'True'}
        self.assertEqual(self.run_bot(count=20, messages='rich', env=env), 0, self.output)
        rows = {row[0]: row[2] for row in self.search()}
        self.assertEqual(rows[2], '😀 bold and a link, `code`')
        self.assertEqual(rows[3], 'Photo #news')
        self.assertEqual(rows[18], 'forwarded from a channel Other Channel')
        index = self.index()['months'][0]
        self.assertEqual((index['photo'], index['video'], index['other'], index['text']), (4, 4, 5, 7))

    def test_files_from_an_earlier_build_are_removed(self):
        self.run_bot(count=10)
        for name in ('2019-01.js', 'stale.txt'):
            open(self.path('data', name), 'w').close()
        open(self.path('data.js'), 'w').close()
        self.assertEqual(self.run_bot(count=10), 0, self.output)
        self.assertEqual(sorted(os.listdir(self.path('data'))), ['2024-01.js', 'index.js', 'search.js'])
        self.assertFalse(os.path.exists(self.path('data.js')))


class ViewerOnly(ViewerRun):
    def test_rebuilds_from_the_export_directory_without_telegram(self):
        self.run_bot(count=30)
        shutil.rmtree(self.path('data'))
        os.remove(self.path('index.html'))
        self.assertEqual(self.viewer_only(self.export_dir()), 0, self.output)
        self.assertIn('Rebuilt viewer for Test: 30 messages', self.output)
        self.assertEqual(self.index()['total'], 30)
        self.assertTrue(os.path.exists(self.path('index.html')))
        self.assertEqual(self.calls('client'), [])

    def test_rebuilds_from_the_chat_name_under_the_output_folder(self):
        self.run_bot(count=30)
        os.remove(self.path('data', 'index.js'))
        self.assertEqual(self.viewer_only('https://t.me/testchat'), 0, self.output)
        self.assertEqual(self.index()['total'], 30)

    def test_rebuilds_from_parts(self):
        self.run_bot(env={'JSON_FILE_PAGE_SIZE': 20000})
        self.assertEqual(self.viewer_only(self.export_dir()), 0, self.output)
        self.assertEqual(self.index()['total'], 250)

    def test_a_chat_without_an_export_is_reported(self):
        self.assertEqual(self.viewer_only('nobody'), 0, self.output)
        self.assertIn('No result.json under', self.output)

    def test_all_is_refused(self):
        self.assertEqual(self.run_bot('--viewer-only', '--all'), 2)
        self.assertIn('--viewer-only needs the chats or export directories to rebuild', self.output)


if __name__ == '__main__':
    unittest.main()
