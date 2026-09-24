"""Runs the program in <program dir> against the fake client: fake_run.py <program dir> [bot.py arguments].

The scenario comes from the FAKE_TELEGRAM environment variable (JSON, see fake_telegram.py).
"""
import json
import os
import runpy
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fake_telegram import install_fake_client, install_fast_sleep  # noqa: E402

program = os.path.abspath(sys.argv[1])
scenario = json.loads(os.environ['FAKE_TELEGRAM'])
install_fake_client(scenario)
if scenario.get('fast_sleep', True):
    install_fast_sleep(scenario['log'])
if scenario.get('memory_every'):
    import tracemalloc
    tracemalloc.start()
sys.argv = [os.path.join(program, 'bot.py'), *sys.argv[2:]]
sys.path[0] = program
os.chdir(program)
runpy.run_path(os.path.join(program, 'bot.py'), run_name='__main__')
