"""Which files the downloading pass fetches (hamstra_telegram.states)."""
import unittest
from datetime import datetime, timezone

from hamstra_telegram import states

ENABLED = {'photos': True, 'videos': False}
DATE = '2024-01-02T10:00:00+00:00'


def medium(state: dict, kind='photo', size=10) -> dict:
    return {'kind': kind, 'size': size, 'path': 'media/2024-01/1.jpg', **state}


class Wanted(unittest.TestCase):
    def wanted(self, state, max_file_size=100, since=None, until=None, **kwargs):
        return states.is_wanted(medium(state, **kwargs), DATE, ENABLED, max_file_size, since, until)

    def test_states_that_are_fetched(self):
        self.assertTrue(self.wanted(states.pending()))
        self.assertTrue(self.wanted(states.failed('x')))
        self.assertTrue(self.wanted(states.total_limit(5)))
        self.assertFalse(self.wanted(states.downloaded()))
        self.assertFalse(self.wanted(states.unavailable('gone')))
        self.assertFalse(self.wanted(states.pending(), kind='contact'))

    def test_a_switched_off_kind_is_fetched_once_it_is_switched_on(self):
        self.assertTrue(self.wanted(states.disabled('MEDIA_EXPORT_PHOTOS')))
        self.assertFalse(self.wanted(states.disabled('MEDIA_EXPORT_VIDEOS')))

    def test_a_large_file_is_fetched_once_the_limit_allows_it(self):
        self.assertFalse(self.wanted(states.too_large(100), size=500))
        self.assertTrue(self.wanted(states.too_large(100), size=500, max_file_size=1000))
        self.assertTrue(self.wanted(states.too_large(100), size=500, max_file_size=0))

    def test_only_files_inside_the_date_range(self):
        day = datetime(2024, 1, 2, tzinfo=timezone.utc)
        self.assertTrue(self.wanted(states.pending(), since=day, until=datetime(2024, 1, 3, tzinfo=timezone.utc)))
        self.assertFalse(self.wanted(states.pending(), since=datetime(2024, 1, 3, tzinfo=timezone.utc)))
        self.assertFalse(self.wanted(states.pending(), until=day))


class Decisions(unittest.TestCase):
    def test_before_download(self):
        self.assertEqual(states.before_download(10, False, 'MEDIA_EXPORT_VIDEOS', 0), states.disabled('MEDIA_EXPORT_VIDEOS'))
        self.assertEqual(states.before_download(10, True, 'MEDIA_EXPORT_VIDEOS', 5), states.too_large(5))
        self.assertIsNone(states.before_download(10, True, 'MEDIA_EXPORT_VIDEOS', 0))

    def test_set_state_replaces_the_fields_of_the_previous_state(self):
        m = medium(states.too_large(5))
        states.set_state(m, states.failed('boom'))
        self.assertEqual(m, {'kind': 'photo', 'size': 10, 'path': 'media/2024-01/1.jpg', 'state': 'failed', 'error': 'boom'})
        states.set_state(m, states.downloaded())
        self.assertEqual(m['state'], 'downloaded')
        self.assertNotIn('error', m)


if __name__ == '__main__':
    unittest.main()
