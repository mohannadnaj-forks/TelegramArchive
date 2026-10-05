"""The first run's question for the API ID and hash, and the runs that do not ask."""
import os
import unittest

from tests.support import ExportRun

HASH = '0123456789abcdef' * 2


class FirstRun(ExportRun):
    without_pair = {'API_ID': '', 'API_HASH': ''}

    def answering(self, *answers):
        self.asked = []
        remaining = iter(answers)

        def ask(prompt):
            self.asked.append(prompt)
            try:
                return next(remaining)
            except StopIteration:
                raise EOFError
        return ask

    def saved(self) -> str:
        with open(os.path.join(self.config, 'settings.env'), encoding='utf-8') as f:
            return f.read()

    def test_the_pair_is_asked_for_saved_and_used(self):
        self.assertEqual(self.run_bot(count=3, env=self.without_pair, ask=self.answering('12345', HASH)), 0, self.output)
        self.assertEqual(self.asked, ['API ID: ', 'API hash: '])
        self.assertIn('https://my.telegram.org', self.output)
        self.assertIn(f'API_ID=12345\nAPI_HASH={HASH}\n', self.saved())
        self.assertIn('MEDIA_EXPORT_PHOTOS=True', self.saved())
        self.assertEqual(len(self.item_ids()), 3)

    def test_the_next_run_does_not_ask(self):
        self.run_bot(count=3, env=self.without_pair, ask=self.answering('12345', HASH))
        before = self.saved()
        self.assertEqual(self.run_bot(count=3, env=self.without_pair, ask=self.answering()), 0, self.output)
        self.assertEqual(self.asked, [])
        self.assertEqual(self.saved(), before)

    def test_a_pair_in_the_environment_is_not_asked_for_or_saved(self):
        self.assertEqual(self.run_bot(count=3, ask=self.answering()), 0, self.output)
        self.assertEqual(self.asked, [])
        self.assertFalse(os.path.exists(os.path.join(self.config, 'settings.env')))

    def test_without_a_terminal_the_run_stops_and_says_where_the_pair_goes(self):
        self.assertEqual(self.run_bot(count=3, env=self.without_pair), 1)
        self.assertIn(os.path.join(self.config, 'settings.env'), self.output)
        self.assertFalse(os.path.exists(self.config))
        self.assertFalse(os.path.exists(self.out))

    def test_rebuilding_the_viewer_does_not_ask(self):
        self.run_bot(count=3)
        self.assertEqual(self.run_bot('--viewer-only', self.export_dir(), chat='', env=self.without_pair,
                                      ask=self.answering()), 0, self.output)
        self.assertEqual(self.asked, [])

    def test_no_answer_stops_the_run_with_nothing_saved(self):
        self.assertEqual(self.run_bot(count=3, env=self.without_pair, ask=self.answering('12345')), 1)
        self.assertIn('No API ID and hash given', self.output)
        self.assertFalse(os.path.exists(self.config))


if __name__ == '__main__':
    unittest.main()
