import logging
import os
import json
import glob
import sys
import argparse
import mimetypes
import re
import shutil
import time
import asyncio
from datetime import datetime, timedelta
import signal

from tqdm_loggable.auto import tqdm
from tqdm_loggable.tqdm_logging import tqdm_logging
from pyrogram import Client
from pyrogram.types import Message
from pyrogram.enums import ChatType, MessageEntityType
from pyrogram.errors import FloodWait
from pyrogram.file_id import FileId
from configs import (
    API_ID, API_HASH, MEDIA_EXPORT, FILE_NOT_FOUND, NOT_INCLUDED, MIN_FREE_DISK_MB, 
    JSON_FILE_PAGE_SIZE, DOWNLOAD_PATH, FLOOD_WAIT_MAX_SLEEP,
    DOWNLOAD_MAX_RETRIES, CHECKPOINT_SECONDS, RESUME_ENABLED,
    ATOMIC_WRITES, ZERO_BYTES_MAX_RETRIES,
    SUSPECTED_FLOOD_WAIT_DURATION
)
from chats import ChatExporter

logger = logging.getLogger(__name__)

for stream in (sys.stdout, sys.stderr):
    stream.reconfigure(encoding='utf-8', errors='replace')

# Global checkpoint data for resuming

class LowDiskSpace(Exception):
    pass


media_bytes = {'total': 0, 'left_out': 0}


def format_size(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:,.1f} {unit}' if unit == 'GB' else f'{size:,.0f} {unit}'
        size /= 1024


def parse_size(value: str) -> int:
    match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*([KMGT]?)I?B?', value.strip().upper())
    if not match:
        raise argparse.ArgumentTypeError(f"'{value}' is not a size; use e.g. 500M, 20G, or 0 for no limit")
    return int(float(match.group(1)) * 1024 ** ' KMGT'.index(match.group(2) or ' '))


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, '%Y-%m-%d')
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a date; use YYYY-MM-DD")


async def download_media_with_flood_control(file_id: str, destination: str, pbar: tqdm) -> tuple:
    """
    Download media with proper FloodWait handling and retry logic.
    Returns True if successful, False if all retries failed.
    """
    if os.path.exists(destination):
        return True, None
    
    free_mb = shutil.disk_usage(os.path.dirname(destination)).free // (1024 * 1024)
    if free_mb < MIN_FREE_DISK_MB:
        raise LowDiskSpace(f"{free_mb:,} MB free at {os.path.dirname(destination)}, below MIN_FREE_DISK_MB={MIN_FREE_DISK_MB:,}")

    temp_destination = f"{destination}.tmp" if ATOMIC_WRITES else destination
    zero_bytes_attempts = 0
    last_error = 'zero bytes written'
    max_zero_bytes_attempts = ZERO_BYTES_MAX_RETRIES  # Limit retries for suspected flood waits
    
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            pbar.set_postfix(status=f"Downloading... (attempt {attempt})")
            
            # Suppress pyrogram's own exception logging temporarily
            pyrogram_logger = logging.getLogger("pyrogram")
            original_level = pyrogram_logger.level
            pyrogram_logger.setLevel(logging.CRITICAL)
            
            try:
                await app.download_media(file_id, temp_destination)
            finally:
                # Restore original logging level
                pyrogram_logger.setLevel(original_level)
            
            # Check if download was successful (non-zero file size)
            if os.path.exists(temp_destination) and os.path.getsize(temp_destination) > 0:
                if ATOMIC_WRITES:
                    os.replace(temp_destination, destination)
                
                # Log successful download
                file_size = os.path.getsize(destination)
                logger.info(f"✅ Downloaded: {os.path.basename(destination)} ({file_size:,} bytes)")
                
                pbar.set_postfix(status="Downloaded ✅")
                return True, None
            else:
                zero_bytes_attempts += 1
                logger.warning(f"Download attempt {attempt} failed: zero bytes written")
                if os.path.exists(temp_destination):
                    os.remove(temp_destination)
                
                # If we've had multiple zero byte failures, this is likely a flood wait
                if zero_bytes_attempts >= max_zero_bytes_attempts:
                    estimated_wait = min(SUSPECTED_FLOOD_WAIT_DURATION, 60 * zero_bytes_attempts)
                    logger.warning(f"🚦 Suspected flood wait detected after {zero_bytes_attempts} zero-byte downloads")
                    logger.info(f"💤 Implementing flood wait: Waiting {estimated_wait} seconds before continuing...")
                    pbar.set_postfix(status=f"Suspected flood wait: {estimated_wait}s...")
                    
                    await asyncio.sleep(estimated_wait)
                    
                    
                    # Reset zero bytes counter after flood wait
                    zero_bytes_attempts = 0
                    continue
                
        except FloodWait as e:
            wait_time = e.value
            if wait_time > FLOOD_WAIT_MAX_SLEEP:
                logger.error(f"❌ Required wait time ({wait_time}s) exceeds maximum ({FLOOD_WAIT_MAX_SLEEP}s)")
                return False, f'FloodWait of {wait_time}s exceeds FLOOD_WAIT_MAX_SLEEP'
            
            logger.warning(f"Telegram says: [420 FLOOD_WAIT_X] - A wait of {wait_time} seconds is required")
            logger.info(f"💤 FloodWait: Waiting {wait_time} seconds (attempt {attempt}/{DOWNLOAD_MAX_RETRIES})")
            pbar.set_postfix(status=f"Waiting {wait_time}s for rate limit...")
            
            await asyncio.sleep(wait_time)
            
            continue
            
        except Exception as e:
            last_error = str(e)
            logger.error(f"Download attempt {attempt} failed: {str(e)}")
            if os.path.exists(temp_destination):
                os.remove(temp_destination)
            if isinstance(e, OSError) and not os.path.isdir(os.path.dirname(destination)):
                raise
                
        # For genuine network issues (not zero bytes), use full retry logic
        if attempt < DOWNLOAD_MAX_RETRIES and zero_bytes_attempts < max_zero_bytes_attempts:
            # Exponential backoff between attempts
            wait_time = min(2 ** attempt, 60)
            logger.info(f"⏳ Waiting {wait_time}s before retry...")
            await asyncio.sleep(wait_time)
        elif zero_bytes_attempts >= max_zero_bytes_attempts:
            # Break early for suspected flood waits to avoid excessive retrying
            break
    
    logger.error(f"❌ Download failed after {attempt} attempts: {os.path.basename(destination)}")
    pbar.set_postfix(status="Download failed ❌")
    return False, last_error

