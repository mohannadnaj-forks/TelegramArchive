"""The items written for each kind of message, pinned from the fake client's 'rich' chat.

tests/fixtures/records/<chat type>.json holds the account and every item; UPDATE_GOLDEN=1 rewrites them.
In-process runs write dates in UTC. The tests below spell out the rules that matter.
"""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_archive.records import instant, peer_id, vcard
from tests.fake_telegram import BASE_TIME
from tests.support import ExportRun

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'records')
ALL_MEDIA = {f'MEDIA_EXPORT_{kind}': 'True' for kind in (
    'AUDIOS', 'VIDEOS', 'PHOTOS', 'STICKERS', 'ANIMATIONS', 'DOCUMENTS', 'VOICE_MESSAGES', 'VIDEO_MESSAGES', 'CONTACTS')}
MB = 1024 * 1024


class RichChat(ExportRun):
    def export(self, chat_type: str, count: int = 30, env=None, **scenario) -> dict:
        self.assertEqual(self.run_bot(count=count, messages='rich', chat_type=chat_type, env={**ALL_MEDIA, **(env or {})},
                                      **scenario), 0, self.output)
        return self.items()

    def account(self) -> dict:
        return self.archive()['account']

    def assert_golden(self, name: str) -> None:
        archive = self.archive()
        result = {'account': archive['account'], 'items': archive['items']}
        path = os.path.join(GOLDEN, f'{name}.json')
        if os.environ.get('UPDATE_GOLDEN'):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=1, ensure_ascii=False)
                f.write('\n')
        with open(path, encoding='utf-8') as f:
            expected = json.load(f)
        self.assertEqual(result['account'], expected['account'])
        for got, want in zip(result['items'], expected['items'], strict=True):
            self.assertEqual(got, want)


