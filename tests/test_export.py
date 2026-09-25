"""Whole runs against the fake Telegram client (tests/fake_telegram.py): listing, resuming, stopping, limits.

The fake chat's messages are numbered 1..count, one hour apart; every third has a photo and every
fifth a video. test_end_to_end.py repeats the Ctrl-C and kill cases in a real subprocess.
"""
import json
import os
import unittest

from tests.support import ExportRun


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


class Killed(ExportRun):
    def test_a_killed_listing_keeps_what_it_saved_and_the_next_run_continues(self):
        self.assertEqual(self.run_bot(checkpoint_seconds=0, kill_after_listed=120), 9)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(131, 251)))
        self.assertEqual(self.state()['listed'], [[131, 250]])
        self.assertEqual(self.state()['run']['status'], 'running', 'a killed run never gets to say otherwise')

        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual([m['id'] for m in self.result()['messages']], list(range(1, 251)))
        self.assertFalse(set(self.listed_ids()) & set(range(131, 250)))
        self.assertEqual([r['status'] for r in self.archive()['runs']], ['running', 'complete'])

    def test_a_kill_loses_only_what_was_not_saved_yet(self):
        self.assertEqual(self.run_bot(kill_after_listed=120, clock=lambda: 0), 9)
        self.assertEqual(self.result()['messages'], [])
        self.assertEqual(self.archive()['runs'], [])


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


if __name__ == '__main__':
    unittest.main()