def load_existing_export(username: str) -> dict:
    """Load the existing export, with the records saved to the journal since result.json was last written."""
    json_name = generate_json_name(username)
    data = load_result_json(json_name)
    journal = read_journal(os.path.dirname(json_name))
    if journal:
        messages = {m['id']: m for m in data.get('messages', [])}
        messages.update(journal)
        data['messages'] = list(messages.values())
        logger.info(f"📂 Recovered {len(journal):,} messages saved since result.json was last written")
    return data


def load_result_json(json_name: str) -> dict:
    if os.path.exists(json_name):
        try:
            with open(json_name, 'r', encoding='utf-8') as f:
                data = json.load(f)
                logger.info(f"📂 Found existing export with {len(data.get('messages', []))} messages")
                return data
        except Exception as e:
            logger.warning(f"⚠️ Failed to load existing export: {e}")
    
    # If main file doesn't exist, try to load split files (result_part1.json, result_part2.json, etc.)
    base_path = json_name.replace('result.json', '')
    part_num = 1
    all_messages = []
    chat_data = {}
    
    while True:
        part_file = f"{base_path}result_part{part_num}.json"
        if not os.path.exists(part_file):
            break
            
        try:
            with open(part_file, 'r', encoding='utf-8') as f:
                part_data = json.load(f)
                if part_num == 1:
                    chat_data = part_data.copy()
                    chat_data['messages'] = []
                all_messages.extend(part_data.get('messages', []))
            part_num += 1
        except Exception as e:
            logger.warning(f"⚠️ Failed to load split file {part_file}: {e}")
            break
    
    if all_messages:
        chat_data['messages'] = all_messages
        logger.info(f"📂 Found existing split export with {len(all_messages)} messages from {part_num-1} parts")
        return chat_data
    
    return {}


JOURNAL_FILE = 'export_journal.jsonl'


def read_journal(export_directory: str) -> dict:
    # One message record per line; later lines replace earlier ones. A line cut short by a crash ends the read.
    records = {}
    try:
        with open(os.path.join(export_directory, JOURNAL_FILE), encoding='utf-8') as f:
            for line in f:
                try:
                    record = json.loads(line)
                except ValueError:
                    break
                records[record['id']] = record
    except FileNotFoundError:
        pass
    return records


def append_journal(export_directory: str, records: list) -> None:
    with open(os.path.join(export_directory, JOURNAL_FILE), 'a', encoding='utf-8') as f:
        for record in records:
            f.write(json.dumps(record, default=str) + '\n')
        f.flush()
        os.fsync(f.fileno())


shutdown_requested = False
FILE_REFERENCE_CHUNK = 100
FILE_REFERENCE_MAX_AGE = 1800


# First Ctrl-C lets the current message finish so progress can be saved; a second one exits immediately.
def signal_handler(signum, frame):
    global shutdown_requested
    if shutdown_requested:
        raise KeyboardInterrupt
    shutdown_requested = True
    print("\n🛑 Stopping after the current message. Press Ctrl-C again to exit immediately.")

signal.signal(signal.SIGINT, signal_handler)