class ChannelRecords(RichChat):
    def test_items_match_the_golden_file(self):
        self.export('channel')
        self.assert_golden('channel')

    def test_account(self):
        self.export('channel', description='About this channel')
        self.assertEqual(self.account(), {
            'id': 'channel1234', 'kind': 'channel', 'public': True, 'name': 'Test', 'username': 'testchat',
            'url': 'https://t.me/testchat', 'description': 'About this channel', 'counts': {'items': 30}})

    def test_a_private_channel_is_not_public_and_has_no_links(self):
        items = self.export('channel', username=None)
        self.assertEqual((self.account()['public'], self.account()['name']), (False, 'Test'))
        self.assertNotIn('url', items[2])

    def test_dates_carry_their_offset_and_posts_their_link(self):
        item = self.export('channel')[2]
        self.assertEqual(item['date'], '2024-01-01T02:00:00+00:00')
        self.assertEqual(item['url'], 'https://t.me/testchat/2')
        self.assertEqual(item['counts'], {'views': 1})
        self.assertNotIn('author', item, "a channel's own post has no author")

    def test_a_post_signed_with_its_authors_profile_names_the_author(self):
        item = self.export('channel')[24]
        self.assertEqual(item['author'], {'id': 'user44', 'name': 'Author'})
        self.assertEqual(item['extra'], {'telegram': {'signature': 'Author'}})

    def test_entities_keep_telegram_offsets_in_utf16_units(self):
        self.assertEqual(self.export('channel')[2]['text'], {
            'plain': '😀 bold and a link, `code`',
            'entities': [{'type': 'bold', 'offset': 3, 'length': 4},
                         {'type': 'text_link', 'offset': 14, 'length': 4, 'url': 'https://example.com'},
                         {'type': 'pre', 'offset': 20, 'length': 6},
                         {'type': 'italic', 'offset': 3, 'length': 8}]})

    def test_media_paths_and_thumbnails(self):
        items = self.export('channel')
        media = {i: item['media'][0] for i, item in items.items() if 'media' in item}
        self.assertEqual((media[3]['kind'], media[3]['path'], media[3]['thumbnail']),
                         ('photo', 'media/2024-01/3.jpg', 'media/2024-01/3.thumb.jpg'))
        self.assertEqual((media[7]['path'], media[7]['name'], media[7]['thumbnail']),
                         ('media/2024-01/7_Clip _1_.MP4', 'Clip <1>.MP4', 'media/2024-01/7.thumb.jpg'))
        self.assertEqual(media[8]['path'], 'media/2024-01/8.mp4', "Kurigram's invented name is not used")
        self.assertNotIn('thumbnail', media[8], 'no thumbnail from Telegram, none recorded')
        self.assertNotIn('name', media[8])
        self.assertEqual(media[10]['path'], 'media/2024-01/10.zip')
        self.assertEqual((media[12]['kind'], media[12]['path']), ('voice', 'media/2024-01/12.ogg'))
        self.assertEqual((media[15]['kind'], media[15]['path']), ('round_video', 'media/2024-01/15.mp4'))
        self.assertEqual((media[29]['path'], media[29]['animated']), ('media/2024-01/29.tgs', 'tgs'))
        for medium in media.values():
            self.assertEqual(medium['state'], 'downloaded' if medium['kind'] != 'video' or medium.get('size', 0) < 200 * MB
                             else 'too_large')
            if medium['state'] == 'downloaded':
                self.assertTrue(os.path.exists(self.path(*medium['path'].split('/'))), medium['path'])

    def test_albums_share_a_group_and_each_item_keeps_its_own_caption(self):
        items = self.export('channel')
        self.assertEqual([items[i].get('group') for i in (3, 4, 5, 6)], [None, '900', '900', '900'])
        self.assertEqual(items[4]['text'], {'plain': 'An album'})
        self.assertNotIn('text', items[5])

    def test_a_file_over_the_size_limit_is_recorded_with_its_size_and_path(self):
        self.assertEqual(self.export('channel')[21]['media'][0], {
            'kind': 'video', 'size': 500 * MB, 'mime': 'video/mp4', 'width': 1920, 'height': 1080, 'duration': 600,
            'path': 'media/2024-01/21.mp4', 'state': 'too_large', 'limit': 200 * MB})

    def test_switched_off_kinds_are_recorded_as_disabled(self):
        items = self.export('channel', env={'MEDIA_EXPORT_DOCUMENTS': 'False', 'MEDIA_EXPORT_CONTACTS': 'False'})
        self.assertEqual((items[9]['media'][0]['state'], items[9]['media'][0]['setting']), ('disabled', 'MEDIA_EXPORT_DOCUMENTS'))
        self.assertEqual((items[16]['media'][0]['state'], items[16]['media'][0]['setting']), ('disabled', 'MEDIA_EXPORT_CONTACTS'))
        self.assertNotIn('9_report.pdf', self.downloaded_files())
        self.assertFalse(os.path.exists(self.path('media', '2024-01', '16.vcf')))

    def test_contacts_are_written_as_vcards(self):
        self.assertEqual(self.export('channel')[16]['media'], [{
            'kind': 'contact', 'contact': {'phone': '+10000000000', 'first_name': 'Ada'},
            'path': 'media/2024-01/16.vcf', 'state': 'downloaded'}])
        with open(self.path('media', '2024-01', '16.vcf'), encoding='utf-8') as f:
            self.assertEqual(f.read(), 'BEGIN:VCARD\nVERSION:3.0\nFN;CHARSET=UTF-8:Ada\nN;CHARSET=UTF-8:;Ada;;;\n'
                                       'TEL;TYPE=CELL:+10000000000\nEND:VCARD\n')

    def test_forwards_name_their_origin(self):
        items = self.export('channel')
        self.assertEqual(items[18]['forward'], {'from': {'id': 'channel9999', 'name': 'Other Channel', 'username': 'other'},
                                                'date': '2024-01-01T01:00:00+00:00'})
        self.assertEqual(items[19]['forward']['from'], {'id': 'user77', 'name': 'Nikolai'})
        self.assertEqual(items[26]['forward'], {'from': {'name': 'Hidden Person'}, 'date': '2024-01-01T03:00:00+00:00'})

    def test_replies_venues_service_messages_edits_and_forward_counts(self):
        items = self.export('channel')
        self.assertEqual(items[20]['reply_to'], '2')
        self.assertEqual(items[17]['location'], {'latitude': 51.5, 'longitude': -0.12})
        self.assertEqual(items[27]['location'], {'latitude': 48.85, 'longitude': 2.29, 'name': 'A tower',
                                                 'address': '1 Example Street'})
        self.assertEqual(items[28]['extra'], {'telegram': {'service': 'pinned_message'}})
        self.assertNotIn('text', items[28])
        self.assertEqual((items[30]['edited'], items[30]['counts']), ('2024-01-02T16:00:00+00:00', {'views': 1, 'forwards': 5}))


