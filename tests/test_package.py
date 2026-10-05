"""What an installed copy relies on: the files shipped inside the package, and the command's entry point."""
import importlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
from importlib import resources

from tests.support import ROOT


class Package(unittest.TestCase):
    def test_the_viewer_page_and_the_example_settings_are_inside_the_package(self):
        for name in ('_index.html', 'settings.example.env'):
            self.assertTrue(resources.files('hamstra_telegram').joinpath(name).is_file(), name)
        self.assertFalse(os.path.exists(os.path.join(ROOT, '_index.html')))

    def test_the_command_points_at_the_command_line(self):
        with open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8') as f:
            module, function = re.search(r'^hamstra-telegram = "([\w.]+):(\w+)"$', f.read(), re.M).groups()
        self.assertTrue(callable(getattr(importlib.import_module(module), function)))

    def test_the_package_runs_as_a_module_from_another_folder(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            env = {**os.environ, 'PYTHONPATH': ROOT, 'PYTHONIOENCODING': 'utf-8'}
            result = subprocess.run([sys.executable, '-m', 'hamstra_telegram', '--help'], cwd=elsewhere, env=env,
                                    capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('usage: hamstra-telegram', result.stdout)


if __name__ == '__main__':
    unittest.main()
