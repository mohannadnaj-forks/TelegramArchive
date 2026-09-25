"""Exporting one chat: listing its messages, then downloading their files."""
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime

from pyrogram.errors import FileReferenceExpired, FloodWait
from tqdm_loggable.auto import tqdm

from . import file_status, records
from .download import Downloader, LowDiskSpace
from .media import MediaKind, find_media, media_fields, media_file_name, photo_size_id
from .settings import Settings, media_setting_name
from .store import ExportFolder, find_export_dir
from .viewer import generate_index_html

logger = logging.getLogger(__name__)

FILE_REFERENCE_CHUNK = 100
FILE_REFERENCE_MAX_AGE = 1800


@dataclass(frozen=True)
class RunOptions:
    output: str
    since: datetime | None = None
    until: datetime | None = None  # exclusive: the day after --until
    max_file_size: int = 200 * 1024 ** 2
    max_total_size: int = 10 * 1024 ** 3
    refresh: bool = False


class StopRequest:
    """Ctrl-C: the first lets the current message finish so progress can be saved; a second one exits immediately."""

    def __init__(self) -> None:
        self.requested = False

    def handle(self, signum, frame) -> None:
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True
        print("\n🛑 Stopping after the current message. Press Ctrl-C again to exit immediately.")

    def check(self) -> None:
        if self.requested:
            raise KeyboardInterrupt


def merge_ranges(ranges: list) -> list:
    merged = []
    for low, high in sorted(ranges):
        if merged and low <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], high)
        else:
            merged.append([low, high])
    return merged


def format_size(size: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:,.1f} {unit}' if unit == 'GB' else f'{size:,.0f} {unit}'
        size /= 1024


def now() -> str:
    return datetime.now().strftime('%Y-%m-%dT%H:%M:%S')


