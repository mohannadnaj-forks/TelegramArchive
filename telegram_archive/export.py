"""Exporting one chat: listing its messages, then downloading their files."""
import contextlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo

from pyrogram.errors import FileReferenceExpired, FloodWait
from tqdm_loggable.auto import tqdm

from . import __version__, records, states
from .download import Downloader, LowDiskSpace
from .media import MediaKind, describe, file_name, find_media, media_path, photo_size_id, thumbnail_path
from .settings import Settings, media_setting_name
from .store import Archive, find_archive, new_archive_dir
from .viewer import generate_index_html

logger = logging.getLogger(__name__)

FILE_REFERENCE_CHUNK = 100
FILE_REFERENCE_MAX_AGE = 1800
FAILED_RUNS_BEFORE_ONE_ATTEMPT = 2
THUMBNAIL_ATTEMPTS = 2
ACCOUNT_PHOTO = 'account/photo.jpg'


@dataclass(frozen=True)
class RunOptions:
    output: str
    since: datetime | None = None  # aware
    until: datetime | None = None  # aware, exclusive: the day after --until
    max_file_size: int = 200 * 1024 ** 2
    max_total_size: int = 10 * 1024 ** 3
    refresh: bool = False
    zone: tzinfo | None = None  # the zone dates are written in; None for the machine's

    def describe(self) -> dict:
        return {'since': self.since and self.since.isoformat(), 'until': self.until and self.until.isoformat(),
                'max_file_size': self.max_file_size, 'max_total_size': self.max_total_size, 'refresh': self.refresh}


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


