"""Running the whole program in a subprocess against the fake client (see fake_telegram.py).

Each test copies the program into a temporary directory, so that the session lock, the .telegram
folder and any .env lookup stay inside it, and runs it there with fake_run.py.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, 'tests')
PROGRAM_FILES = ('bot.py', 'configs.py', 'chats.py', '_index.html')
PROGRAM_PACKAGES = ('telegram_archive',)


def copy_program(destination: str, source: str = ROOT) -> None:
    os.makedirs(destination, exist_ok=True)
    for name in PROGRAM_FILES:
        if os.path.exists(os.path.join(source, name)):
            shutil.copy(os.path.join(source, name), destination)
    for name in PROGRAM_PACKAGES:
        if os.path.isdir(os.path.join(source, name)):
            shutil.copytree(os.path.join(source, name), os.path.join(destination, name),
                            ignore=shutil.ignore_patterns('__pycache__'))


def base_env(**settings) -> dict:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('MEDIA_EXPORT_', 'CHAT_EXPORT_', 'JSON_FILE_PAGE_SIZE'))}
    env.update({'API_ID': '1', 'API_HASH': 'x', 'DOWNLOAD_PATH': '', 'MIN_FREE_DISK_MB': '0',
                'PYTHONIOENCODING': 'utf-8', 'CHECKPOINT_SECONDS': '10',
                'MEDIA_EXPORT_PHOTOS': 'True', 'MEDIA_EXPORT_VIDEOS': 'True'})
    env.update({key: str(value) for key, value in settings.items()})
    return env


def run_program(program: str, args: list, scenario: dict, env: dict, timeout: int = 120) -> subprocess.CompletedProcess:
    env = {**env, 'FAKE_TELEGRAM': json.dumps(scenario)}
    return subprocess.run([sys.executable, os.path.join(TESTS, 'fake_run.py'), program, *args],
                          env=env, capture_output=True, text=True, encoding='utf-8', timeout=timeout)


class ExportRun(unittest.TestCase):
    chat = 'testchat'

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.program = os.path.join(self.dir, 'program')
        copy_program(self.program)
        self.out = os.path.join(self.dir, 'out')
        self.log = os.path.join(self.dir, 'calls.jsonl')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_bot(self, *args, checkpoint_seconds=10, env=None, chat=None, **scenario):
        open(self.log, 'w').close()
        scenario = {'count': 250, **scenario, 'log': self.log}
        chats = [] if '--all' in args or chat == '' else [chat or self.chat]
        settings = base_env(CHECKPOINT_SECONDS=checkpoint_seconds, **(env or {}))
        result = run_program(self.program, [*chats, '-o', self.out, *args], scenario, settings)
        self.output = result.stdout + result.stderr
        return result.returncode

    def calls(self, kind: str) -> list:
        with open(self.log, encoding='utf-8') as f:
            return [c for c in map(json.loads, f) if c['call'] == kind]

    def export_dir(self) -> str:
        [name] = os.listdir(self.out)
        return os.path.join(self.out, name)

    def path(self, *parts) -> str:
        return os.path.join(self.export_dir(), *parts)

    def read_json(self, *parts):
        with open(self.path(*parts), encoding='utf-8') as f:
            return json.load(f)

    def result(self) -> dict:
        return self.read_json('result.json')

    def records(self) -> dict:
        return {m['id']: m for m in self.result()['messages']}

    def state(self) -> dict:
        return self.read_json('export_state.json')

    def listed_ids(self) -> list:
        return [c['id'] for c in self.calls('yield')]

    def downloaded_files(self) -> list:
        return [c['path'] for c in self.calls('download') if not c['path'].endswith('_thumb.jpg')]

    def sleeps(self) -> list:
        return [c['seconds'] for c in self.calls('sleep')]

    def file_states(self) -> dict:
        states = {}
        for m in self.result()['messages']:
            if 'file_status' in m:
                states[m['file_status']['state']] = states.get(m['file_status']['state'], 0) + 1
        return states


def message_id_of(file_name: str) -> int:
    # photo_12.jpg, video_12.mp4 or 12_name.ext
    stem = file_name.split('.')[0]
    return int(stem.split('_')[1] if not stem[0].isdigit() else stem.split('_')[0])