class Archive:
    def __init__(self, chat_ids=None) -> None:
        if chat_ids is None:
            chat_ids = []
        self.chat_ids = chat_ids
        self.username = ''
        self.messages = []
        self.chat_data = {}

    def fill_chat_data(self, chat: ChatExporter) -> None:
        self.username = chat.username
        if chat.username:
            self.chat_data['username'] = chat.username
        description = getattr(chat, 'description', None) or getattr(chat, 'bio', None)
        if description:
            self.chat_data['description'] = description
        match chat.type:
            case ChatType.PRIVATE:
                # TODO: lastname
                self.chat_data['name'] = chat.first_name
                self.chat_data['type'] = 'personal_chat'
                self.chat_data['id'] = chat.id
            case ChatType.CHANNEL:
                self.chat_data['name'] = chat.title
                self.chat_data['type'] = 'public_channel'
                # when using telegram api ids have -100 prefix
                # https://stackoverflow.com/questions/33858927/how-to-obtain-the-chat-id-of-a-private-telegram-channel
                self.chat_data['id'] = str(chat.id)[4::] if str(chat.id).startswith('-100') else chat.id
            case ChatType.GROUP:
                self.chat_data['name'] = chat.title
                self.chat_data['type'] = 'public_group'
                self.chat_data['id'] = str(chat.id)[4::] if str(chat.id).startswith('-100') else chat.id
            case ChatType.SUPERGROUP:
                self.chat_data['name'] = chat.title
                self.chat_data['type'] = 'public_supergroup'
                self.chat_data['id'] = str(chat.id)[4::] if str(chat.id).startswith('-100') else chat.id
            # TODO: private SUPERGROUP and GROUP
            case _:
                # bot? other chat types
                pass

    async def process_message(self, chat, message, msg_info: dict, pbar: tqdm) -> None:
        # TODO: move msg_info filling to other function
        msg_info['id'] = message.id
        msg_info['type'] = 'message'
        msg_info['date'] = message.date.strftime('%Y-%m-%dT%H:%M:%S')
        msg_info['date_unixtime'] = convert_to_unixtime(message.date)

        if chat.type == ChatType.CHANNEL:
            msg_info['from'] = chat.title
            msg_info['from_id'] = f'channel{str(message.sender_chat.id).removeprefix("-100")}'
        elif message.from_user is not None:
            msg_info['from'] = ' '.join(filter(None, (message.from_user.first_name, message.from_user.last_name)))
            msg_info['from_id'] = f'user{message.from_user.id}'
        elif message.sender_chat is not None:
            # Anonymous group admins and channels posting into a group.
            msg_info['from'] = message.sender_chat.title
            msg_info['from_id'] = f'channel{str(message.sender_chat.id).removeprefix("-100")}'
        else:
            msg_info['from'] = chat.title or chat.first_name

        if message.reply_to_message_id is not None:
            msg_info['reply_to_message_id'] = message.reply_to_message_id

        if message.media_group_id is not None:
            msg_info['media_group_id'] = str(message.media_group_id)

        if message.views is not None:
            msg_info['views'] = message.views

        if message.forward_from_chat is not None:
            msg_info['forwarded_from'] = message.forward_from_chat.title
        elif message.forward_from is not None:
            msg_info['forwarded_from'] = message.forward_from.first_name

        # TODO: type service. actor...

        for attr, kind in MEDIA_KINDS.items():
            media = getattr(message, attr)
            if media is not None:
                await export_media(message, media, attr, kind, msg_info, pbar, self.username, download=False)
                break
        if message.contact is not None:
            names = get_contact_name(self.username, message.id)
            get_contact_data(message, msg_info, names)
        elif message.location is not None:
            msg_info['location_information'] = {
                'latitude': message.location.latitude,
                'longitude': message.location.longitude
            }

        if message.text is not None:
            text = get_text_data(message, 'text')
            if text:
                text.append(message.text)
                msg_info['text'] = text
            else:
                msg_info['text'] = message.text
        elif message.caption is not None:
            caption = get_text_data(message, 'caption')
            if caption:
                caption.append(message.caption)
                msg_info['caption'] = caption
            else:
                msg_info['caption'] = message.caption
        else:
            msg_info['text'] = ''


_export_dirs = {}


def get_export_dir(username: str) -> str:
    # One directory per chat for the whole run; an earlier export of the same chat is continued.
    if username not in _export_dirs:
        pattern = os.path.join(glob.escape(DOWNLOAD_PATH), f'ChatExport_{glob.escape(username)}_*')
        existing = sorted(glob.glob(pattern)) if RESUME_ENABLED else []
        today = datetime.now().strftime("%Y-%m-%d")
        _export_dirs[username] = existing[-1] if existing else os.path.join(DOWNLOAD_PATH, f'ChatExport_{username}_{today}')
    return _export_dirs[username]


# message attribute -> (MEDIA_EXPORT key, folder, media_type in the JSON, fallback extension)
MEDIA_KINDS = {
    'photo': ('photos', 'photos', None, '.jpg'),
    'video': ('videos', 'video_files', 'video_file', '.mp4'),
    'animation': ('animations', 'video_files', 'animation', '.mp4'),
    'sticker': ('stickers', 'stickers', 'sticker', '.webp'),
    'video_note': ('video_messages', 'round_video_messages', 'video_message', '.mp4'),
    'audio': ('audios', 'files', 'audio_file', '.mp3'),
    'voice': ('voice_messages', 'voice_messages', 'voice_message', '.ogg'),
    'document': ('documents', 'files', None, ''),
}

MEDIA_FIELDS = (('mime_type', 'mime_type'), ('duration', 'duration_seconds'), ('width', 'width'),
                ('height', 'height'), ('performer', 'performer'), ('title', 'title'), ('emoji', 'sticker_emoji'))


async def export_media(message: Message, media, attr: str, kind: tuple, msg_info: dict, pbar: tqdm, username: str, download: bool) -> None:
    switch, folder, media_type, fallback_ext = kind
    if media_type:
        msg_info['media_type'] = media_type
    for source, field in MEDIA_FIELDS:
        value = getattr(media, source, None)
        if value is not None:
            msg_info[field] = value

    file_key = 'photo' if folder == 'photos' else 'file'
    size = getattr(media, 'file_size', None) or 0
    export_dir = get_export_dir(username)
    name = media_file_name(message.id, media, attr, fallback_ext)
    path = f'{export_dir}/{folder}/{name}'
    thumbs = getattr(media, 'thumbs', None)
    thumb_path = f'{export_dir}/{folder}/{name}_thumb.jpg' if thumbs or file_key == 'photo' else None

    if os.path.exists(path):
        status = {'state': 'downloaded', 'size': os.path.getsize(path)}
    elif not MEDIA_EXPORT[switch]:
        status = {'state': 'disabled', 'setting': f'MEDIA_EXPORT_{switch.upper()}'}
    elif MAX_FILE_SIZE and size > MAX_FILE_SIZE:
        status = {'state': 'too_large', 'size': size, 'limit': MAX_FILE_SIZE}
    elif not download:
        status = {'state': 'pending', 'size': size}
    elif MAX_TOTAL_SIZE and media_bytes['total'] + size > MAX_TOTAL_SIZE:
        status = {'state': 'total_limit', 'size': size, 'limit': MAX_TOTAL_SIZE}
        media_bytes['left_out'] += 1
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pbar.set_postfix(file=name)
        ok, error = await download_media_with_flood_control(media.file_id, path, pbar)
        pbar.set_postfix(file=None)
        status = {'state': 'downloaded', 'size': os.path.getsize(path)} if ok else {'state': 'failed', 'error': error}
        if ok:
            media_bytes['total'] += status['size']

    downloaded = status['state'] == 'downloaded'
    msg_info[file_key] = f'{folder}/{name}' if downloaded else NOT_INCLUDED[status['state']]
    msg_info['file_status'] = status

    if thumb_path is not None:
        if downloaded and not os.path.exists(thumb_path):
            thumb_id = thumbs[0].file_id if file_key == 'file' else photo_size_id(media.file_id, 'm')
            await download_media_with_flood_control(thumb_id, thumb_path, pbar)
        if os.path.exists(thumb_path):
            msg_info['thumbnail'] = f'{folder}/{name}_thumb.jpg'
        elif file_key == 'file':
            msg_info['thumbnail'] = msg_info[file_key]
    elif file_key == 'file' and media_type != 'voice_message':
        msg_info['thumbnail'] = msg_info[file_key]


