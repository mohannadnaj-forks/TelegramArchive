"""Runs bot.py end to end against a fake Telegram client, without a network or a login.

Each run is a subprocess on a copy of the program in a temporary directory, so the session lock
and the Ctrl-C handling behave as they do for real. The fake chat's messages are numbered 1..count,
one hour apart; every third has a photo and every fifth a video.
"""
import json
import os
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM_FILES = ('bot.py', 'configs.py', 'chats.py', '_index.html')
BASE_DATE = datetime(2024, 1, 1)
PAGE = 100


def install_fake_client(scenario: dict) -> None:
    import pyrogram
    from pyrogram.enums import ChatType
    from pyrogram.file_id import FileId, FileType, ThumbnailSource

    deleted = set(scenario.get('deleted', []))
    ids = [i for i in range(1, scenario['count'] + 1) if i not in deleted]
    counters = {'listed': 0, 'downloads': 0}

    def log(entry: dict) -> None:
        with open(scenario['log'], 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    class Text(str):
        entities = None

    def make_message(i: int):
        photo = video = None
        if i % 3 == 0:
            file_id = FileId(file_type=FileType.PHOTO, dc_id=2, media_id=i, access_hash=1, file_reference=b'',
                             volume_id=0, local_id=0, thumbnail_source=ThumbnailSource.THUMBNAIL,
                             thumbnail_file_type=FileType.PHOTO, thumbnail_size='y').encode()
            photo = SimpleNamespace(file_id=file_id, file_size=1000 + i, width=800, height=600, thumbs=None)
        elif i % 5 == 0:
            video = SimpleNamespace(file_id=f'video:{i}', file_size=5000 + i, file_name=None, mime_type='video/mp4',
                                    duration=3, width=640, height=360, thumbs=None)
        message = SimpleNamespace(
            id=i, date=BASE_DATE + timedelta(hours=i), empty=False,
            sender_chat=SimpleNamespace(id=-1001234, title='Test'), from_user=None,
            reply_to_message_id=None, media_group_id=None, views=1, forward_from_chat=None, forward_from=None,
            contact=None, location=None, text=None if photo or video else Text(f'message {i}'),
            caption=None, caption_entities=None, photo=photo, video=video,
        )
        for attr in ('animation', 'sticker', 'video_note', 'audio', 'voice', 'document'):
            setattr(message, attr, None)
        return message

    def size_of(file_id: str) -> int:
        if file_id.startswith('video:'):
            return 5000 + int(file_id.split(':')[1])
        decoded = FileId.decode(file_id)
        return 1000 + decoded.media_id if decoded.thumbnail_size == 'y' else 10

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_chat(self, chat_id):
            return SimpleNamespace(id=-1001234, type=ChatType.CHANNEL, title='Test', username='testchat',
                                   photo=None, description=None)

        async def get_chat_history_count(self, chat_id):
            return len(ids)

        async def get_chat_history(self, chat_id, limit=0, offset=0, offset_id=None, offset_date=None,
                                   min_id=0, max_id=0, reverse=False):
            # Kurigram: newest first, max_id inclusive, offset_date returns messages older than it.
            selected = [i for i in reversed(ids)
                        if (not max_id or i <= max_id) and (offset_date is None or BASE_DATE + timedelta(hours=i) < offset_date)]
            for start in range(0, len(selected), PAGE):
                page = selected[start:start + PAGE]
                log({'call': 'history', 'max_id': max_id, 'page': [page[0], page[-1]]})
                for i in page:
                    log({'call': 'yield', 'id': i})
                    counters['listed'] += 1
                    if counters['listed'] == scenario.get('fail_after_listed'):
                        raise ConnectionError('network went away')
                    yield make_message(i)
                    if counters['listed'] == scenario.get('interrupt_after_listed'):
                        signal.raise_signal(signal.SIGINT)

        async def get_messages(self, chat_id, message_ids):
            log({'call': 'get_messages', 'ids': list(message_ids)})
            return [make_message(i) if i in ids else SimpleNamespace(id=i, empty=True) for i in message_ids]

        async def download_media(self, file_id, file_name):
            log({'call': 'download', 'path': os.path.basename(file_name).removesuffix('.tmp')})
            with open(file_name, 'wb') as f:
                f.write(b'x' * size_of(file_id))
            counters['downloads'] += 1
            if counters['downloads'] == scenario.get('interrupt_after_downloads'):
                signal.raise_signal(signal.SIGINT)

    pyrogram.Client = FakeClient


class ExportRun(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.program = os.path.join(self.dir, 'program')
        os.makedirs(self.program)
        for name in PROGRAM_FILES:
            shutil.copy(os.path.join(HERE, name), self.program)
        self.out = os.path.join(self.dir, 'out')
        self.log = os.path.join(self.dir, 'calls.jsonl')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_bot(self, *args, **scenario):
        open(self.log, 'w').close()
        scenario = {'count': 250, **scenario, 'log': self.log}
        env = {**os.environ, 'API_ID': '1', 'API_HASH': 'x', 'DOWNLOAD_PATH': '', 'MIN_FREE_DISK_MB': '0',
               'FAKE_TELEGRAM': json.dumps(scenario), 'PYTHONIOENCODING': 'utf-8'}
        for kind in ('PHOTOS', 'VIDEOS'):
            env[f'MEDIA_EXPORT_{kind}'] = 'True'
        result = subprocess.run(
            [sys.executable, os.path.abspath(__file__), '--bot', self.program, 'testchat', '-o', self.out, *args],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=120,
        )
        self.output = result.stdout + result.stderr
        return result.returncode

    def calls(self, kind: str) -> list:
        with open(self.log, encoding='utf-8') as f:
            return [c for c in map(json.loads, f) if c['call'] == kind]

    def export_dir(self) -> str:
        [name] = os.listdir(self.out)
        return os.path.join(self.out, name)

    def result(self) -> dict:
        with open(os.path.join(self.export_dir(), 'result.json'), encoding='utf-8') as f:
            return json.load(f)

    def state(self) -> dict:
        with open(os.path.join(self.export_dir(), 'export_state.json'), encoding='utf-8') as f:
            return json.load(f)

    def listed_ids(self) -> list:
        return [c['id'] for c in self.calls('yield')]

    def downloaded_files(self) -> list:
        return [c['path'] for c in self.calls('download') if not c['path'].endswith('_thumb.jpg')]

    def file_states(self) -> dict:
        states = {}
        for m in self.result()['messages']:
            if 'file_status' in m:
                states[m['file_status']['state']] = states.get(m['file_status']['state'], 0) + 1
        return states


class FullExport(ExportRun):
    def test_first_run_lists_everything_and_downloads_every_file(self):
        self.assertEqual(self.run_bot(), 0, self.output)
        messages = self.result()['messages']
        self.assertEqual([m['id'] for m in messages], list(range(1, 251)))
        self.assertEqual(self.file_states(), {'downloaded': 83 + 34})
        self.assertEqual(self.state()['listed'], [[1, 250]])
        self.assertEqual(self.state()['run']['status'], 'complete')
        self.assertTrue(os.path.exists(os.path.join(self.export_dir(), 'index.html')))

    def test_a_later_run_lists_only_new_messages(self):
        self.run_bot(count=200)
        self.assertEqual(self.run_bot(count=230), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(230, 199, -1)))
        self.assertTrue(all(int(p.split('_')[1].split('.')[0]) > 200 for p in self.downloaded_files()))
        self.assertEqual(len(self.result()['messages']), 230)
        self.assertEqual(self.state()['listed'], [[1, 230]])

    def test_an_up_to_date_export_reads_one_page(self):
        self.run_bot()
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(len(self.calls('history')), 1)
        self.assertEqual(self.calls('download'), [])


class StoppingDuringListing(ExportRun):
    def test_ctrl_c_keeps_what_was_listed_and_the_next_run_continues_below_it(self):
        self.assertEqual(self.run_bot(count=350, interrupt_after_listed=120), 0, self.output)
        self.assertIn('Progress saved', self.output)
        ids = [m['id'] for m in self.result()['messages']]
        self.assertEqual(ids, list(range(231, 351)))
        self.assertEqual(self.state()['listed'], [[231, 350]])
        self.assertEqual(self.state()['run']['status'], 'stopped')
        self.assertEqual(self.calls('download'), [])
        self.assertEqual(self.file_states(), {'pending': 40 + 16})

        self.assertEqual(self.run_bot(count=350), 0, self.output)
        self.assertEqual([i for i in self.listed_ids() if i >= 231], [350], 'only the newest, which is found covered')
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(1, 351)))
        self.assertEqual(self.state()['listed'], [[1, 350]])
        self.assertEqual(self.file_states(), {'downloaded': 116 + 47})

    def test_a_network_failure_keeps_what_was_listed(self):
        self.assertNotEqual(self.run_bot(fail_after_listed=150), 0)
        self.assertIn('network went away', self.output)
        self.assertEqual(len(self.result()['messages']), 149)
        self.assertEqual(self.state()['run']['status'], 'failed')
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(len(self.result()['messages']), 250)

    def test_new_messages_posted_between_runs_are_listed_before_continuing(self):
        self.run_bot(count=300, interrupt_after_listed=50)
        self.assertEqual(self.state()['listed'], [[251, 300]])
        self.assertEqual(self.run_bot(count=320), 0, self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(1, 321)))
        self.assertEqual(self.state()['listed'], [[1, 320]])
        self.assertNotIn(275, self.listed_ids())


