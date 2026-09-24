"""Writes tests/fixtures/legacy/<scenario>/: exports made by an earlier version of the program.

    python tests/fixtures/make_legacy_exports.py [revision]    (default: e7faf7f)

Each scenario runs that revision's bot.py (taken with `git show`) against the fake client's 'basic'
chat, in the states an export can be left in: finished, stopped or killed during either pass, split
into parts, limited by size or settings. test_legacy_exports.py resumes them with the current code.
The viewer (index.html, data/) is left out; every run rebuilds it.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(TESTS))
from tests.support import base_env, run_program  # noqa: E402

FILES = ('bot.py', 'configs.py', 'chats.py', '_index.html')

# name -> list of runs, each (arguments, scenario, settings); the runs happen in order on one export.
SCENARIOS = {
    'complete': [([], {'count': 60}, {})],
    'stopped_listing': [([], {'count': 90, 'interrupt_after_listed': 40}, {})],
    'stopped_downloading': [([], {'count': 90, 'interrupt_after_downloads': 8}, {})],
    'killed_listing': [([], {'count': 90, 'kill_after_listed': 45}, {'CHECKPOINT_SECONDS': 0})],
    'killed_downloading': [
        ([], {'count': 90, 'interrupt_after_listed': 30}, {}),
        ([], {'count': 90, 'kill_after_downloads': 6}, {'CHECKPOINT_SECONDS': 0}),
    ],
    'split': [([], {'count': 90}, {'JSON_FILE_PAGE_SIZE': 20000})],
    'date_range_stopped': [(['--since', '2024-01-02', '--until', '2024-01-03'], {'count': 90, 'interrupt_after_listed': 10}, {})],
    'limits': [(['--max-total-size', '10K', '--max-file-size', '1050'],
                {'count': 90, 'download_errors': {'photo_48.jpg': ['network'] * 5}}, {'MEDIA_EXPORT_VIDEOS': 'False'})],
}


def main() -> None:
    revision = sys.argv[1] if len(sys.argv) > 1 else 'e7faf7f'
    root = os.path.join(HERE, 'legacy')
    shutil.rmtree(root, ignore_errors=True)
    with tempfile.TemporaryDirectory() as work:
        program = os.path.join(work, 'program')
        os.makedirs(program)
        for name in FILES:
            content = subprocess.run(['git', 'show', f'{revision}:{name}'], cwd=os.path.dirname(TESTS),
                                     capture_output=True, check=True).stdout
            with open(os.path.join(program, name), 'wb') as f:
                f.write(content)
        for name, runs in SCENARIOS.items():
            out = os.path.join(work, name)
            log = os.path.join(work, f'{name}.jsonl')
            codes = []
            for args, scenario, settings in runs:
                result = run_program(program, ['testchat', '-o', out, *args], {**scenario, 'log': log},
                                     base_env(**settings))
                codes.append(result.returncode)
            [export] = os.listdir(out)
            destination = os.path.join(root, name, export)
            shutil.copytree(os.path.join(out, export), destination,
                            ignore=shutil.ignore_patterns('index.html', 'data'))
            with open(os.path.join(root, name, 'made_by.json'), 'w', encoding='utf-8') as f:
                json.dump({'revision': revision, 'runs': runs, 'exit_codes': codes}, f, indent=2)
                f.write('\n')
            print(name, codes, sorted(os.listdir(destination)))


if __name__ == '__main__':
    main()
