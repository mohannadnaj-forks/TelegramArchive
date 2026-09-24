"""The JSON record written for each kind of message, pinned from the fake client's 'rich' chat.

tests/fixtures/records/<chat type>.json holds the full records (without date_unixtime, which depends
on the time zone); UPDATE_GOLDEN=1 rewrites them. The tests below spell out the rules that matter.
"""
import json
import os
import time
import unittest
from datetime import datetime

from tests.support import ExportRun

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'records')
ALL_MEDIA = {f'MEDIA_EXPORT_{kind}': 'True' for kind in (
    'AUDIOS', 'VIDEOS', 'PHOTOS', 'STICKERS', 'ANIMATIONS', 'DOCUMENTS', 'VOICE_MESSAGES', 'VIDEO_MESSAGES', 'CONTACTS')}


class RichChat(ExportRun):
    def export(self, chat_type: str, count: int = 30, env=None, **scenario) -> dict:
        self.assertEqual(self.run_bot(count=count, messages='rich', chat_type=chat_type, env={**ALL_MEDIA, **(env or {})},
                                      **scenario), 0, self.output)
        return self.records()

    def assert_golden(self, name: str) -> None:
        result = self.result()
        for m in result['messages']:
            date = datetime.strptime(m['date'], '%Y-%m-%dT%H:%M:%S')
            self.assertEqual(m.pop('date_unixtime'), int(time.mktime(date.timetuple())))
        path = os.path.join(GOLDEN, f'{name}.json')
        if os.environ.get('UPDATE_GOLDEN'):
            os.makedirs(GOLDEN, exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=1, ensure_ascii=False)
                f.write('\n')
        with open(path, encoding='utf-8') as f:
            expected = json.load(f)
        self.assertEqual({k: v for k, v in result.items() if k != 'messages'},
                         {k: v for k, v in expected.items() if k != 'messages'})
        for got, want in zip(result['messages'], expected['messages'], strict=True):
            self.assertEqual(got, want)


class ChannelRecords(RichChat):
    def test_records_match_the_golden_file(self):
        self.export('channel', count=23)
        self.assert_golden('channel')

    def test_chat_fields(self):
        self.export('channel', count=23, description='About this channel')
        chat = {k: v for k, v in self.result().items() if k != 'messages'}
        self.assertEqual(chat, {'username': 'testchat', 'description': 'About this channel', 'name': 'Test',
                                'type': 'public_channel', 'id': '1234'})

    def test_entities_keep_telegram_offsets_in_utf16_units(self):
        text = self.export('channel', count=23)[2]['text']
        self.assertEqual(text[-1], '😀 bold and a link, `code`')
        self.assertEqual(text[0], {'type': 'bold', 'text': 'bold', 'offset': 3, 'length': 4})
        self.assertEqual(text[1], {'type': 'text_link', 'href': 'https://example.com', 'text': 'link', 'offset': 14, 'length': 4})
        self.assertEqual(text[2], {'type': 'pre', 'language': '', 'text': '`code`', 'offset': 20, 'length': 6})
        self.assertEqual(text[3]['text'], 'bold and', 'entities overlap and keep Telegram order')

    def test_media_names_and_thumbnails(self):
        records = self.export('channel', count=23)
        self.assertEqual(records[3]['photo'], 'photos/photo_3.jpg')
        self.assertEqual(records[3]['thumbnail'], 'photos/photo_3.jpg_thumb.jpg')
        self.assertEqual(records[7]['file'], 'video_files/7_Clip _1_.MP4')
        self.assertEqual(records[7]['thumbnail'], 'video_files/7_Clip _1_.MP4_thumb.jpg')
        self.assertEqual(records[8]['file'], 'video_files/video_8.mp4', "Kurigram's invented name is not used")
        self.assertEqual(records[8]['thumbnail'], records[8]['file'], 'a video without a thumbnail points at itself')
        self.assertEqual(records[10]['file'], 'files/document_10.zip')
        self.assertEqual(records[12]['file'], 'voice_messages/voice_12.oga')
        self.assertNotIn('thumbnail', records[12])
        self.assertEqual(records[15]['file'], 'round_video_messages/video_note_15.mp4')
        for record in records.values():
            path = record.get('photo') or record.get('file')
            if record.get('file_status', {}).get('state') == 'downloaded':
                self.assertTrue(os.path.exists(self.path(path)), path)

    def test_albums_carry_their_group_id_and_the_caption_stays_on_its_message(self):
        records = self.export('channel', count=23)
        self.assertEqual([records[i].get('media_group_id') for i in (3, 4, 5, 6)], [None, '900', '900', '900'])
        self.assertEqual(records[4]['caption'], 'An album')
        self.assertEqual(records[5]['text'], '')

    def test_a_file_over_the_size_limit_is_recorded_with_its_size(self):
        record = self.export('channel', count=23)[21]
        self.assertEqual(record['file_status'], {'state': 'too_large', 'size': 500 * 1024 * 1024, 'limit': 200 * 1024 * 1024})
        self.assertEqual(record['file'], '(File exceeds maximum size. Change data exporting settings to download.)')

    def test_switched_off_kinds_are_recorded_as_disabled(self):
        records = self.export('channel', count=23, env={'MEDIA_EXPORT_DOCUMENTS': 'False', 'MEDIA_EXPORT_CONTACTS': 'False'})
        self.assertEqual(records[9]['file_status'], {'state': 'disabled', 'setting': 'MEDIA_EXPORT_DOCUMENTS'})
        self.assertEqual(records[9]['file'], '(File not included. Change data exporting settings to download.)')
        self.assertEqual(records[16]['contact_vcard'], '(File not included. Change data exporting settings to download.)')
        self.assertNotIn('9_report.pdf', self.downloaded_files())

    def test_contacts_are_written_as_vcards(self):
        records = self.export('channel', count=23)
        self.assertEqual(records[16]['contact_information'], {'phone_number': '+10000000000', 'fist_name': 'Ada', 'last_name': ''})
        self.assertEqual(records[16]['contact_vcard'], 'contacts/contact_16.vcf')
        with open(self.path('contacts', 'contact_16.vcf'), encoding='utf-8') as f:
            self.assertIn('TEL;TYPE=CELL:+10000000000', f.read())


class GroupRecords(RichChat):
    def test_records_match_the_golden_file(self):
        self.export('supergroup')
        self.assert_golden('supergroup')

    def test_senders(self):
        records = self.export('supergroup')
        self.assertEqual((records[1]['from'], records[1]['from_id']), ('Pavel D', 'user42'))
        self.assertEqual((records[22]['from'], records[22]['from_id']), ('Linked Channel', 'channel5678'))
        self.assertEqual((records[23]['from'], records[23]['from_id']), ('Solo', 'user43'))
        self.assertNotIn('views', records[1])
        self.assertEqual(self.result()['type'], 'public_supergroup')


class PrivateChatRecords(RichChat):
    def test_chat_fields(self):
        self.export('private', count=3)
        chat = {k: v for k, v in self.result().items() if k != 'messages'}
        self.assertEqual(chat, {'username': 'testchat', 'description': 'A bio', 'name': 'Pavel',
                                'type': 'personal_chat', 'id': 42})

    def test_a_chat_with_a_bot_has_no_name_type_or_id(self):
        self.export('bot', count=3)
        chat = {k: v for k, v in self.result().items() if k != 'messages'}
        self.assertEqual(chat, {'username': 'testchat', 'description': 'A bio'})


if __name__ == '__main__':
    unittest.main()
