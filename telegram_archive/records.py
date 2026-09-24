"""Kurigram chats and messages -> the fields of result.json. No input or output here."""
import time
from datetime import datetime

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
CHAT_TYPES = {
    ChatType.PRIVATE: 'personal_chat',
    ChatType.CHANNEL: 'public_channel',
    ChatType.GROUP: 'public_group',
    ChatType.SUPERGROUP: 'public_supergroup',
}


def without_api_prefix(chat_id) -> str:
    # The API gives channels and supergroups ids with a -100 prefix; exports record them without it.
    return str(chat_id).removeprefix('-100')


def chat_fields(chat) -> dict:
    fields = {}
    if chat.username:
        fields['username'] = chat.username
    description = getattr(chat, 'description', None) or getattr(chat, 'bio', None)
    if description:
        fields['description'] = description
    # TODO: the last name of private chats; private groups and channels are typed as public ones; chats with bots get none of these.
    if chat.type == ChatType.PRIVATE:
        fields.update(name=chat.first_name, type='personal_chat', id=chat.id)
    elif chat.type in CHAT_TYPES:
        chat_id = str(chat.id)[4:] if str(chat.id).startswith('-100') else chat.id
        fields.update(name=chat.title, type=CHAT_TYPES[chat.type], id=chat_id)
    return fields


def unixtime(date: datetime) -> int:
    # Kurigram's dates are naive local time.
    return int(time.mktime(date.timetuple()))


def message_fields(chat, message) -> dict:
    """The fields every record starts with, up to and including forwarded_from."""
    record = {
        'id': message.id,
        'type': 'message',
        'date': message.date.strftime('%Y-%m-%dT%H:%M:%S'),
        'date_unixtime': unixtime(message.date),
    }
    if chat.type == ChatType.CHANNEL:
        record['from'] = chat.title
        record['from_id'] = f'channel{without_api_prefix(chat.id)}'
    elif message.from_user is not None:
        record['from'] = ' '.join(filter(None, (message.from_user.first_name, message.from_user.last_name)))
        record['from_id'] = f'user{message.from_user.id}'
    elif message.sender_chat is not None:
        # Anonymous group admins and channels posting into a group.
        record['from'] = message.sender_chat.title
        record['from_id'] = f'channel{without_api_prefix(message.sender_chat.id)}'
    else:
        record['from'] = chat.title or chat.first_name

    if message.reply_to_message_id is not None:
        record['reply_to_message_id'] = message.reply_to_message_id
    if message.media_group_id is not None:
        record['media_group_id'] = str(message.media_group_id)
    if message.views is not None:
        record['views'] = message.views
    if message.forward_from_chat is not None:
        record['forwarded_from'] = message.forward_from_chat.title
    elif message.forward_from is not None:
        record['forwarded_from'] = message.forward_from.first_name
    return record


def contact_fields(contact) -> dict:
    return {'phone_number': contact.phone_number, 'fist_name': contact.first_name or '', 'last_name': contact.last_name or ''}


def vcard(contact) -> str:
    first, last = contact.first_name or '', contact.last_name or ''
    return (
        'BEGIN:VCARD\n'
        'VERSION:3.0\n'
        f'FN;CHARSET=UTF-8:{" ".join(filter(None, (first, last)))}\n'
        f'N;CHARSET=UTF-8:{last};{first};;;\n'
        f'TEL;TYPE=CELL:{contact.phone_number}\n'
        'END:VCARD\n'
    )


def location_fields(location) -> dict:
    return {'latitude': location.latitude, 'longitude': location.longitude}


def entities(text, message_entities) -> list:
    """Entities as result.json records them; text is Kurigram's Str, which slices in UTF-16 code units like Telegram."""
    found = []
    for e in message_entities or ():
        entity = {'type': ENTITY_TYPES.get(e.type, 'unknown')}
        if e.type == MessageEntityType.PRE:
            entity['language'] = ''
        elif e.type == MessageEntityType.TEXT_LINK:
            entity['href'] = e.url
        entity['text'] = text[e.offset:e.offset + e.length]
        entity['offset'] = e.offset
        entity['length'] = e.length
        found.append(entity)
    return found


def text_fields(message) -> dict:
    """'text' or 'caption': a plain string, or entities followed by the string."""
    if message.text is not None:
        found = entities(message.text, message.text.entities)
        return {'text': [*found, message.text] if found else message.text}
    if message.caption is not None:
        found = entities(message.caption, message.caption_entities)
        return {'caption': [*found, message.caption] if found else message.caption}
    return {'text': ''}