class StoppingDuringDownloads(ExportRun):
    def test_ctrl_c_during_downloads_resumes_without_downloading_twice(self):
        self.assertEqual(self.run_bot(interrupt_after_downloads=10), 0, self.output)
        states = self.file_states()
        first = self.downloaded_files()
        self.assertEqual(states.get('downloaded'), len(first))
        self.assertEqual(self.state()['run']['stage'], 'downloading')
        self.assertEqual(self.run_bot(), 0, self.output)
        second = self.downloaded_files()
        self.assertFalse(set(first) & set(second))
        self.assertEqual(len(first) + len(second), 83 + 34)
        self.assertEqual(len(self.calls('history')), 1)

    def test_newest_files_are_downloaded_first(self):
        self.run_bot(interrupt_after_downloads=5)
        ids = [int(p.split('_')[1].split('.')[0]) for p in self.downloaded_files()]
        self.assertEqual(ids, sorted(ids, reverse=True))
        self.assertEqual(ids[0], 250)


class SizeLimits(ExportRun):
    def test_total_size_limit_keeps_the_newest_files_and_reports_the_full_size(self):
        self.assertEqual(self.run_bot('--max-total-size', '20K'), 0, self.output)
        downloaded = sorted(m['id'] for m in self.result()['messages'] if m.get('file_status', {}).get('state') == 'downloaded')
        left_out = [m['id'] for m in self.result()['messages'] if m.get('file_status', {}).get('state') == 'total_limit']
        self.assertTrue(downloaded and left_out)
        self.assertIn(250, downloaded)
        self.assertGreater(sum(downloaded) / len(downloaded), sum(left_out) / len(left_out))
        self.assertIn('left out by --max-total-size', self.output)
        self.assertIn('A complete export needs about', self.output)
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.file_states(), {'downloaded': 83 + 34})
        self.assertEqual(len(self.calls('history')), 1)