class GroupRecords(RichChat):
    def test_items_match_the_golden_file(self):
        self.export('supergroup')
        self.assert_golden('supergroup')

    def test_authors(self):
        items = self.export('supergroup')
        self.assertEqual(items[1]['author'], {'id': 'user42', 'name': 'Pavel D'})
        self.assertEqual(items[22]['author'], {'id': 'channel5678', 'name': 'Linked Channel'})
        self.assertEqual(items[23]['author'], {'id': 'user43', 'name': 'Solo'})
        self.assertNotIn('counts', items[1])
        self.assertEqual(self.account()['kind'], 'group')

    def test_a_basic_group(self):
        self.export('group', count=3, chat_id=-555)
        self.assertEqual((self.account()['id'], self.account()['kind']), ('chat555', 'group'))


class PrivateChatRecords(RichChat):
    def test_account(self):
        self.export('private', count=3)
        self.assertEqual(self.account(), {
            'id': 'user42', 'kind': 'private', 'public': True, 'name': 'Pavel D', 'username': 'testchat',
            'url': 'https://t.me/testchat', 'description': 'A bio', 'counts': {'items': 3}})

    def test_a_chat_with_a_bot_is_named_and_typed(self):
        self.export('bot', count=3, username=None)
        account = self.account()
        self.assertEqual((account['id'], account['kind'], account['public'], account['name']), ('user42', 'bot', False, 'Pavel D'))

    def test_saved_messages(self):
        self.export('private', count=3, chat='me')
        self.assertEqual(self.account()['kind'], 'saved')


class Pieces(unittest.TestCase):
    def test_instants_keep_the_offset_of_their_zone(self):
        local = datetime.fromtimestamp(BASE_TIME + 3600)
        self.assertEqual(instant(local, timezone.utc), '2024-01-01T01:00:00+00:00')
        self.assertEqual(instant(local, timezone(timedelta(hours=3))), '2024-01-01T04:00:00+03:00')

    def test_instants_are_right_through_the_hours_repeated_when_clocks_go_back(self):
        # Naive local times are ambiguous for an hour each autumn where the machine's zone has daylight saving;
        # Kurigram's datetime.fromtimestamp marks the second one with fold, and instant() keeps it apart.
        for ts in range(1729990800 - 7200, 1729990800 + 7200, 900):
            self.assertEqual(instant(datetime.fromtimestamp(ts), timezone.utc),
                             datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec='seconds'))

    def test_peer_ids_carry_their_kind(self):
        self.assertEqual([peer_id(i) for i in (42, -1001234, -555)], ['user42', 'channel1234', 'chat555'])

    def test_vcards_escape_separators(self):
        card = vcard(SimpleNamespace(first_name='Ada; Jr', last_name='Love\nlace', phone_number='+1'))
        self.assertIn('N;CHARSET=UTF-8:Love\\nlace;Ada\\; Jr;;;\n', card)


if __name__ == '__main__':
    unittest.main()
