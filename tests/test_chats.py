"""Which chats a run exports, and the export folder each one goes to."""
import os
import unittest

from tests.support import ExportRun


class NamingChats(ExportRun):
    def requested(self) -> list:
        return [c['chat_id'] for c in self.calls('get_chat')]

    def test_usernames_links_and_ids(self):
        for chat, expected in (('durov', 'durov'), ('@durov', 'durov'), ('https://t.me/durov', 'durov'),
                               ('t.me/durov/123?single', 'durov'), ('me', 'me'),
                               ('1234', -1001234), ('-1001234', -1001234)):
            self.assertEqual(self.run_bot(chat=chat, count=3), 0, self.output)
            self.assertEqual(self.requested(), [expected], chat)

    def test_several_chats_in_one_run(self):
        self.assertEqual(self.run_bot(chat=['testchat', 'durov'], count=3), 0, self.output)
        self.assertEqual(self.requested(), ['testchat', 'durov'])

    def test_a_chat_is_required(self):
        self.assertEqual(self.run_bot(chat='', count=3), 2)
        self.assertIn('name at least one chat, or pass --all', self.output)


class ExportAll(ExportRun):
    DIALOGS = [{'id': -1001234, 'type': 'CHANNEL', 'title': 'A channel'},
               {'id': -1005678, 'type': 'SUPERGROUP', 'title': 'A supergroup'},
               {'id': 42, 'type': 'BOT', 'first_name': 'A bot'}]

    def test_only_the_kinds_switched_on_are_exported(self):
        env = {'CHAT_EXPORT_CHANNELS': 'True', 'CHAT_EXPORT_SUPER_GROUPS': 'False', 'CHAT_EXPORT_BOTS': 'False'}
        self.assertEqual(self.run_bot('--all', count=3, dialogs=self.DIALOGS, env=env), 0, self.output)
        self.assertEqual([c['chat_id'] for c in self.calls('get_chat')], [-1001234])
        self.assertIn('Dialog: id=-1005678, title=A supergroup, type=SUPERGROUP, export=False', self.output)

    def test_ids_from_the_dialog_list_are_used_as_they_are(self):
        dialogs = [{'id': 42, 'type': 'PRIVATE', 'first_name': 'Pavel'}, {'id': -555, 'type': 'GROUP', 'title': 'A group'}]
        env = {'CHAT_EXPORT_PERSONALS': 'True', 'CHAT_EXPORT_GROUPS': 'True'}
        self.assertEqual(self.run_bot('--all', count=3, dialogs=dialogs, env=env), 0, self.output)
        self.assertEqual([c['chat_id'] for c in self.calls('get_chat')], [42, -555])

    def test_a_basic_group_id_on_the_command_line(self):
        self.assertEqual(self.run_bot(chat='-555', count=3), 0, self.output)
        self.assertEqual([c['chat_id'] for c in self.calls('get_chat')], [-555])


class ExportFolders(ExportRun):
    def test_an_archive_goes_to_telegram_username(self):
        self.run_bot(count=3)
        self.assertEqual(os.listdir(self.out), ['telegram-testchat'])

    def test_a_chat_without_username_uses_its_id(self):
        self.run_bot(count=3, username=None)
        self.assertEqual(os.listdir(self.out), ['telegram-channel1234'])

    def test_an_archive_is_found_by_its_account_whatever_its_folder_is_called(self):
        self.run_bot(count=3)
        os.rename(self.path(), os.path.join(self.out, 'my channel'))
        self.assertEqual(self.run_bot(count=5), 0, self.output)
        self.assertEqual(os.listdir(self.out), ['my channel'])
        self.assertEqual(self.listed_ids(), [5, 4, 3])

    def test_a_chat_that_changed_its_username_keeps_its_archive(self):
        self.run_bot(count=3)
        self.assertEqual(self.run_bot(count=5, username='renamed'), 0, self.output)
        self.assertEqual(os.listdir(self.out), ['telegram-testchat'])
        self.assertEqual(self.archive()['account']['username'], 'renamed')

    def test_another_chat_by_the_same_name_gets_its_own_folder(self):
        self.run_bot(count=3, chat_id=-1005555)
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        self.assertEqual(sorted(os.listdir(self.out)), ['telegram-testchat', 'telegram-testchat-2'])

    def test_an_output_folder_with_brackets_is_found_again(self):
        self.out = os.path.join(self.dir, 'out [1]')
        self.run_bot(count=3)
        self.assertEqual(self.run_bot(count=5), 0, self.output)
        self.assertEqual(len(os.listdir(self.out)), 1)
        self.assertEqual(self.listed_ids(), [5, 4, 3])

    def test_resume_disabled_starts_a_fresh_folder(self):
        self.run_bot(count=3)
        self.assertEqual(self.run_bot(count=3, env={'RESUME_ENABLED': 'False'}), 0, self.output)
        self.assertEqual(sorted(os.listdir(self.out)), ['telegram-testchat', 'telegram-testchat-2'])

if __name__ == '__main__':
    unittest.main()