class OlderExports(ExportRun):
    def test_an_export_without_state_is_listed_again_and_keeps_its_files(self):
        self.run_bot(count=100)
        os.remove(os.path.join(self.export_dir(), 'export_state.json'))
        self.assertEqual(self.run_bot(count=100), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(100, 0, -1)))
        self.assertEqual(self.calls('download'), [])


class DateRanges(ExportRun):
    # Message i is dated BASE_DATE + i hours: ids 24..47 are 2024-01-02, 48..71 are 2024-01-03.
    def test_a_range_lists_only_its_messages_and_resumes_without_relisting(self):
        self.assertEqual(self.run_bot('--since', '2024-01-02', '--until', '2024-01-03', interrupt_after_listed=10), 0, self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(62, 72)))
        self.assertEqual(self.run_bot('--since', '2024-01-02', '--until', '2024-01-03'), 0, self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(24, 72)))
        self.assertFalse(set(self.listed_ids()) & set(range(62, 71)))
        downloaded = {int(c['path'].split('_')[1].split('.')[0]) for c in self.calls('download')}
        self.assertTrue(downloaded and all(24 <= i <= 71 for i in downloaded))
        self.assertEqual(self.state()['listed'], [[24, 71]])

    def test_a_full_run_after_a_range_skips_the_range(self):
        self.run_bot('--since', '2024-01-02', '--until', '2024-01-03')
        self.assertEqual(self.run_bot(count=100), 0, self.output)
        self.assertFalse(set(self.listed_ids()) & set(range(24, 72)) - {71})
        self.assertEqual(self.state()['listed'], [[1, 100]])
        self.assertEqual(len(self.result()['messages']), 100)


class Refresh(ExportRun):
    def test_refresh_lists_the_whole_history_again(self):
        self.run_bot(count=150)
        self.assertEqual(self.run_bot('--refresh', count=150), 0, self.output)
        self.assertEqual(self.listed_ids(), list(range(150, 0, -1)))
        self.assertEqual(self.calls('download'), [])


if __name__ == '__main__' and sys.argv[1:2] == ['--bot']:
    program = sys.argv[2]
    install_fake_client(json.loads(os.environ['FAKE_TELEGRAM']))
    sys.argv = [os.path.join(program, 'bot.py'), *sys.argv[3:]]
    sys.path.insert(0, program)
    os.chdir(program)
    runpy.run_path(os.path.join(program, 'bot.py'), run_name='__main__')
elif __name__ == '__main__':
    unittest.main()