def photo_size_id(file_id: str, size: str) -> str:
    # Photo thumbnails are the photo's own smaller sizes; 'm' fits within 320 px.
    decoded = FileId.decode(file_id)
    decoded.thumbnail_size = size
    return decoded.encode()


INVENTED_NAME = re.compile(r'(video|photo|audio|voice|document|animation|sticker)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\w+')


def media_file_name(message_id: int, media, kind: str, fallback_ext: str = '') -> str:
    # The message id keeps names unique and the same on every run; the rest is made safe for Windows, exFAT and NTFS.
    original = getattr(media, 'file_name', None)
    # Kurigram invents 'video_<time of parsing>.mp4' for unnamed videos; such names differ on every run.
    if original and INVENTED_NAME.fullmatch(original):
        original = None
    if original:
        stem, ext = os.path.splitext(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original).strip(' .'))
        return f'{message_id}_{stem[:120]}{ext[:16]}'
    ext = mimetypes.guess_extension(getattr(media, 'mime_type', None) or '') or fallback_ext
    return f'{kind}_{message_id}{ext}'


def write_export(chat_data: dict, json_name: str) -> None:
    chat_data['messages'].sort(key=lambda m: m['id'])
    if JSON_FILE_PAGE_SIZE:
        split_json_file(chat_data, json_name, JSON_FILE_PAGE_SIZE)
        return
    tmp_name = f'{json_name}.tmp'
    with open(tmp_name, mode='w', encoding='utf-8') as f:
        json.dump(chat_data, f, indent=4, default=str)
    os.replace(tmp_name, json_name)


def generate_json_name(username: str, path: str = '') -> str:
    # TODO: add path when user want other path
    chat_export_name = get_export_dir(username)
    json_name = f'{chat_export_name}/result.json'
    os.makedirs(chat_export_name, exist_ok=True)
    return json_name


def get_text_data(message: Message, text_mode: str) -> list:
    text = []
    if text_mode == 'caption':
        if message.caption_entities is not None:
            entities = message.caption_entities
        else:
            return text
    elif text_mode == 'text':
        if message.text.entities is not None:
            entities = message.text.entities
        else:
            return text

    for e in entities:
        txt = {}
        match e.type:
            case MessageEntityType.URL:
                txt['type'] = 'link'
            case MessageEntityType.HASHTAG:
                txt['type'] = 'hashtag'
            case MessageEntityType.CASHTAG:
                txt['type'] = 'cashtag'
            case MessageEntityType.BOT_COMMAND:
                txt['type'] = 'bot_command'
            case MessageEntityType.MENTION:
                txt['type'] = 'mention'
            case MessageEntityType.EMAIL:
                txt['type'] = 'email'
            case MessageEntityType.PHONE_NUMBER:
                txt['type'] = 'phone_number'
            case MessageEntityType.BOLD:
                txt['type'] = 'bold'
            case MessageEntityType.ITALIC:
                txt['type'] = 'italic'
            case MessageEntityType.UNDERLINE:
                txt['type'] = 'underline'
            case MessageEntityType.STRIKETHROUGH:
                txt['type'] = 'strikethrough'
            case MessageEntityType.SPOILER:
                txt['type'] = 'spoiler'
            case MessageEntityType.CODE:
                txt['type'] = 'code'
            case MessageEntityType.PRE:
                txt['type'] = 'pre'
                txt['language'] = ''
            case MessageEntityType.BLOCKQUOTE:
                txt['type'] = 'blockquote'
            case MessageEntityType.TEXT_LINK:
                txt['type'] = 'text_link'
                txt['href'] = e.url
            case MessageEntityType.TEXT_MENTION:
                txt['type'] = 'text_mention'
            case MessageEntityType.BANK_CARD:
                txt['type'] = 'bank_card'
            case MessageEntityType.CUSTOM_EMOJI:
                txt['type'] = 'custom_emoji'
            case _:
                txt['type'] = 'unknown'

        if text_mode == 'text':
            txt['text'] = message.text[e.offset:e.offset + e.length]
        else:
            txt['text'] = message.caption[e.offset:e.offset + e.length]
        # Telegram's offsets count UTF-16 code units, as JavaScript string indices do.
        txt['offset'] = e.offset
        txt['length'] = e.length
        text.append(txt)
    return text


