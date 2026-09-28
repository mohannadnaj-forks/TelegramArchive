"""A fake Kurigram client for running the exporter without a network or a login.

A scenario (a dict, so it can travel to a subprocess as JSON) describes the chat and the failures to
inject. Every call the program makes is appended to scenario['log'] as one JSON line.

Chats (scenario['messages']):
- 'basic': messages 1..count, one hour apart from BASE_DATE; every third has a photo, every fifth a
  video, the rest are text. Exports in tests/fixtures were made with this chat, so it must not change.
- 'rich': the first RICH_COUNT ids are one of each kind of message the exporter handles (see
  rich_message); ids above that are plain text.

The fake follows Kurigram 2.2.26's get_chat_history as read from its source: newest first, max_id
inclusive, offset_date returning messages older than it.
"""
import asyncio
import functools
import inspect
import json
import os
import signal
from datetime import datetime, timezone
from types import SimpleNamespace

BASE_TIME = datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()
PAGE = 100
CHAT_ID = -1001234
MEDIA_ATTRS = ('photo', 'video', 'animation', 'sticker', 'video_note', 'audio', 'voice', 'document')
RICH_COUNT = 30


def date_of(i: int) -> datetime:
    # Naive local time, as Kurigram gives it; message i is i hours after 2024-01-01 00:00 UTC.
    return datetime.fromtimestamp(BASE_TIME + 3600 * i)


def photo_file_id(i: int, size: str = 'y') -> str:
    from pyrogram.file_id import FileId, FileType, ThumbnailSource
    return FileId(file_type=FileType.PHOTO, dc_id=2, media_id=i, access_hash=1, file_reference=b'',
                  volume_id=0, local_id=0, thumbnail_source=ThumbnailSource.THUMBNAIL,
                  thumbnail_file_type=FileType.PHOTO, thumbnail_size=size).encode()


def size_of(file_id: str) -> int:
    # Sizes are derived from the file id so that downloads and file_size agree.
    if ':' in file_id:
        kind, i, *rest = file_id.split(':')
        if 'thumb' in rest:
            return 10
        return {'video': 5000, 'doc': 3000, 'audio': 4000, 'voice': 700, 'sticker': 300,
                'animation': 2000, 'round': 1500}[kind] + int(i)
    from pyrogram.file_id import FileId
    decoded = FileId.decode(file_id)
    return 1000 + decoded.media_id if decoded.thumbnail_size == 'y' else 10


def text(value: str, entities=None):
    from pyrogram.types.messages_and_media.message import Str
    return Str(value).init(entities)


def entity(kind: str, offset: int, length: int, **extra):
    from pyrogram.enums import MessageEntityType
    from pyrogram.types import MessageEntity
    return MessageEntity(type=getattr(MessageEntityType, kind), offset=offset, length=length, **extra)


def thumbs(file_id: str) -> list:
    return [SimpleNamespace(file_id=f'{file_id}:thumb')]


def base_message(i: int, chat_type: str) -> SimpleNamespace:
    message = SimpleNamespace(
        id=i, date=date_of(i), empty=False,
        sender_chat=SimpleNamespace(id=CHAT_ID, title='Test') if chat_type == 'channel' else None,
        from_user=None if chat_type == 'channel' else SimpleNamespace(id=42, first_name='Pavel', last_name='D'),
        reply_to_message_id=None, media_group_id=None, views=1 if chat_type == 'channel' else None, forwards=None,
        forward_origin=None, contact=None, location=None, venue=None, service=None, edit_date=None,
        author_signature=None, text=None, caption=None, caption_entities=None,
    )
    for attr in MEDIA_ATTRS:
        setattr(message, attr, None)
    return message


def basic_message(i: int, chat_type: str = 'channel') -> SimpleNamespace:
    message = base_message(i, chat_type)
    if i % 3 == 0:
        message.photo = SimpleNamespace(file_id=photo_file_id(i), file_size=1000 + i, width=800, height=600, thumbs=None)
    elif i % 5 == 0:
        message.video = SimpleNamespace(file_id=f'video:{i}', file_size=5000 + i, file_name=None, mime_type='video/mp4',
                                        duration=3, width=640, height=360, thumbs=None)
    else:
        message.text = text(f'message {i}')
    return message