class ChatExport:
    # A run has two passes. Listing walks the history newest to oldest and writes each message's item,
    # marking wanted files 'pending'; the listed id ranges are kept in the archive so a stopped listing
    # continues where it stopped. Downloading then fetches the wanted files not on disk, by message id.

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
        self.username = chat.username or str(chat.id)
        account = records.account_fields(chat, saved=cid == 'me')
        # Messages already exported are kept, also ones since deleted or outside this run's date range.
        existing = find_archive(options.output, lambda a: a.get('id') == account['id']) if settings.resume_enabled else None
        self.archive = Archive(existing or new_archive_dir(options.output, f"telegram-{chat.username or account['id']}"))
        self.directory = self.archive.path
        self.account = {**self.archive.get('account', {}), **account}
        if self.archive.get('created') is None:
            self.archive.set('source', 'telegram')
            self.archive.set('created', self.now())
        self.archive.set('generator', f'telegram-archive {__version__}')
        self.existing = self.archive.count()
        self.listed = self.archive.get('extra', {}).get('telegram', {}).get('listed', [])
        self.run_state = {'status': 'running', 'stage': 'listing', 'pid': os.getpid(), 'started': self.now(),
                          'options': options.describe()}
        self.run_id = self.archive.start_run(self.run_state)
        self.written_at = clock()
        self.downloaded_total = 0
        self.left_out = 0

    def now(self) -> str:
        return datetime.now(self.options.zone).astimezone(self.options.zone).isoformat(timespec='seconds')

    def save(self, final: bool = False) -> None:
        """Commits what was written since the last save, at most every CHECKPOINT_SECONDS unless final."""
        if not final and self.clock() - self.written_at < self.settings.checkpoint_seconds:
            return
        self.run_state['updated'] = self.now()
        self.archive.set('account', self.account)
        self.archive.set('extra', {'telegram': {'listed': self.listed}})
        self.archive.update_run(self.run_id, self.run_state)
        self.archive.commit()
        self.written_at = self.clock()

    def cover(self, low: int, high: int) -> None:
        self.listed = merge_ranges(self.listed + [[low, high]])

    async def run(self) -> bool:
        try:
            return await self.export()
        finally:
            self.archive.close()

    async def export(self) -> bool:
        await self.save_account_photo()
        if self.existing:
            print(f"📂 Continuing the existing export of @{self.username} ({self.existing:,} messages)")
        failure = None
        try:
            await self.list_messages()
            self.run_state['stage'] = 'downloading'
            await self.download_media()
            self.run_state['stage'] = 'complete'
        except (KeyboardInterrupt, Exception) as e:
            if not isinstance(e, KeyboardInterrupt):
                failure = e
                self.run_state['error'] = str(e)
                logger.error(f"❌ Stopping: {e}")
        complete = self.run_state['stage'] == 'complete'
        self.run_state['status'] = 'complete' if complete else 'failed' if failure else 'stopped'
        self.save(final=True)
        generate_index_html(self.directory, self.archive)
        self.report()
        if failure is not None and not isinstance(failure, LowDiskSpace):
            raise failure
        return complete

    async def save_account_photo(self) -> None:
        photo = self.account.get('photo') or ACCOUNT_PHOTO
        path = self.full_path(photo)
        if not os.path.exists(path) and getattr(self.chat, 'photo', None):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                await self.client.download_media(self.chat.photo.big_file_id, path)
            except Exception as e:
                logger.warning(f"⚠️ Could not download the chat photo: {e}")
        if os.path.exists(path):
            self.account['photo'] = photo

    async def list_messages(self) -> None:
        options = self.options
        skip = [] if options.refresh else [list(r) for r in self.listed]
        try:
            in_chat = await self.client.get_chat_history_count(self.cid)
        except Exception:
            in_chat = None
        self.run_state.update(items_at_source=in_chat, listed=0)
        if in_chat is not None:
            self.account['counts'] = {'items': in_chat}
        known = f" ({in_chat:,} in the chat, {self.existing:,} in the export)" if in_chat is not None else ''
        if skip:
            print(f"📥 Listing messages of @{self.username} not listed before{known}; --refresh re-reads the whole history")
        else:
            print(f"📥 Listing messages of @{self.username}{known}")
        started = self.clock()
        bound, offset_date = 0, options.until
        while True:
            top, lowest, covered = bound or None, None, None
            try:
                async for message in self.client.get_chat_history(self.cid, max_id=bound, offset_date=offset_date):
                    self.stop.check()
                    if bound and message.id > bound:
                        continue
                    if options.since is not None and message.date is not None and message.date.astimezone() < options.since:
                        return
                    covered = next((r for r in skip if r[0] <= message.id <= r[1]), None)
                    if covered:
                        break
                    self.archive.put_item(self.list_item(message), message.id)
                    top = top or message.id
                    lowest = message.id
                    self.cover(lowest, top)
                    listed = self.run_state['listed'] = self.run_state['listed'] + 1
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

    def full_path(self, path: str) -> str:
        return os.path.join(self.directory, *path.split('/'))

    def list_item(self, message) -> dict:
        item = records.item_fields(self.chat, message, self.options.zone)
        known = self.archive.media_of(item['id'])
        medium = self.list_medium(message, item, known[0] if known else None)
        if medium is not None:
            item['media'] = [medium]
        return item

    def list_medium(self, message, item: dict, known: dict | None) -> dict | None:
        """The message's medium as listing records it: on disk, left out by a setting, or pending."""
        path = known.get('path') if known else None
        thumbnail = known.get('thumbnail') if known else None
        if message.contact is not None:
            medium = {'kind': 'contact', 'contact': records.contact_fields(message.contact),
                      'path': path or media_path(item['date'][:7], f"{item['id']}.vcf")}
            if not self.settings.media['contacts']:
                states.set_state(medium, states.disabled(media_setting_name('contacts')))
                return medium
            full = self.full_path(medium['path'])
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'w', encoding='utf-8') as f:
                f.write(records.vcard(message.contact))
            states.set_state(medium, states.downloaded())
            return medium
        found = find_media(message)
        if not found:
            return None
        kind, media = found
        medium = {**describe(kind, media), 'path': path or media_path(item['date'][:7], file_name(item['id'], media, kind))}
        if thumbnail:
            medium['thumbnail'] = thumbnail
        if os.path.exists(self.full_path(medium['path'])):
            self.mark_downloaded(medium, item)
        else:
            states.set_state(medium, states.before_download(medium.get('size') or 0, self.settings.media[kind.setting],
                                                            media_setting_name(kind.setting), self.options.max_file_size)
                             or states.pending())
        return medium

    def thumbnail_of(self, medium: dict, item: dict) -> str:
        return medium.get('thumbnail') or thumbnail_path(item['date'][:7], item['id'])

    def mark_downloaded(self, medium: dict, item: dict) -> None:
        states.set_state(medium, states.downloaded())
        medium.pop('failures', None)
        if not medium.get('size'):
            medium['size'] = os.path.getsize(self.full_path(medium['path']))
        thumbnail = self.thumbnail_of(medium, item)
        medium.pop('thumbnail', None)
        if os.path.exists(self.full_path(thumbnail)):
            medium['thumbnail'] = thumbnail

    async def download_medium(self, message, kind: MediaKind, media, item: dict, medium: dict, pbar) -> None:
        """Downloads the file and its thumbnail, and records the outcome in medium."""
        if not medium.get('path'):
            medium['path'] = media_path(item['date'][:7], file_name(item['id'], media, kind))
        path = self.full_path(medium['path'])
        if not os.path.exists(path):
            size = medium.get('size') or 0
            if self.options.max_total_size and self.downloaded_total + size > self.options.max_total_size:
                states.set_state(medium, states.total_limit(self.options.max_total_size))
                self.left_out += 1
                return
            os.makedirs(os.path.dirname(path), exist_ok=True)
            attempts = 1 if medium.get('failures', 0) >= FAILED_RUNS_BEFORE_ONE_ATTEMPT else None
            ok, error = await self.fetch(message, media, path, pbar, attempts)
            if not ok:
                states.set_state(medium, states.failed(error))
                medium['failures'] = medium.get('failures', 0) + 1
                return
            self.downloaded_total += size or os.path.getsize(path)
        thumbnail = self.full_path(self.thumbnail_of(medium, item))
        thumbs = getattr(media, 'thumbs', None)
        if not os.path.exists(thumbnail) and (thumbs or kind.kind == 'photo'):
            thumb_id = photo_size_id(media.file_id, 'm') if kind.kind == 'photo' else thumbs[0].file_id
            with contextlib.suppress(FileReferenceExpired):
                await self.downloader.fetch(thumb_id, thumbnail, pbar, THUMBNAIL_ATTEMPTS, self.save)
        self.mark_downloaded(medium, item)

    async def fetch(self, message, media, path: str, pbar, attempts: int | None = None) -> tuple[bool, str | None]:
        """Downloads the file; when its reference has expired, fetches the message again, once."""
        try:
            return await self.downloader.fetch(media.file_id, path, pbar, attempts, self.save)
        except FileReferenceExpired as e:
            error = str(e)
        [fresh] = await self.get_messages([message.id])
        found = find_media(fresh) if fresh is not None and not fresh.empty else None
        if not found:
            return False, error
        try:
            return await self.downloader.fetch(found[1].file_id, path, pbar, attempts, self.save)
        except FileReferenceExpired as e:
            return False, str(e)

    def wanted(self, medium: dict, date: str) -> bool:
        return states.is_wanted(medium, date, self.settings.media, self.options.max_file_size,
                                self.options.since, self.options.until)

    async def download_media(self) -> None:
        """Downloads the wanted files, newest first; --max-total-size bounds this run's downloads."""
        options = self.options
        planned, left_out, planned_bytes = [], [], 0
        for item_id, position, date, medium in self.archive.media_not_downloaded():
            if not self.wanted(medium, date):
                continue
            size = medium.get('size') or 0
            if options.max_total_size and planned_bytes + size > options.max_total_size:
                left_out.append((item_id, position, medium))
                continue
            planned.append(int(item_id))
            planned_bytes += size
        self.left_out = len(left_out)
        for item_id, position, medium in left_out:
            states.set_state(medium, states.total_limit(options.max_total_size))
            self.archive.set_medium(item_id, position, medium)
        if not planned:
            return
        free = self.downloader.free_bytes(self.directory)
        print(f"📦 {len(planned):,} files to download, {format_size(planned_bytes)}; {format_size(free)} free on disk")
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
                    await self.download_item(messages.get(message_id), str(message_id), pbar)
                    pbar.update(1)
                    self.save()
        finally:
            pbar.close()

    async def download_item(self, message, item_id: str, pbar) -> None:
        item = self.archive.item(item_id)
        [medium] = item['media']
        found = find_media(message) if message is not None else None
        if found is None:
            states.set_state(medium, states.unavailable('message no longer available'))
        else:
            await self.download_medium(message, *found, item, medium, pbar)
        self.archive.set_medium(item_id, 0, medium)

    def report(self) -> None:
        file_states = self.archive.file_states()
        summary = ', '.join(f"{v['count']} {state.replace('_', ' ')}" for state, v in sorted(file_states.items()))
        run = self.run_state
        in_chat = run.get('items_at_source')
        count = self.archive.count()
        held = f"{count:,}" + (f" of about {in_chat:,}" if in_chat else '')
        if run['stage'] == 'listing':
            print(f"💾 Progress saved for @{self.username}: {run.get('listed', 0):,} messages listed this run; "
                  f"the export holds {held} messages. Media is downloaded after the listing.")
        elif run['stage'] == 'downloading':
            print(f"💾 Progress saved for @{self.username}: {held} messages ({summary})")
        if run['stage'] != 'complete':
            print("💡 Run the same command again to continue.")
            return
        total = self.archive.downloaded_bytes()
        print(f"✅ Export of @{self.username} is up to date: {count:,} messages, media {format_size(total)} ({summary})")
        if self.left_out:
            print(f"💡 {self.left_out} files were left out by --max-total-size ({format_size(self.options.max_total_size)}); run again with a larger value to fetch them.")
        missing = sum(v['bytes'] for state, v in file_states.items() if state in ('total_limit', 'too_large'))
        if missing:
            print(f"📦 A complete export needs about {format_size(total + missing)} "
                  f"({format_size(missing)} not downloaded yet, thumbnails not counted).")
