"""A 200,000-message listing, to catch memory regressions. Slow: set TELEGRAM_ARCHIVE_SLOW_TESTS=1 to run it.

Listing writes each record to archive.db as it goes, so memory should not grow with the export.
"""
import os
import unittest

from tests.support import SubprocessRun

COUNT = 200_000


@unittest.skipUnless(os.environ.get('TELEGRAM_ARCHIVE_SLOW_TESTS'), 'slow; set TELEGRAM_ARCHIVE_SLOW_TESTS=1')
class Scale(SubprocessRun):
    def test_listing_memory_does_not_grow_with_the_export(self):
        code = self.run_bot(count=COUNT, quiet=True, memory_every=COUNT // 10,
                            env={'MEDIA_EXPORT_PHOTOS': 'False', 'MEDIA_EXPORT_VIDEOS': 'False'})
        self.assertEqual(code, 0, self.output[-2000:])
        samples = self.calls('memory')
        first, last = samples[0], samples[-1]
        per_message = (last['current'] - first['current']) / (last['listed'] - first['listed'])
        print(f"\n{COUNT:,} messages: {last['current'] / 2 ** 20:.0f} MB traced at the end of listing, "
              f"{per_message:.0f} bytes per message, listing took {last['time'] - first['time']:.1f}s")
        self.assertLess(per_message, 100)
        self.assertEqual(len(self.item_ids()), COUNT)


if __name__ == '__main__':
    unittest.main()