def media(kind: str, i: int, **fields) -> SimpleNamespace:
    file_id = f'{kind}:{i}'
    values = {'file_id': file_id, 'file_size': size_of(file_id), 'file_name': None, 'mime_type': None, 'thumbs': None}
    values.update(fields)
    return SimpleNamespace(**values)


def rich_message(i: int, chat_type: str = 'channel') -> SimpleNamespace:
    m = base_message(i, chat_type)
    if i == 1:
        pass  # a service message: no text, no media
    elif i == 2:
        # The emoji is two UTF-16 code units, so the bold entity starts at 3, not 2.
        m.text = text('😀 bold and a link, `code`', [
            entity('BOLD', 3, 4), entity('TEXT_LINK', 14, 4, url='https://example.com'),
            entity('PRE', 20, 6), entity('ITALIC', 3, 8)])
    elif i == 3:
        m.photo = SimpleNamespace(file_id=photo_file_id(i), file_size=1000 + i, width=1280, height=720, thumbs=None)
        m.caption = text('Photo #news', [entity('HASHTAG', 6, 5)])
        m.caption_entities = m.caption.entities
    elif i in (4, 5, 6):
        m.photo = SimpleNamespace(file_id=photo_file_id(i), file_size=1000 + i, width=600, height=800, thumbs=None)
        m.media_group_id = 900
        if i == 4:
            m.caption = text('An album')
    elif i == 7:
        m.video = media('video', i, file_name='Clip <1>.MP4', mime_type='video/mp4', duration=12.5,
                        width=1920, height=1080, thumbs=thumbs(f'video:{i}'))
        m.caption = text('A named video')
    elif i == 8:
        m.video = media('video', i, file_name='video_2024-01-01_10-00-00.mp4', mime_type='video/mp4',
                        duration=3, width=640, height=360)
    elif i == 9:
        m.document = media('doc', i, file_name='report.pdf', mime_type='application/pdf')
    elif i == 10:
        m.document = media('doc', i, mime_type='application/zip')
    elif i == 11:
        m.audio = media('audio', i, file_name='song.mp3', mime_type='audio/mpeg', duration=200,
                        performer='Artist', title='Song')
    elif i == 12:
        m.voice = media('voice', i, mime_type='audio/ogg', duration=4)
    elif i == 13:
        m.sticker = media('sticker', i, mime_type='image/webp', emoji='👍', width=512, height=512,
                          thumbs=thumbs(f'sticker:{i}'))
    elif i == 14:
        m.animation = media('animation', i, mime_type='video/mp4', duration=2, width=320, height=240)
    elif i == 15:
        m.video_note = media('round', i, mime_type='video/mp4', duration=9, thumbs=thumbs(f'round:{i}'))
    elif i == 16:
        m.contact = SimpleNamespace(phone_number='+10000000000', first_name='Ada', last_name=None)
    elif i == 17:
        m.location = SimpleNamespace(latitude=51.5, longitude=-0.12)
    elif i == 18:
        m.text = text('forwarded from a channel')
        m.forward_origin = SimpleNamespace(chat=SimpleNamespace(id=-1009999, title='Other Channel', username='other'),
                                           date=date_of(1))
    elif i == 19:
        m.text = text('forwarded from a person')
        m.forward_origin = SimpleNamespace(sender_user=SimpleNamespace(id=77, first_name='Nikolai', last_name=None),
                                           date=date_of(2))
    elif i == 20:
        m.text = text('a reply')
        m.reply_to_message_id = 2
    elif i == 21:
        m.video = media('video', i, mime_type='video/mp4', duration=600, width=1920, height=1080,
                        file_size=500 * 1024 * 1024)
    elif i == 22 and chat_type != 'channel':
        # An anonymous group admin, or a channel posting into the group.
        m.from_user = None
        m.sender_chat = SimpleNamespace(id=-1005678, title='Linked Channel')
        m.text = text('posted as a channel')
    elif i == 23 and chat_type != 'channel':
        m.from_user = SimpleNamespace(id=43, first_name='Solo', last_name=None)
        m.text = text('no last name')
    elif i == 24 and chat_type == 'channel':
        # A channel post signed with the author's profile: Kurigram sets from_user, not sender_chat.
        m.sender_chat = None
        m.from_user = SimpleNamespace(id=44, first_name='Author', last_name=None)
        m.author_signature = 'Author'
        m.text = text('signed post')
    elif i == 25:
        m.photo = SimpleNamespace(file_id=photo_file_id(i), file_size=1000 + i, width=100, height=100, thumbs=None)
    elif i == 26:
        m.text = text('forwarded from someone who hides their account')
        m.forward_origin = SimpleNamespace(sender_user_name='Hidden Person', date=date_of(3))
    elif i == 27:
        m.venue = SimpleNamespace(location=SimpleNamespace(latitude=48.85, longitude=2.29), title='A tower',
                                  address='1 Example Street')
        m.location = m.venue.location
    elif i == 28:
        from pyrogram.enums import MessageServiceType
        m.service = MessageServiceType.PINNED_MESSAGE
    elif i == 29:
        m.sticker = media('sticker', i, mime_type='application/x-tgsticker', emoji='🎉', width=512, height=512,
                          is_animated=True, is_video=False, thumbs=thumbs(f'sticker:{i}'))
    elif i == 30:
        m.text = text('edited, and forwarded 5 times')
        m.edit_date = date_of(40)
        m.forwards = 5 if chat_type == 'channel' else None
    else:
        m.text = text(f'message {i}')
    return m


