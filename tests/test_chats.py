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
    def test_an_export_goes_to_chatexport_username_date(self):
        self.run_bot(count=3)
        [name] = os.listdir(self.out)
        self.assertRegex(name, r'^ChatExport_testchat_\d{4}-\d{2}-\d{2}$')

    def test_a_chat_without_username_uses_its_id(self):
        self.run_bot(count=3, username=None)
        [name] = os.listdir(self.out)
        self.assertRegex(name, r'^ChatExport_-1001234_\d{4}-\d{2}-\d{2}$')

    def test_the_newest_existing_folder_is_continued(self):
        for day in ('2024-01-01', '2025-06-30'):
            os.makedirs(os.path.join(self.out, f'ChatExport_testchat_{day}'))
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        self.assertEqual(sorted(os.listdir(self.out)), ['ChatExport_testchat_2024-01-01', 'ChatExport_testchat_2025-06-30'])
        self.assertTrue(os.path.exists(os.path.join(self.out, 'ChatExport_testchat_2025-06-30', 'result.json')))

    def test_an_output_folder_with_brackets_is_found_again(self):
        self.out = os.path.join(self.dir, 'out [1]')
        self.run_bot(count=3)
        self.assertEqual(self.run_bot(count=5), 0, self.output)
        self.assertEqual(len(os.listdir(self.out)), 1)
        self.assertEqual(self.listed_ids(), [5, 4, 3])

    def test_resume_disabled_starts_a_fresh_folder(self):
        os.makedirs(os.path.join(self.out, 'ChatExport_testchat_2024-01-01'))
        self.assertEqual(self.run_bot(count=3, env={'RESUME_ENABLED': 'False'}), 0, self.output)
        self.assertEqual(len(os.listdir(self.out)), 2)


class SessionLock(ExportRun):
    def test_a_second_run_on_the_same_login_stops_at_once(self):
        lock_dir = os.path.join(self.program, '.telegram')
        os.makedirs(lock_dir)
        with open(os.path.join(lock_dir, 'my_bot.lock'), 'a+') as held:
            if os.name == 'nt':
                import msvcrt
                held.seek(0)
                msvcrt.locking(held.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_bot(count=3), 1)
        self.assertIn('Another export is already running with this Telegram login', self.output)
        self.assertFalse(os.path.exists(self.out))
        self.assertEqual(self.calls('client'), [])

    def test_the_session_lives_next_to_the_program(self):
        self.assertEqual(self.run_bot(count=3), 0, self.output)
        [client] = self.calls('client')
        self.assertEqual(client['name'], 'my_bot')
        self.assertEqual(os.path.realpath(client['workdir']), os.path.realpath(os.path.join(self.program, '.telegram')))


if __name__ == '__main__':
    unittest.main()
