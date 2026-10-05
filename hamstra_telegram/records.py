"""Kurigram chats and messages -> the account and item shapes of docs/export-format.md. No input or output here."""
from datetime import datetime, tzinfo

from pyrogram.enums import ChatType, MessageEntityType

ENTITY_TYPES = {
    MessageEntityType.URL: 'link',
    MessageEntityType.HASHTAG: 'hashtag',
    MessageEntityType.CASHTAG: 'cashtag',
    MessageEntityType.BOT_COMMAND: 'bot_command',
    MessageEntityType.MENTION: 'mention',
    MessageEntityType.EMAIL: 'email',
    MessageEntityType.PHONE_NUMBER: 'phone_number',
    MessageEntityType.BOLD: 'bold',
    MessageEntityType.ITALIC: 'italic',
    MessageEntityType.UNDERLINE: 'underline',
    MessageEntityType.STRIKETHROUGH: 'strikethrough',
    MessageEntityType.SPOILER: 'spoiler',
    MessageEntityType.CODE: 'code',
    MessageEntityType.PRE: 'pre',
    MessageEntityType.BLOCKQUOTE: 'blockquote',
    MessageEntityType.TEXT_LINK: 'text_link',
    MessageEntityType.TEXT_MENTION: 'text_mention',
    MessageEntityType.BANK_CARD: 'bank_card',
    MessageEntityType.CUSTOM_EMOJI: 'custom_emoji',
}
ACCOUNT_KINDS = {
    ChatType.PRIVATE: 'private',
    ChatType.BOT: 'bot',
    ChatType.CHANNEL: 'channel',
    ChatType.GROUP: 'group',
    ChatType.SUPERGROUP: 'group',
}


def instant(date: datetime, zone: tzinfo | None = None) -> str:
    """ISO 8601 with the UTC offset. Kurigram's dates are naive local time (with fold set for the repeated hour)."""
    return date.astimezone(zone).isoformat(timespec='seconds')


def peer_id(peer_id: int) -> str:
    """A Telegram id with its kind: user42, channel1234 (channels and supergroups, without -100), chat555 (basic groups)."""
    text = str(peer_id)
    if text.startswith('-100'):
        return f'channel{text[4:]}'
    if text.startswith('-'):
        return f'chat{text[1:]}'
    return f'user{text}'


def full_name(person) -> str:
    return ' '.join(filter(None, (getattr(person, 'first_name', None), getattr(person, 'last_name', None))))


def author_of(peer) -> dict:
    """A user or a chat as an item's author."""
    author = {'id': peer_id(peer.id), 'name': getattr(peer, 'title', None) or full_name(peer) or peer_id(peer.id)}
    if getattr(peer, 'username', None):
        author['username'] = peer.username
    return author


def account_fields(chat, saved: bool = False) -> dict:
    kind = 'saved' if saved else ACCOUNT_KINDS.get(chat.type, 'private')
    account = {'id': peer_id(chat.id), 'kind': kind, 'public': bool(chat.username),
               'name': chat.title or full_name(chat) or (f'@{chat.username}' if chat.username else peer_id(chat.id))}
    if chat.username:
        account['username'] = chat.username
        account['url'] = f'https://t.me/{chat.username}'
    description = getattr(chat, 'description', None) or getattr(chat, 'bio', None)
    if description:
        account['description'] = description
    return account


def text_of(value, message_entities) -> dict:
    """Text with entities at Telegram's offsets; value is Kurigram's Str, which slices in UTF-16 code units."""
    text = {'plain': str(value)}
    found = []
    for e in message_entities or ():
        entity = {'type': ENTITY_TYPES.get(e.type, 'unknown'), 'offset': e.offset, 'length': e.length}
        if e.type == MessageEntityType.TEXT_LINK:
            entity['url'] = e.url
        elif e.type == MessageEntityType.PRE and getattr(e, 'language', None):
            entity['language'] = e.language
        elif e.type == MessageEntityType.TEXT_MENTION and getattr(e, 'user', None) is not None:
            entity['user_id'] = peer_id(e.user.id)
        found.append(entity)
    if found:
        text['entities'] = found
    return text


def forward_of(message, zone) -> dict | None:
    origin = getattr(message, 'forward_origin', None)
    if origin is None:
        return None
    peer = getattr(origin, 'sender_user', None) or getattr(origin, 'sender_chat', None) or getattr(origin, 'chat', None)
    if peer is not None:
        source = author_of(peer)
    elif getattr(origin, 'sender_user_name', None):
        source = {'name': origin.sender_user_name}
    else:
        return None
    forward = {'from': source}
    if getattr(origin, 'date', None):
        forward['date'] = instant(origin.date, zone)
    return forward


def location_of(message) -> dict | None:
    venue = getattr(message, 'venue', None)
    location = venue.location if venue is not None else message.location
    if location is None:
        return None
    fields = {'latitude': location.latitude, 'longitude': location.longitude}
    if venue is not None:
        fields.update({k: v for k, v in (('name', venue.title), ('address', venue.address)) if v})
    return fields


def item_fields(chat, message, zone: tzinfo | None = None) -> dict:
    """The item for a message, without its media."""
    item = {'id': str(message.id), 'date': instant(message.date, zone)}
    if getattr(message, 'edit_date', None):
        item['edited'] = instant(message.edit_date, zone)
    if message.from_user is not None:
        item['author'] = author_of(message.from_user)
    elif message.sender_chat is not None and message.sender_chat.id != chat.id:
        item['author'] = author_of(message.sender_chat)
    if message.text is not None:
        item['text'] = text_of(message.text, message.text.entities)
    elif message.caption:
        item['text'] = text_of(message.caption, message.caption_entities)
    if message.media_group_id is not None:
        item['group'] = str(message.media_group_id)
    if message.reply_to_message_id is not None:
        item['reply_to'] = str(message.reply_to_message_id)
    forward = forward_of(message, zone)
    if forward:
        item['forward'] = forward
    counts = {k: v for k, v in (('views', message.views), ('forwards', getattr(message, 'forwards', None))) if v is not None}
    if counts:
        item['counts'] = counts
    location = location_of(message)
    if location:
        item['location'] = location
    if chat.username and chat.type in (ChatType.CHANNEL, ChatType.SUPERGROUP):
        item['url'] = f'https://t.me/{chat.username}/{message.id}'
    extra = {}
    if getattr(message, 'service', None) is not None:
        extra['service'] = message.service.name.lower()
    if getattr(message, 'author_signature', None):
        extra['signature'] = message.author_signature
    if extra:
        item['extra'] = {'telegram': extra}
    return item


def contact_fields(contact) -> dict:
    fields = {'phone': contact.phone_number, 'first_name': contact.first_name or ''}
    if contact.last_name:
        fields['last_name'] = contact.last_name
    return fields


def vcard_escape(value: str) -> str:
    return value.replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\r\n', '\\n').replace('\n', '\\n')


def vcard(contact) -> str:
    first, last = vcard_escape(contact.first_name or ''), vcard_escape(contact.last_name or '')
    return (
        'BEGIN:VCARD\n'
        'VERSION:3.0\n'
        f'FN;CHARSET=UTF-8:{" ".join(filter(None, (first, last)))}\n'
        f'N;CHARSET=UTF-8:{last};{first};;;\n'
        f'TEL;TYPE=CELL:{vcard_escape(contact.phone_number or "")}\n'
        'END:VCARD\n'
    )