# TODO: fix better typing
def get_contact_data(
        message: Message,
        msg_info: dict,
        names: tuple
) -> list:
    contact_data = {'phone_number': message.contact.phone_number}
    contact_data['fist_name'] = message.contact.first_name if message.contact.first_name is not None else ''
    contact_data['last_name'] = message.contact.last_name if message.contact.last_name is not None else ''
    msg_info['contact_information'] = contact_data

    if MEDIA_EXPORT['contacts'] is True:
        vcard_path, vcard_relative_path = names
        msg_info['contact_vcard'] = vcard_relative_path

        # convert to vcard
        vcard = (
            'BEGIN:VCARD\n'
            'VERSION:3.0\n'
            f'FN;CHARSET=UTF-8:{message.contact.first_name} {message.contact.last_name}\n'
            f'N;CHARSET=UTF-8:{message.contact.last_name};{message.contact.first_name};;;\n'
            f'TEL;TYPE=CELL:{message.contact.phone_number}\n'
            'END:VCARD\n'
        )
        with open(vcard_path, 'w', encoding='utf-8') as f:
            f.write(vcard)
    else:
        msg_info['contact_vcard'] = FILE_NOT_FOUND


def get_contact_name(username: str, message_id: int) -> tuple:
    chat_export_name = get_export_dir(username)
    # TODO: when user want other path
    path = ''
    media_dir = f'{chat_export_name}/contacts'
    os.makedirs(media_dir, exist_ok=True)
    contact_name = f'contact_{message_id}.vcf'
    contact_path = f'{media_dir}/{contact_name}'
    contact_relative_path = f'contacts/{contact_name}'
    return contact_path, contact_relative_path


def convert_to_unixtime(date: datetime):
    # telegram date format: "2022-07-10 08:49:23"
    unix_time = int(time.mktime(date.timetuple()))
    return unix_time


VIEWER_CHUNK_MESSAGES = 2000
VIDEO_TYPES = ('video_file', 'video_message', 'animation')


