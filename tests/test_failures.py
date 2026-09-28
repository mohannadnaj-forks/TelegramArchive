"""How the export behaves when Telegram, the network or the disk gets in the way.

Waits are recorded by the fake instead of slept (install_fast_sleep), so the tests check the
durations the program asked for.
"""
import os
import unittest

from tests.support import ExportRun

VIDEO = '250.mp4'  # the newest file of the basic chat, downloaded first


class DownloadRetries(ExportRun):
    def status_of(self, message_id: int) -> dict:
        return {k: v for k, v in self.medium(message_id).items() if k in ('state', 'error', 'limit', 'setting')}

    def attempts(self, name: str) -> list:
        return [c['attempt'] for c in self.calls('download') if c['path'] == name]

    def test_a_flood_wait_is_waited_out_and_the_download_retried(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['flood:30']}), 0, self.output)
        self.assertEqual(self.status_of(250)['state'], 'downloaded')
        self.assertEqual(self.sleeps(), [30])
        self.assertEqual([c['attempt'] for c in self.calls('download') if c['path'] == VIDEO], [1, 2])

    def test_a_flood_wait_longer_than_the_maximum_fails_the_file_and_the_run_goes_on(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['flood:30']}, env={'FLOOD_WAIT_MAX_SLEEP': 10}), 0, self.output)
        self.assertEqual(self.status_of(250), {'state': 'failed', 'error': 'FloodWait of 30s exceeds FLOOD_WAIT_MAX_SLEEP'})
        self.assertEqual(self.sleeps(), [])
        self.assertEqual(self.file_states(), {'downloaded': 116, 'failed': 1})
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.downloaded_files(), [VIDEO])
        self.assertEqual(self.file_states(), {'downloaded': 117})

    def test_flood_waits_on_every_attempt_are_recorded_as_such(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['flood:5'] * 5}), 0, self.output)
        self.assertEqual(self.sleeps(), [5] * 5)
        self.assertEqual(self.status_of(250)['state'], 'failed')
        self.assertIn('FLOOD_WAIT', self.status_of(250)['error'])

    def test_errors_are_retried_with_growing_waits_then_recorded(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.status_of(250), {'state': 'failed', 'error': 'network went away'})
        self.assertEqual(self.sleeps(), [2, 4, 8, 16])
        self.assertEqual(self.medium(250)['path'], 'media/2024-01/250.mp4', 'the path stays where the file will go')

    def test_a_file_that_failed_in_two_runs_gets_one_attempt_per_run(self):
        for run in (1, 2):
            self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
            self.assertEqual(self.medium(250)['failures'], run)
            self.assertEqual(self.attempts(VIDEO), [1, 2, 3, 4, 5])
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.attempts(VIDEO), [1])
        self.assertEqual(self.medium(250)['failures'], 3)
        self.assertEqual(self.run_bot('--retry-failed'), 0, self.output)
        self.assertEqual(self.status_of(250)['state'], 'downloaded')
        self.assertNotIn('failures', self.medium(250))

    def test_the_failure_count_survives_a_refresh(self):
        for _ in (1, 2):
            self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.run_bot('--refresh', download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.attempts(VIDEO), [1])
        self.assertEqual(self.medium(250)['failures'], 3)

    def test_a_file_that_failed_in_three_runs_is_left_alone_unless_asked(self):
        for _ in (1, 2, 3):
            self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.medium(250)['failures'], 3)
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['network'] * 5}), 0, self.output)
        self.assertEqual(self.attempts(VIDEO), [])
        self.assertIn('1 files failed in 3 runs or more', self.output)
        self.assertEqual(self.run_bot('--retry-failed'), 0, self.output)
        self.assertEqual(self.attempts(VIDEO), [1])
        self.assertEqual(self.status_of(250)['state'], 'downloaded')

    def test_a_thumbnail_gets_two_attempts_and_the_file_stays_downloaded(self):
        self.assertEqual(self.run_bot(download_errors={'249.thumb.jpg': ['network'] * 5}), 0, self.output)
        self.assertEqual(self.attempts('249.thumb.jpg'), [1, 2])
        self.assertEqual(self.status_of(249)['state'], 'downloaded')
        self.assertNotIn('thumbnail', self.medium(249))

    def test_an_error_that_clears_up_costs_one_wait(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['network']}), 0, self.output)
        self.assertEqual(self.status_of(250)['state'], 'downloaded')
        self.assertEqual(self.sleeps(), [2])

    def test_repeated_zero_byte_downloads_are_treated_as_a_hidden_flood_wait(self):
        self.assertEqual(self.run_bot(download_errors={VIDEO: ['zero'] * 5}), 0, self.output)
        self.assertEqual(self.status_of(250), {'state': 'failed', 'error': 'zero bytes written'})
        self.assertEqual(self.sleeps(), [2, 120, 8, 120])
        self.assertFalse(os.path.exists(self.path('media', '2024-01', VIDEO)))
        self.assertFalse(os.path.exists(self.path('media', '2024-01', VIDEO + '.tmp')))

    def test_an_expired_file_reference_is_renewed_by_fetching_the_message_again(self):
        self.assertEqual(self.run_bot(expired_references=[250]), 0, self.output)
        self.assertEqual(self.status_of(250)['state'], 'downloaded')
        self.assertIn({'call': 'get_messages', 'ids': [250]}, self.calls('get_messages'))
        self.assertEqual([c['attempt'] for c in self.calls('download') if c['path'] == VIDEO], [1, 2])
        self.assertEqual(self.sleeps(), [])

    def test_a_reference_that_expires_again_fails_the_file_until_the_next_run(self):
        self.assertEqual(self.run_bot(always_expired=[250]), 0, self.output)
        status = self.status_of(250)
        self.assertEqual(status['state'], 'failed')
        self.assertIn('FILE_REFERENCE_EXPIRED', status['error'])
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.status_of(250)['state'], 'downloaded')

    def test_messages_are_fetched_again_in_batches_of_100_for_fresh_file_references(self):
        self.assertEqual(self.run_bot(), 0, self.output)
        batches = [len(c['ids']) for c in self.calls('get_messages')]
        self.assertEqual(batches, [100, 17])
        self.assertEqual(self.calls('get_messages')[0]['ids'][0], 250)

    def test_messages_are_fetched_again_when_their_file_references_are_30_minutes_old(self):
        ticks = iter(range(0, 10 ** 9, 60))
        self.assertEqual(self.run_bot(count=60, clock=lambda: next(ticks)), 0, self.output)
        batches = [c['ids'] for c in self.calls('get_messages')]
        self.assertGreater(len(batches), 1)
        self.assertEqual(batches[1][0], batches[0][len(batches[0]) - len(batches[1])])
        self.assertEqual(self.file_states(), {'downloaded': 20 + 8})