CHAT_TYPES = {'channel': 'CHANNEL', 'supergroup': 'SUPERGROUP', 'group': 'GROUP', 'private': 'PRIVATE', 'bot': 'BOT'}


def make_chat(scenario: dict, chat_id=None):
    from pyrogram.enums import ChatType
    kind = scenario.get('chat_type', 'channel')
    chat = SimpleNamespace(id=CHAT_ID, type=getattr(ChatType, CHAT_TYPES[kind]), title='Test', username='testchat',
                           photo=None, description=None, first_name=None, last_name=None, bio=None)
    if kind in ('private', 'bot'):
        chat.id, chat.title, chat.first_name, chat.last_name = 42, None, 'Pavel', 'D'
        chat.bio = 'A bio'
    elif scenario.get('description'):
        chat.description = scenario['description']
    if 'username' in scenario:
        chat.username = scenario['username']
    if 'chat_id' in scenario:
        chat.id = scenario['chat_id']
    if scenario.get('chat_photo'):
        chat.photo = SimpleNamespace(big_file_id='chatphoto:0')
    return chat


def make_fake_client(scenario: dict, kill=lambda: os._exit(9)) -> type:
    """A client class for the scenario; kill() is what the scenario's kill_after_* points call."""
    from pyrogram.errors import FileReferenceExpired, FloodWait

    deleted = set(scenario.get('deleted', []))
    gone_later = set(scenario.get('deleted_after_listing', []))
    ids = [i for i in range(1, scenario['count'] + 1) if i not in deleted]
    chat_type = scenario.get('chat_type', 'channel')
    builder = rich_message if scenario.get('messages') == 'rich' else basic_message
    counters = {'listed': 0, 'downloads': 0, 'attempts': {}, 'fetched': {}}

    def log(entry: dict) -> None:
        with open(scenario['log'], 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry) + '\n')

    def make_message(i: int, fetch: bool = False):
        message = builder(i, chat_type)
        if fetch:
            counters['fetched'][i] = counters['fetched'].get(i, 0) + 1
        # The first fetch by id of these messages (every fetch, for always_expired) carries an expired file reference.
        if fetch and (counters['fetched'][i] == 1 and i in scenario.get('expired_references', [])
                      or i in scenario.get('always_expired', [])):
            for attr in MEDIA_ATTRS:
                media = getattr(message, attr)
                if media is not None and ':' in media.file_id:
                    media.file_id += ':expired'
        return message

    async def report_progress(progress, *args) -> None:
        # As Kurigram does: a coroutine runs on the loop, a plain function on a worker thread.
        if inspect.iscoroutinefunction(progress):
            await progress(*args)
        else:
            await asyncio.get_running_loop().run_in_executor(None, functools.partial(progress, *args))

    def fail(action: str):
        if action == 'network':
            raise ConnectionError('network went away')
        if action.startswith('flood:'):
            raise FloodWait(value=int(action.split(':')[1]))
        if action == 'expired':
            raise FileReferenceExpired()
        raise AssertionError(action)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            log({'call': 'client', 'name': args[0] if args else kwargs.get('name'),
                 'workdir': kwargs.get('workdir')})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_chat(self, chat_id):
            log({'call': 'get_chat', 'chat_id': chat_id})
            return make_chat(scenario, chat_id)

        async def get_dialogs(self):
            from pyrogram.enums import ChatType
            for d in scenario.get('dialogs', []):
                yield SimpleNamespace(chat=SimpleNamespace(id=d['id'], type=getattr(ChatType, d['type']),
                                                           title=d.get('title'), first_name=d.get('first_name')))

        async def get_chat_history_count(self, chat_id):
            return len(ids)

        async def get_chat_history(self, chat_id, limit=0, offset=0, offset_id=None, offset_date=None,
                                   min_id=0, max_id=0, reverse=False):
            selected = [i for i in reversed(ids)
                        if (not max_id or i <= max_id) and (offset_date is None or date_of(i).timestamp() < offset_date.timestamp())]
            for start in range(0, len(selected), PAGE):
                page = selected[start:start + PAGE]
                log({'call': 'history', 'max_id': max_id, 'page': [page[0], page[-1]]})
                for i in page:
                    counters['listed'] += 1
                    action = scenario.get('history_errors', {}).get(str(counters['listed']))
                    if action:
                        fail(action)
                    if counters['listed'] == scenario.get('fail_after_listed'):
                        fail('network')
                    if not scenario.get('quiet'):
                        log({'call': 'yield', 'id': i})
                    if scenario.get('memory_every') and counters['listed'] % scenario['memory_every'] == 0:
                        import time
                        import tracemalloc
                        current, peak = tracemalloc.get_traced_memory()
                        log({'call': 'memory', 'listed': counters['listed'], 'current': current, 'peak': peak,
                             'time': time.perf_counter()})
                    yield make_message(i)
                    if counters['listed'] == scenario.get('interrupt_after_listed'):
                        signal.raise_signal(signal.SIGINT)
                    if counters['listed'] == scenario.get('kill_after_listed'):
                        kill()

        async def get_messages(self, chat_id, message_ids):
            message_ids = list(message_ids)
            log({'call': 'get_messages', 'ids': message_ids})
            counters['get_messages'] = counters.get('get_messages', 0) + 1
            if scenario.get('get_messages_error') and counters['get_messages'] <= scenario.get('get_messages_errors', 10 ** 9):
                fail(scenario['get_messages_error'])
            present = set(ids) - gone_later
            return [make_message(i, fetch=True) if i in present else SimpleNamespace(id=i, empty=True) for i in message_ids]

        async def download_media(self, file_id, file_name, progress=None, progress_args=()):
            name = os.path.basename(file_name).removesuffix('.tmp')
            attempt = counters['attempts'][name] = counters['attempts'].get(name, 0) + 1
            log({'call': 'download', 'path': name, 'attempt': attempt})
            script = scenario.get('download_errors', {}).get(name, [])
            action = script[attempt - 1] if attempt <= len(script) else None
            if file_id.endswith(':expired'):
                action = 'expired'
            if action == 'zero':
                open(file_name, 'wb').close()
                return file_name
            if action:
                fail(action)
            size = size_of(file_id) if not file_id.startswith('chatphoto') else 50
            with open(file_name, 'wb') as f:
                f.write(b'x' * size)
            if progress is not None:
                await report_progress(progress, size, size, *progress_args)
            counters['downloads'] += 1
            if counters['downloads'] == scenario.get('interrupt_after_downloads'):
                signal.raise_signal(signal.SIGINT)
            if counters['downloads'] == scenario.get('kill_after_downloads'):
                kill()
            return file_name

    return FakeClient


def install_fake_client(scenario: dict) -> type:
    import pyrogram
    pyrogram.Client = make_fake_client(scenario)
    return pyrogram.Client


def recording_sleep(log_path: str):
    """An asyncio.sleep that records the wait and returns at once."""
    import asyncio
    real_sleep = asyncio.sleep

    async def sleep(delay, result=None):
        if delay:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({'call': 'sleep', 'seconds': delay}) + '\n')
        await real_sleep(0)
        return result

    return sleep


def install_fast_sleep(log_path: str) -> None:
    # Retries and flood waits sleep for seconds or minutes; in a subprocess the fake replaces asyncio.sleep.
    import asyncio
    asyncio.sleep = recording_sleep(log_path)