def compact_js(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def plain_text(value) -> str:
    if isinstance(value, list):
        return value[-1] if value and isinstance(value[-1], str) else ''
    return value or ''


def viewer_index(chat_data: dict, export_path: str) -> tuple:
    # The viewer loads data/index.js, then one data/<month>.js file at a time, all through <script> tags
    # so that it works from file:// without a server. Chunks hold the result.json records unchanged.
    months = {}
    for message in chat_data['messages']:
        months.setdefault(message['date'][:7], []).append(message)
    chunks, index_months = {}, []
    for key in sorted(months):
        messages = months[key]
        parts = [messages[i:i + VIEWER_CHUNK_MESSAGES] for i in range(0, len(messages), VIEWER_CHUNK_MESSAGES)]
        entry = {'key': key, 'count': len(messages), 'video': 0, 'photo': 0, 'other': 0, 'text': 0, 'missing': 0, 'chunks': []}
        for message in messages:
            status = message.get('file_status')
            if 'photo' in message:
                entry['photo'] += 1
            elif message.get('media_type') in VIDEO_TYPES:
                entry['video'] += 1
            elif status or 'file' in message:
                entry['other'] += 1
            else:
                entry['text'] += 1
            if status and status.get('state') != 'downloaded':
                entry['missing'] += 1
        for number, part in enumerate(parts, 1):
            name = key if len(parts) == 1 else f'{key}.{number}'
            chunks[name] = part
            entry['chunks'].append({
                'name': name, 'count': len(part),
                'first_id': part[0]['id'], 'last_id': part[-1]['id'],
                'first_date': part[0]['date'], 'last_date': part[-1]['date'],
                'missing': sum(1 for m in part if m.get('file_status', {}).get('state') not in (None, 'downloaded')),
            })
        index_months.append(entry)
    result_json = os.path.join(export_path, 'result.json')
    if not os.path.exists(result_json):
        result_json = os.path.join(export_path, 'result_part1.json')
    index = {
        'source': 'telegram',
        'chat': {k: v for k, v in chat_data.items() if k != 'messages'},
        'updated': datetime.fromtimestamp(os.path.getmtime(result_json)).strftime('%Y-%m-%dT%H:%M:%S') if os.path.exists(result_json) else None,
        'total': len(chat_data['messages']),
        'months': index_months,
    }
    search = [
        [m['id'], m['date'][:16], ' '.join(filter(None, (plain_text(m.get('text')), plain_text(m.get('caption')), m.get('forwarded_from'))))]
        for m in chat_data['messages']
    ]
    return index, chunks, search


def generate_index_html(export_path: str, chat_data: dict):
    """Generate index.html and its data/ folder for viewing the exported chat."""
    template_path = os.path.join(os.path.dirname(__file__), '_index.html')
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_template = f.read()
    except FileNotFoundError:
        logger.error(f"❌ HTML template not found: {template_path}")
        return

    index, chunks, search = viewer_index(chat_data, export_path)
    data_dir = os.path.join(export_path, 'data')
    files = {f'{name}.js': f'archiveChunk({compact_js(name)},{compact_js(part)});\n' for name, part in chunks.items()}
    files['search.js'] = f'archiveSearch({compact_js(search)});\n'
    files['index.js'] = f'archiveIndex({compact_js(index)});\n'
    try:
        os.makedirs(data_dir, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(data_dir, f'{name}.tmp'), 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(os.path.join(data_dir, f'{name}.tmp'), os.path.join(data_dir, name))
        for name in os.listdir(data_dir):
            if name not in files:
                os.remove(os.path.join(data_dir, name))
        if os.path.exists(os.path.join(export_path, 'data.js')):
            os.remove(os.path.join(export_path, 'data.js'))
    except Exception as e:
        logger.warning(f"⚠️ Failed to generate viewer data: {e}")
        return

    html_file_path = os.path.join(export_path, 'index.html')
    try:
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_template)
        logger.info(f"📄 Generated HTML viewer: {html_file_path}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to generate HTML viewer: {e}")


def to_html():
    pass


def split_json_file(data: dict, output_path: str, page_size: int = JSON_FILE_PAGE_SIZE):
    # Every part carries the chat's fields; parts left over from an earlier, longer split are removed.
    chat = {key: value for key, value in data.items() if key != 'messages'}
    base_size = len(json.dumps({**chat, 'messages': []}, indent=4, default=str).encode('utf-8'))
    parts, current, size = [], [], base_size
    for msg in data['messages']:
        text = json.dumps(msg, indent=4, default=str)
        msg_size = len(text.encode('utf-8')) + 8 * (text.count('\n') + 1) + 2
        if current and size + msg_size > page_size:
            parts.append(current)
            current, size = [], base_size
        current.append(msg)
        size += msg_size
    parts.append(current)

    for number, messages in enumerate(parts, 1):
        part_path = output_path.replace('result.json', f'result_part{number}.json')
        with open(f'{part_path}.tmp', 'w', encoding='utf-8') as outf:
            json.dump({**chat, 'messages': messages}, outf, indent=4, default=str)
        os.replace(f'{part_path}.tmp', part_path)
    number = len(parts) + 1
    while os.path.exists(output_path.replace('result.json', f'result_part{number}.json')):
        os.remove(output_path.replace('result.json', f'result_part{number}.json'))
        number += 1


async def main():
    fmt = "%(asctime)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt='%Y-%m-%d %H:%M:%S')

    # Mute other libraries (like Telethon, HTTPX, etc.)
    logging.getLogger("telethon").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("pyrogram").setLevel(logging.ERROR)
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    
    # Disable traceback logging for specific loggers
    for logger_name in ["telethon", "httpx", "pyrogram", "urllib3"]:
        logging.getLogger(logger_name).propagate = False

    # Set the rate how often we update logs
    # Defaults to 10 seconds - optional
    # tqdm_logging.set_log_rate(datetime.timedelta(seconds=1))

    async with app:
        print("\033[32mStarting...\033[0m")

        if EXPORT_ALL:
            all_dialogs_id = await ChatExporter(app).get_ids()
            CHAT_IDS.extend(all_dialogs_id)

        for cid in CHAT_IDS:
            # when use telegram api, channels id have -100 prefix
            if type(cid) == int and not str(cid).startswith('-100'):
                cid = int(f'-100{cid}')

            chat = await app.get_chat(cid)
            title = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Unknown')
            print(f"📋 Exporting: {title} (@{getattr(chat, 'username', None) or chat.id})")

            if not await ChatExport(chat, cid).run():
                return


STATE_FILE = 'export_state.json'


def merge_ranges(ranges: list) -> list:
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return merged


def load_state(export_directory: str) -> dict:
    try:
        with open(os.path.join(export_directory, STATE_FILE), encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning(f"⚠️ Failed to read {STATE_FILE}; the history is listed again: {e}")
        return {}


def write_state(export_directory: str, state: dict) -> None:
    path = os.path.join(export_directory, STATE_FILE)
    with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)
    os.replace(f'{path}.tmp', path)


def file_states(messages) -> dict:
    states = {}
    for m in messages:
        status = m.get('file_status')
        if status:
            entry = states.setdefault(status['state'], {'count': 0, 'bytes': 0})
            entry['count'] += 1
            entry['bytes'] += status.get('size') or 0
    return states


def downloaded_bytes(messages) -> int:
    sizes = {m.get('photo') or m.get('file'): m['file_status'].get('size') or 0
             for m in messages if m.get('file_status', {}).get('state') == 'downloaded'}
    return sum(sizes.values())


def now() -> str:
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


class ChatExport:
    # A run has two passes. Listing walks the history newest to oldest and writes each message's record,
    # marking wanted files 'pending'; the listed id ranges are kept in export_state.json so a stopped
    # listing continues where it stopped. Downloading then fetches pending files by message id.

    def __init__(self, chat, cid) -> None:
        self.chat = chat
        self.cid = cid
        self.archive = Archive(CHAT_IDS)
        self.archive.fill_chat_data(chat)
        self.username = chat.username or str(chat.id)
        self.archive.username = self.username
        self.json_name = generate_json_name(self.username)
        self.directory = os.path.dirname(self.json_name)

        # Messages already exported are kept, also ones since deleted or outside this run's date range.
        existing = load_existing_export(self.username) if RESUME_ENABLED else {}
        self.exported = {m['id']: m for m in existing.get('messages', [])}
        for key, value in existing.items():
            if key != 'messages':
                self.archive.chat_data.setdefault(key, value)
        self.state = load_state(self.directory) if self.exported else {}
        self.state.setdefault('listed', [])
        self.state['run'] = {'status': 'running', 'stage': 'listing', 'pid': os.getpid(), 'started': now()}
        self.dirty = set()
        self.written_at = time.time()

    def save(self, final: bool = False) -> None:
        # Checkpoints append changed records to the journal; result.json is rewritten, and the journal
        # removed, only when the run ends. The state is written last, so it never runs ahead of the records.
        if not final and time.time() - self.written_at < CHECKPOINT_SECONDS:
            return
        if final:
            self.archive.chat_data['messages'] = list(self.exported.values())
            write_export(self.archive.chat_data, self.json_name)
            if os.path.exists(os.path.join(self.directory, JOURNAL_FILE)):
                os.remove(os.path.join(self.directory, JOURNAL_FILE))
        elif self.dirty:
            append_journal(self.directory, [self.exported[i] for i in self.dirty])
        self.dirty.clear()
        self.state['run'].update(updated=now(), messages_exported=len(self.exported),
                                 files=file_states(self.exported.values()))
        write_state(self.directory, self.state)
        self.written_at = time.time()

    def cover(self, low: int, high: int) -> None:
        self.state['listed'] = merge_ranges(self.state['listed'] + [[low, high]])

    async def run(self) -> bool:
        await save_chat_photo(self.chat, self.archive.chat_data, self.directory)
        if self.exported:
            print(f"📂 Continuing the existing export of @{self.username} ({len(self.exported):,} messages)")
        failure = None
        try:
            await self.list_messages()
            self.state['run']['stage'] = 'downloading'
            await self.download_media()
            self.state['run']['stage'] = 'complete'
        except (KeyboardInterrupt, Exception) as e:
            if not isinstance(e, KeyboardInterrupt):
                failure = e
                logger.error(f"❌ Stopping: {e}")
        complete = self.state['run']['stage'] == 'complete'
        self.state['run']['status'] = 'complete' if complete else 'failed' if failure else 'stopped'
        if not complete:
            print("💾 Saving...")
        self.save(final=True)
        generate_index_html(self.directory, self.archive.chat_data)
        self.report()
        if failure is not None and not isinstance(failure, LowDiskSpace):
            raise failure
        return complete

    async def list_messages(self) -> None:
        skip = [] if REFRESH else [list(r) for r in self.state['listed']]
        try:
            in_chat = await app.get_chat_history_count(self.cid)
        except Exception:
            in_chat = None
        self.state['run'].update(messages_in_chat=in_chat, listed_this_run=0)
        known = f" ({in_chat:,} in the chat, {len(self.exported):,} in the export)" if in_chat is not None else ''
        if skip:
            print(f"📥 Listing messages of @{self.username} not listed before{known}; --refresh re-reads the whole history")
        else:
            print(f"📥 Listing messages of @{self.username}{known}")
        started = time.time()
        bar = tqdm(disable=True)
        bound, offset_date = 0, UNTIL
        while True:
            top, lowest, covered = bound or None, None, None
            async for message in app.get_chat_history(self.cid, max_id=bound, offset_date=offset_date):
                if shutdown_requested:
                    raise KeyboardInterrupt
                if bound and message.id > bound:
                    continue
                if SINCE is not None and message.date is not None and message.date < SINCE:
                    return
                covered = next((r for r in skip if r[0] <= message.id <= r[1]), None)
                if covered:
                    break
                msg_info = {}
                await self.archive.process_message(self.chat, message, msg_info, bar)
                self.exported[message.id] = msg_info
                self.dirty.add(message.id)
                top = top or message.id
                lowest = message.id
                self.cover(lowest, top)
                listed = self.state['run']['listed_this_run'] = self.state['run']['listed_this_run'] + 1
                if listed % 1000 == 0:
                    print(f"📥 Listed {listed:,} messages this run, back to {message.date:%Y-%m-%d} ({time.time() - started:.0f}s)")
                self.save()
            if covered is None:
                # The history ended: everything below what was listed is covered too.
                if top:
                    self.cover(1, lowest or top)
                return
            if top:
                self.cover(covered[1] + 1, top)
            bound, offset_date = covered[0] - 1, None
            if bound < 1:
                return

    def wanted(self, record: dict) -> bool:
        status = record.get('file_status') or {}
        state = status.get('state')
        if state in ('pending', 'failed', 'total_limit'):
            pass
        elif state == 'disabled':
            if not MEDIA_EXPORT.get(status.get('setting', '').removeprefix('MEDIA_EXPORT_').lower(), False):
                return False
        elif state == 'too_large':
            if MAX_FILE_SIZE and (status.get('size') or 0) > MAX_FILE_SIZE:
                return False
        else:
            return False
        date = datetime.strptime(record['date'], '%Y-%m-%dT%H:%M:%S')
        return (SINCE is None or date >= SINCE) and (UNTIL is None or date < UNTIL)

    async def download_media(self) -> None:
        media_bytes['total'] = downloaded_bytes(self.exported.values())
        media_bytes['left_out'] = 0
        planned, planned_bytes = [], media_bytes['total']
        for message_id in sorted(self.exported, reverse=True):
            record = self.exported[message_id]
            if not self.wanted(record):
                continue
            size = record['file_status'].get('size') or 0
            if MAX_TOTAL_SIZE and planned_bytes + size > MAX_TOTAL_SIZE:
                record['photo' if 'photo' in record else 'file'] = NOT_INCLUDED['total_limit']
                record['file_status'] = {'state': 'total_limit', 'size': size, 'limit': MAX_TOTAL_SIZE}
                self.dirty.add(message_id)
                media_bytes['left_out'] += 1
                continue
            planned.append(message_id)
            planned_bytes += size
        if not planned:
            return
        free = shutil.disk_usage(self.directory).free
        print(f"📦 {len(planned):,} files to download, {format_size(planned_bytes - media_bytes['total'])}; {format_size(free)} free on disk")
        if media_bytes['left_out']:
            print(f"💡 --max-total-size {format_size(MAX_TOTAL_SIZE)} leaves out {media_bytes['left_out']:,} more files")
        pbar = tqdm(total=len(planned), desc=f"Downloading @{self.username}", unit="file")
        try:
            for start in range(0, len(planned), FILE_REFERENCE_CHUNK):
                chunk = planned[start:start + FILE_REFERENCE_CHUNK]
                messages, fetched_at = {}, 0
                for position, message_id in enumerate(chunk):
                    if shutdown_requested:
                        raise KeyboardInterrupt
                    # File references inside fetched messages expire, so the rest of a chunk is re-read when they get old.
                    if time.time() - fetched_at > FILE_REFERENCE_MAX_AGE:
                        fresh = await app.get_messages(self.chat.id, chunk[position:])
                        messages = {m.id: m for m in fresh if m is not None and not m.empty}
                        fetched_at = time.time()
                    message = messages.get(message_id)
                    for attr, kind in MEDIA_KINDS.items():
                        media = getattr(message, attr, None)
                        if media is not None:
                            await export_media(message, media, attr, kind, self.exported[message_id], pbar, self.username, download=True)
                            self.dirty.add(message_id)
                            break
                    pbar.update(1)
                    self.save()
        finally:
            pbar.close()

    def report(self) -> None:
        messages = self.archive.chat_data['messages']
        states = file_states(messages)
        summary = ', '.join(f"{v['count']} {state.replace('_', ' ')}" for state, v in sorted(states.items()))
        run = self.state['run']
        in_chat = run.get('messages_in_chat')
        held = f"{len(self.exported):,}" + (f" of about {in_chat:,}" if in_chat else '')
        if run['stage'] == 'listing':
            print(f"💾 Progress saved for @{self.username}: {run.get('listed_this_run', 0):,} messages listed this run; "
                  f"the export holds {held} messages. Media is downloaded after the listing.")
        elif run['stage'] == 'downloading':
            print(f"💾 Progress saved for @{self.username}: {held} messages ({summary})")
        if run['stage'] != 'complete':
            print("💡 Run the same command again to continue.")
            return
        total = downloaded_bytes(messages)
        print(f"✅ Export of @{self.username} is up to date: {len(self.exported):,} messages, media {format_size(total)} ({summary})")
        if media_bytes['left_out']:
            print(f"💡 {media_bytes['left_out']} files were left out by --max-total-size ({format_size(MAX_TOTAL_SIZE)}); run again with a larger value to fetch them.")
        missing = sum(v['bytes'] for state, v in states.items() if state in ('total_limit', 'too_large'))
        if missing:
            print(f"📦 A complete export needs about {format_size(total + missing)} "
                  f"({format_size(missing)} not downloaded yet, thumbnails not counted).")


async def save_chat_photo(chat, chat_data: dict, export_directory: str) -> None:
    path = os.path.join(export_directory, 'chat_photo.jpg')
    if not os.path.exists(path) and getattr(chat, 'photo', None):
        try:
            await app.download_media(chat.photo.big_file_id, path)
        except Exception as e:
            logger.warning(f"⚠️ Could not download the chat photo: {e}")
    if os.path.exists(path):
        chat_data['photo'] = 'chat_photo.jpg'


def parse_chat(value: str):
    value = re.sub(r'^(https?://)?(t\.me|telegram\.me)/', '', value.strip()).lstrip('@').split('/')[0].split('?')[0]
    return int(value) if re.fullmatch(r'-?\d+', value) else value


parser = argparse.ArgumentParser(description="Export Telegram chats to JSON, media files and an HTML viewer.")
parser.add_argument('chats', nargs='*', help="usernames, t.me links or numeric ids; 'me' is Saved Messages")
parser.add_argument('--all', action='store_true', help="export every chat allowed by the CHAT_EXPORT_* settings")
parser.add_argument('-o', '--output', default=DOWNLOAD_PATH or 'exports', help="directory to export into (default: DOWNLOAD_PATH from .env, else ./exports)")
parser.add_argument('--since', type=parse_date, metavar='YYYY-MM-DD', help="only messages from this day on")
parser.add_argument('--until', type=parse_date, metavar='YYYY-MM-DD', help="only messages up to and including this day")
parser.add_argument('--max-file-size', type=parse_size, default='200M', metavar='SIZE', help="leave out files larger than this, e.g. 50M, 1G; 0 for no limit (default: 200M)")
parser.add_argument('--max-total-size', type=parse_size, default='10G', metavar='SIZE', help="stop, with progress saved, before a chat's media passes this; 0 for no limit (default: 10G)")
parser.add_argument('--refresh', action='store_true', help="re-read the whole history, refreshing edited messages, instead of only messages newer than the export")
parser.add_argument('--viewer-only', action='store_true', help="rebuild index.html and data.js from the existing result.json, without connecting to Telegram; chats may also be export directories")
args = parser.parse_args()
if args.since and args.until and args.since > args.until:
    parser.error("--since is after --until")
if not args.chats and not args.all:
    parser.error("name at least one chat, or pass --all")
if args.viewer_only and args.all:
    parser.error("--viewer-only needs the chats or export directories to rebuild")

CHAT_IDS = [parse_chat(c) for c in args.chats]
EXPORT_ALL = args.all
SINCE = args.since
UNTIL = args.until + timedelta(days=1) if args.until else None
MAX_FILE_SIZE = args.max_file_size
MAX_TOTAL_SIZE = args.max_total_size
REFRESH = args.refresh
DOWNLOAD_PATH = os.path.abspath(os.path.expanduser(args.output))

SESSION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.telegram')
os.makedirs(SESSION_DIR, exist_ok=True)

def rebuild_viewers(chats: list) -> None:
    for chat in chats:
        if os.path.isdir(chat) and any(os.path.exists(os.path.join(chat, name)) for name in ('result.json', 'result_part1.json')):
            _export_dirs[chat] = os.path.abspath(chat)
            username = chat
        else:
            username = str(parse_chat(chat))
        export_directory = get_export_dir(username)
        chat_data = load_existing_export(username)
        if not chat_data:
            print(f"❌ No result.json under {export_directory}")
            continue
        generate_index_html(export_directory, chat_data)
        print(f"✅ Rebuilt viewer for {chat_data.get('name', username)}: {len(chat_data['messages']):,} messages -> {export_directory}/index.html")


if args.viewer_only:
    rebuild_viewers(args.chats)
    sys.exit(0)

def lock_session() -> None:
    # Two clients on one session can lock its database or get the login revoked (AUTH_KEY_DUPLICATED).
    global _session_lock
    _session_lock = open(os.path.join(SESSION_DIR, 'my_bot.lock'), 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            _session_lock.seek(0)
            msvcrt.locking(_session_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_session_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("❌ Another export is already running with this Telegram login. "
                 "Wait for it to finish, or name several chats in one command.")


lock_session()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

app = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=SESSION_DIR,
)

try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\n👋 Interrupted.")

