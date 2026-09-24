"""Running the program against the fake client (see fake_telegram.py).

ExportRun calls telegram_archive.cli.run in this process: fast, and what most tests use.
SubprocessRun copies the program into a temporary directory and runs bot.py there, so that the real
Ctrl-C, a real kill, the session lock between processes and the .env lookup behave as they do for a
user; test_end_to_end.py and test_scale.py use it.

Both record every call to the fake in a log, read back with calls().
"""
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from contextlib import redirect_stderr, redirect_stdout

from telegram_archive import cli
from telegram_archive.telegram import SESSION_NAME
from tests.fake_telegram import make_fake_client, recording_sleep

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, 'tests')
PROGRAM_FILES = ('bot.py', '_index.html')
PROGRAM_PACKAGES = ('telegram_archive',)


def copy_program(destination: str, source: str = ROOT) -> None:
    os.makedirs(destination, exist_ok=True)
    for name in PROGRAM_FILES:
        shutil.copy(os.path.join(source, name), destination)
    for name in PROGRAM_PACKAGES:
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


def run_program(program: str, args: list, scenario: dict, env: dict, timeout: int = 300) -> subprocess.CompletedProcess:
    env = {**env, 'FAKE_TELEGRAM': json.dumps(scenario)}
    return subprocess.run([sys.executable, os.path.join(TESTS, 'fake_run.py'), program, *args],
                          env=env, capture_output=True, text=True, encoding='utf-8', timeout=timeout)


class SimulatedKill(BaseException):
    """Raised where the scenario kills the program: nothing in the exporter catches it, so nothing more is saved."""


def simulated_kill():
    raise SimulatedKill


class ExportChecks:
    chat = 'testchat'

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='telegram-archive-test-')
        self.out = os.path.join(self.dir, 'out')
        self.log = os.path.join(self.dir, 'calls.jsonl')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def arguments(self, args, chat) -> list:
        chats = [] if '--all' in args or chat == '' else chat if isinstance(chat, list) else [chat or self.chat]
        return [*chats, '-o', self.out, *args]

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


class ExportRun(ExportChecks, unittest.TestCase):
    """Runs the program in this process. The exit code is what a user's shell would see; 9 for a kill."""

    def run_bot(self, *args, checkpoint_seconds=10, env=None, chat=None, **scenario) -> int:
        open(self.log, 'w').close()
        scenario = {'count': 250, **scenario, 'log': self.log}
        client_class = make_fake_client(scenario, kill=simulated_kill)
        settings = base_env(CHECKPOINT_SECONDS=checkpoint_seconds, **(env or {}))
        out, err = io.StringIO(), io.StringIO()
        root = logging.getLogger()
        handler, level = logging.StreamHandler(err), root.level
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                cli.run(self.arguments(args, chat), settings,
                        client_factory=lambda s, session_dir: client_class(SESSION_NAME, workdir=session_dir),
                        session_dir=os.path.join(self.dir, 'session'), sleep=recording_sleep(self.log))
        except SimulatedKill:
            code = 9
        except SystemExit as e:
            if isinstance(e.code, str):
                err.write(e.code)
            code = e.code if isinstance(e.code, int) else 0 if e.code is None else 1
        except Exception:
            err.write(traceback.format_exc())
            code = 1
        finally:
            root.removeHandler(handler)
            root.setLevel(level)
        self.output = out.getvalue() + err.getvalue()
        return code


class SubprocessRun(ExportChecks, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.program = os.path.join(self.dir, 'program')
        copy_program(self.program)

    def run_bot(self, *args, checkpoint_seconds=10, env=None, chat=None, **scenario) -> int:
        open(self.log, 'w').close()
        scenario = {'count': 250, **scenario, 'log': self.log}
        settings = base_env(CHECKPOINT_SECONDS=checkpoint_seconds, **(env or {}))
        result = run_program(self.program, self.arguments(args, chat), scenario, settings)
        self.output = result.stdout + result.stderr
        return result.returncode


def message_id_of(file_name: str) -> int:
    # photo_12.jpg, video_12.mp4 or 12_name.ext
    stem = file_name.split('.')[0]
    return int(stem.split('_')[1] if not stem[0].isdigit() else stem.split('_')[0])