class LowDiskSpace(ExportRun):
    def test_the_run_stops_before_downloading_and_keeps_its_listing(self):
        self.assertEqual(self.run_bot(env={'MIN_FREE_DISK_MB': 10 ** 9}), 0, self.output)
        self.assertIn('below MIN_FREE_DISK_MB', self.output)
        self.assertEqual(self.calls('download'), [])
        self.assertEqual(self.state()['run']['status'], 'failed')
        self.assertEqual(self.state()['run']['stage'], 'downloading')
        self.assertEqual(self.file_states(), {'pending': 117})
        self.assertIn('Run the same command again to continue', self.output)
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.file_states(), {'downloaded': 117})


class FailuresOutsideDownloads(ExportRun):
    def test_a_flood_wait_while_listing_is_waited_out_and_the_listing_continues(self):
        self.assertEqual(self.run_bot(history_errors={'50': 'flood:30'}), 0, self.output)
        self.assertEqual(self.sleeps(), [30])
        self.assertEqual(self.listed_ids(), list(range(250, 201, -1)) + list(range(201, 0, -1)))
        self.assertEqual(len(self.item_ids()), 250)
        self.assertEqual(self.state()['listed'], [[1, 250]])

    def test_a_flood_wait_while_fetching_for_downloads_is_waited_out(self):
        self.assertEqual(self.run_bot(get_messages_error='flood:20', get_messages_errors=1), 0, self.output)
        self.assertEqual(self.sleeps(), [20])
        self.assertEqual(self.file_states(), {'downloaded': 117})

    def test_a_flood_wait_while_listing_longer_than_the_maximum_stops_the_run_with_the_listing_kept(self):
        self.assertNotEqual(self.run_bot(history_errors={'50': 'flood:30'}, env={'FLOOD_WAIT_MAX_SLEEP': 10}), 0)
        self.assertIn('FLOOD_WAIT', self.output)
        self.assertEqual(len(self.item_ids()), 49)
        self.assertEqual(self.state()['run']['status'], 'failed')
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertNotIn(240, self.listed_ids())
        self.assertEqual(len(self.item_ids()), 250)

    def test_an_error_fetching_messages_for_downloads_stops_the_run_with_the_listing_kept(self):
        self.assertNotEqual(self.run_bot(get_messages_error='network'), 0)
        self.assertEqual(self.state()['run']['stage'], 'downloading')
        self.assertEqual(self.file_states(), {'pending': 117})
        self.assertEqual(self.run_bot(), 0, self.output)
        self.assertEqual(self.file_states(), {'downloaded': 117})

    def test_a_message_deleted_after_it_was_listed_is_kept_and_its_file_is_not_asked_for_again(self):
        self.assertEqual(self.run_bot(deleted_after_listing=[249]), 0, self.output)
        self.assertEqual(self.medium(249)['state'], 'unavailable')
        self.assertEqual(self.medium(249)['error'], 'message no longer available')
        self.assertIn(249, self.item_ids())
        self.assertEqual(self.state()['run']['status'], 'complete')
        self.assertEqual(self.run_bot(deleted_after_listing=[249]), 0, self.output)
        self.assertEqual(self.calls('get_messages'), [])


if __name__ == '__main__':
    unittest.main()