class ChatExport:
    # A run has two passes. Listing walks the history newest to oldest and writes each message's record,
    # marking wanted files 'pending'; the listed id ranges are kept in export_state.json so a stopped
    # listing continues where it stopped. Downloading then fetches pending files by message id.

    def __init__(self, client, chat, cid, settings: Settings, options: RunOptions, stop: StopRequest,
                 downloader: Downloader | None = None, clock=time.time) -> None:
        self.client = client
        self.chat = chat
        self.cid = cid
        self.settings = settings
        self.options = options
        self.stop = stop
        self.downloader = downloader or Downloader(client, settings)
        self.clock = clock
        self.chat_data = records.chat_fields(chat)
        self.username = chat.username or str(chat.id)
        self.folder = ExportFolder(find_export_dir(options.output, self.username, settings.resume_enabled))
        self.folder.create()
        self.directory = self.folder.path

        # Messages already exported are kept, also ones since deleted or outside this run's date range.
        existing = self.folder.load() if settings.resume_enabled else {}
        self.exported = {m['id']: m for m in existing.get('messages', [])}
        for key, value in existing.items():
            if key != 'messages':
                self.chat_data.setdefault(key, value)
        self.state = self.folder.load_state() if self.exported else {}
        self.state.setdefault('listed', [])
        self.state['run'] = {'status': 'running', 'stage': 'listing', 'pid': os.getpid(), 'started': now()}
        self.dirty = set()
        self.written_at = clock()
        self.downloaded_total = 0
        self.left_out = 0

    def save(self, final: bool = False) -> None:
        # Checkpoints append changed records to the journal; result.json is rewritten, and the journal
        # removed, only when the run ends. The state is written last, so it never runs ahead of the records.
        if not final and self.clock() - self.written_at < self.settings.checkpoint_seconds:
            return
        if final:
            self.chat_data['messages'] = list(self.exported.values())
            self.folder.write_result(self.chat_data, self.settings.json_file_page_size)
            self.folder.remove_journal()
        elif self.dirty:
            self.folder.append_journal([self.exported[i] for i in self.dirty])
        self.dirty.clear()
        self.state['run'].update(updated=now(), messages_exported=len(self.exported),
                                 files=file_status.count_states(self.exported.values()))
        self.folder.write_state(self.state)
        self.written_at = self.clock()

    def cover(self, low: int, high: int) -> None:
        self.state['listed'] = merge_ranges(self.state['listed'] + [[low, high]])

    async def run(self) -> bool:
        await self.save_chat_photo()
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
        generate_index_html(self.directory, self.chat_data)
        self.report()
        if failure is not None and not isinstance(failure, LowDiskSpace):
            raise failure
        return complete

    async def save_chat_photo(self) -> None:
        path = os.path.join(self.directory, 'chat_photo.jpg')
        if not os.path.exists(path) and getattr(self.chat, 'photo', None):
            try:
                await self.client.download_media(self.chat.photo.big_file_id, path)
            except Exception as e:
                logger.warning(f"⚠️ Could not download the chat photo: {e}")
        if os.path.exists(path):
            self.chat_data['photo'] = 'chat_photo.jpg'

    async def list_messages(self) -> None:
        options = self.options
        skip = [] if options.refresh else [list(r) for r in self.state['listed']]
        try:
            in_chat = await self.client.get_chat_history_count(self.cid)
        except Exception:
            in_chat = None
        self.state['run'].update(messages_in_chat=in_chat, listed_this_run=0)
        known = f" ({in_chat:,} in the chat, {len(self.exported):,} in the export)" if in_chat is not None else ''
        if skip:
            print(f"📥 Listing messages of @{self.username} not listed before{known}; --refresh re-reads the whole history")
        else:
            print(f"📥 Listing messages of @{self.username}{known}")
        started = self.clock()
        bar = tqdm(disable=True)
        bound, offset_date = 0, options.until
        while True:
            top, lowest, covered = bound or None, None, None
            try:
                async for message in self.client.get_chat_history(self.cid, max_id=bound, offset_date=offset_date):
                    self.stop.check()
                    if bound and message.id > bound:
                        continue
                    if options.since is not None and message.date is not None and message.date < options.since:
                        return
                    covered = next((r for r in skip if r[0] <= message.id <= r[1]), None)
                    if covered:
                        break
                    self.exported[message.id] = await self.list_message(message, bar)
                    self.dirty.add(message.id)
                    top = top or message.id
                    lowest = message.id
                    self.cover(lowest, top)
                    listed = self.state['run']['listed_this_run'] = self.state['run']['listed_this_run'] + 1
                    if listed % 1000 == 0:
                        print(f"📥 Listed {listed:,} messages this run, back to {message.date:%Y-%m-%d} ({self.clock() - started:.0f}s)")
                    self.save()
            except FloodWait as e:
                await self.wait_out(e)
                if lowest is not None:
                    bound, offset_date = lowest - 1, None
                    if bound < 1:
                        return
                continue
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

    async def wait_out(self, flood: FloodWait) -> None:
        if flood.value > self.settings.flood_wait_max_sleep:
            raise flood
        print(f"🚦 Telegram asks to wait {flood.value}s before continuing; waiting")
        await self.downloader.sleep(flood.value)

    async def get_messages(self, message_ids: list) -> list:
        while True:
            try:
                return await self.client.get_messages(self.chat.id, message_ids)
            except FloodWait as e:
                await self.wait_out(e)

    async def list_message(self, message, pbar) -> dict:
        record = records.message_fields(self.chat, message)
        found = find_media(message)
        if found:
            await self.export_media(message, *found, record, pbar, download=False)
        if message.contact is not None:
            record['contact_information'] = records.contact_fields(message.contact)
            if self.settings.media['contacts']:
                name = f'contact_{message.id}.vcf'
                os.makedirs(os.path.join(self.directory, 'contacts'), exist_ok=True)
                with open(os.path.join(self.directory, 'contacts', name), 'w', encoding='utf-8') as f:
                    f.write(records.vcard(message.contact))
                record['contact_vcard'] = f'contacts/{name}'
            else:
                record['contact_vcard'] = file_status.FILE_NOT_FOUND
        elif message.location is not None:
            record['location_information'] = records.location_fields(message.location)
        record.update(records.text_fields(message))
        return record

    async def export_media(self, message, kind: MediaKind, media, record: dict, pbar, download: bool) -> None:
        """Fills in the record's media fields and file status, downloading the file when download is set."""
        record.update(media_fields(kind, media))
        options = self.options
        size = getattr(media, 'file_size', None) or 0
        name = media_file_name(message.id, media, kind.attr, kind.fallback_ext)
        relative = f'{kind.folder}/{name}'
        path = os.path.join(self.directory, kind.folder, name)
        thumbs = getattr(media, 'thumbs', None)
        thumb_path = f'{path}_thumb.jpg' if thumbs or kind.path_key == 'photo' else None

        if os.path.exists(path):
            status = file_status.downloaded(os.path.getsize(path))
        else:
            status = file_status.before_download(size, self.settings.media[kind.setting], media_setting_name(kind.setting),
                                                 options.max_file_size)
        if status is None and not download:
            status = file_status.pending(size)
        elif status is None and options.max_total_size and self.downloaded_total + size > options.max_total_size:
            status = file_status.total_limit(size, options.max_total_size)
            self.left_out += 1
        elif status is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            pbar.set_postfix(file=name)
            ok, error = await self.fetch(message, media, path, pbar)
            pbar.set_postfix(file=None)
            status = file_status.downloaded(os.path.getsize(path)) if ok else file_status.failed(error)
            if ok:
                self.downloaded_total += status['size']

        file_status.set_status(record, kind.path_key, status, relative)
        downloaded = status['state'] == 'downloaded'
        if thumb_path is not None:
            if downloaded and not os.path.exists(thumb_path):
                thumb_id = thumbs[0].file_id if kind.path_key == 'file' else photo_size_id(media.file_id, 'm')
                with contextlib.suppress(FileReferenceExpired):
                    await self.downloader.fetch(thumb_id, thumb_path, pbar)
            if os.path.exists(thumb_path):
                record['thumbnail'] = f'{relative}_thumb.jpg'
            elif kind.path_key == 'file':
                record['thumbnail'] = record['file']
        elif kind.path_key == 'file' and kind.media_type != 'voice_message':
            record['thumbnail'] = record['file']

    async def fetch(self, message, media, path: str, pbar) -> tuple[bool, str | None]:
        """Downloads the file; when its reference has expired, fetches the message again, once."""
        try:
            return await self.downloader.fetch(media.file_id, path, pbar)
        except FileReferenceExpired as e:
            error = str(e)
        [fresh] = await self.get_messages([message.id])
        found = find_media(fresh) if fresh is not None and not fresh.empty else None
        if not found:
            return False, error
        try:
            return await self.downloader.fetch(found[1].file_id, path, pbar)
        except FileReferenceExpired as e:
            return False, str(e)

    def wanted(self, record: dict) -> bool:
        return file_status.is_wanted(record, self.settings.media, self.options.max_file_size,
                                     self.options.since, self.options.until)

    async def download_media(self) -> None:
        options = self.options
        self.downloaded_total = file_status.downloaded_bytes(self.exported.values())
        self.left_out = 0
        planned, planned_bytes = [], self.downloaded_total
        for message_id in sorted(self.exported, reverse=True):
            record = self.exported[message_id]
            if not self.wanted(record):
                continue
            size = record['file_status'].get('size') or 0
            if options.max_total_size and planned_bytes + size > options.max_total_size:
                file_status.set_status(record, file_status.path_key(record), file_status.total_limit(size, options.max_total_size))
                self.dirty.add(message_id)
                self.left_out += 1
                continue
            planned.append(message_id)
            planned_bytes += size
        if not planned:
            return
        free = self.downloader.free_bytes(self.directory)
        print(f"📦 {len(planned):,} files to download, {format_size(planned_bytes - self.downloaded_total)}; {format_size(free)} free on disk")
        if self.left_out:
            print(f"💡 --max-total-size {format_size(options.max_total_size)} leaves out {self.left_out:,} more files")
        pbar = tqdm(total=len(planned), desc=f"Downloading @{self.username}", unit="file")
        try:
            for start in range(0, len(planned), FILE_REFERENCE_CHUNK):
                chunk = planned[start:start + FILE_REFERENCE_CHUNK]
                messages, fetched_at = {}, None
                for position, message_id in enumerate(chunk):
                    self.stop.check()
                    # File references inside fetched messages expire, so the rest of a chunk is re-read when they get old.
                    if fetched_at is None or self.clock() - fetched_at > FILE_REFERENCE_MAX_AGE:
                        fresh = await self.get_messages(chunk[position:])
                        messages = {m.id: m for m in fresh if m is not None and not m.empty}
                        fetched_at = self.clock()
                    message = messages.get(message_id)
                    found = find_media(message) if message is not None else None
                    if found:
                        await self.export_media(message, *found, self.exported[message_id], pbar, download=True)
                        self.dirty.add(message_id)
                    pbar.update(1)
                    self.save()
        finally:
            pbar.close()

    def report(self) -> None:
        messages = self.chat_data['messages']
        states = file_status.count_states(messages)
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
        total = file_status.downloaded_bytes(messages)
        print(f"✅ Export of @{self.username} is up to date: {len(self.exported):,} messages, media {format_size(total)} ({summary})")
        if self.left_out:
            print(f"💡 {self.left_out} files were left out by --max-total-size ({format_size(self.options.max_total_size)}); run again with a larger value to fetch them.")
        missing = sum(v['bytes'] for state, v in states.items() if state in ('total_limit', 'too_large'))
        if missing:
            print(f"📦 A complete export needs about {format_size(total + missing)} "
                  f"({format_size(missing)} not downloaded yet, thumbnails not counted).")
