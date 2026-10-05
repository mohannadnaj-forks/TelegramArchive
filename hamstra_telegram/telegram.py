"""Everything the exporter asks of Telegram, and the one place a Kurigram client is created."""
from typing import AsyncIterator, Protocol

from pyrogram.enums import ChatType

from .settings import Settings

SESSION_NAME = 'account'


class TelegramClient(Protocol):
    """The calls the exporter makes; tests/fake_telegram.py implements the same."""

    async def __aenter__(self): ...

    async def __aexit__(self, *exc): ...

    async def get_chat(self, chat_id): ...

    def get_dialogs(self) -> AsyncIterator: ...

    async def get_chat_history_count(self, chat_id) -> int: ...

    def get_chat_history(self, chat_id, max_id: int = 0, offset_date=None) -> AsyncIterator: ...

    async def get_messages(self, chat_id, message_ids): ...

    async def download_media(self, file_id, file_name): ...


def create_client(settings: Settings, session_dir: str) -> TelegramClient:
    from pyrogram import Client
    return Client(SESSION_NAME, api_id=int(settings.api_id), api_hash=settings.api_hash, workdir=session_dir)


def api_chat_id(chat):
    # A positive id on the command line is a channel's id as exports record it, without the API's -100 prefix.
    return int(f'-100{chat}') if isinstance(chat, int) and chat > 0 else chat


async def dialog_ids(client: TelegramClient, chat_kinds: dict) -> list:
    """The ids of the account's chats whose kind is switched on in CHAT_EXPORT_*."""
    export_map = {
        ChatType.CHANNEL: chat_kinds.get('channel', False),
        ChatType.SUPERGROUP: chat_kinds.get('super_group', False),
        ChatType.GROUP: chat_kinds.get('group', False),
        ChatType.PRIVATE: chat_kinds.get('personal', False),
        ChatType.BOT: chat_kinds.get('bot', False),
    }
    ids = []
    async for dialog in client.get_dialogs():
        chat = dialog.chat
        should_export = export_map.get(chat.type, False)
        print(
            f"Dialog: id={chat.id}, "
            f"title={getattr(chat, 'title', None) or getattr(chat, 'first_name', None)}, "
            f"type={chat.type.name}, export={should_export}"
        )
        if should_export:
            ids.append(chat.id)
    return ids
