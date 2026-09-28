"""Downloading one file, with retries, flood waits and the free-space check."""
import asyncio
import logging
import os
import re
import shutil
import time

from pyrogram.errors import FileReferenceExpired, FloodWait

from . import files
from .settings import Settings

logger = logging.getLogger(__name__)
library_logger = logging.getLogger('pyrogram')

PROGRESS_SECONDS = 5


class LowDiskSpace(Exception):
    pass


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


class LastWarning(logging.Handler):
    """Keeps the last warning the library logged, which names the error behind a failed request."""

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.message = None

    def emit(self, record: logging.LogRecord) -> None:
        self.message = record.getMessage()

    @property
    def reason(self) -> str | None:
        if self.message is None:
            return None
        return re.sub(r'^\[\d+\] Retrying "[^"]*" due to: ', '', self.message)


def format_progress(current: int, total: int, seconds: float) -> str:
    mb = 1024 ** 2
    done = f"{current / mb:,.0f}/{total / mb:,.0f} MB" if total else f"{current / mb:,.0f} MB"
    return f"{done}, {current / mb / max(seconds, 0.001):.1f} MB/s"


class Downloader:
    def __init__(self, client, settings: Settings, sleep=asyncio.sleep, free_bytes=free_bytes, clock=time.monotonic) -> None:
        self.client = client
        self.settings = settings
        self.sleep = sleep
        self.free_bytes = free_bytes
        self.clock = clock
        self.last_reason = None

    async def fetch(self, file_id: str, destination: str, pbar, attempts: int | None = None,
                    heartbeat=None, size: int = 0) -> tuple[bool, str | None]:
        """Downloads file_id to destination unless it is there. Returns (ok, the last error).

        attempts overrides DOWNLOAD_MAX_RETRIES; heartbeat is called as the file's bytes arrive; size is
        the expected size, shown when the library reports none. FileReferenceExpired is raised at once:
        retrying the same reference cannot succeed."""
        if os.path.exists(destination):
            return True, None

        settings = self.settings
        free_mb = self.free_bytes(os.path.dirname(destination)) // (1024 * 1024)
        if free_mb < settings.min_free_disk_mb:
            raise LowDiskSpace(f"{free_mb:,} MB free at {os.path.dirname(destination)}, below MIN_FREE_DISK_MB={settings.min_free_disk_mb:,}")

        temp_destination = f"{destination}.tmp"
        name = os.path.basename(destination)
        zero_bytes_attempts = 0
        last_error = 'zero bytes written'
        max_zero_bytes_attempts = settings.zero_bytes_max_retries
        max_attempts = attempts or settings.download_max_retries

        for attempt in range(1, max_attempts + 1):
            try:
                pbar.set_postfix(file=name, status=f"Downloading... (attempt {attempt})")
                await self.download(file_id, temp_destination, name, pbar, heartbeat, size)

                if os.path.exists(temp_destination) and os.path.getsize(temp_destination) > 0:
                    files.replace(temp_destination, destination)
                    file_size = os.path.getsize(destination)
                    logger.info(f"✅ Downloaded: {name} ({file_size:,} bytes)")
                    pbar.set_postfix(file=name, status="Downloaded ✅")
                    return True, None

                zero_bytes_attempts += 1
                logger.warning(f"Download attempt {attempt} failed: zero bytes written")
                if os.path.exists(temp_destination):
                    os.remove(temp_destination)

                # Repeated empty downloads are taken as a flood wait Telegram did not announce.
                if zero_bytes_attempts >= max_zero_bytes_attempts:
                    estimated_wait = min(settings.suspected_flood_wait_duration, 60 * zero_bytes_attempts)
                    logger.warning(f"🚦 Suspected flood wait detected after {zero_bytes_attempts} zero-byte downloads")
                    logger.info(f"💤 Implementing flood wait: Waiting {estimated_wait} seconds before continuing...")
                    pbar.set_postfix(file=name, status=f"Suspected flood wait: {estimated_wait}s...")
                    await self.sleep(estimated_wait)
                    zero_bytes_attempts = 0
                    continue

            except FileReferenceExpired:
                if os.path.exists(temp_destination):
                    os.remove(temp_destination)
                raise

            except FloodWait as e:
                last_error = str(e)
                wait_time = e.value
                if wait_time > settings.flood_wait_max_sleep:
                    logger.error(f"❌ Required wait time ({wait_time}s) exceeds maximum ({settings.flood_wait_max_sleep}s)")
                    return False, f'FloodWait of {wait_time}s exceeds FLOOD_WAIT_MAX_SLEEP'
                logger.warning(f"Telegram says: [420 FLOOD_WAIT_X] - A wait of {wait_time} seconds is required")
                logger.info(f"💤 FloodWait: Waiting {wait_time} seconds (attempt {attempt}/{max_attempts})")
                pbar.set_postfix(file=name, status=f"Waiting {wait_time}s for rate limit...")
                await self.sleep(wait_time)
                continue

            except Exception as e:
                last_error = str(e)
                if self.last_reason and self.last_reason not in last_error:
                    last_error = f"{last_error} (last error: {self.last_reason})"
                logger.error(f"Download attempt {attempt} failed: {last_error}")
                if os.path.exists(temp_destination):
                    os.remove(temp_destination)
                if isinstance(e, OSError) and not os.path.isdir(os.path.dirname(destination)):
                    raise

            if attempt < max_attempts and zero_bytes_attempts < max_zero_bytes_attempts:
                wait_time = min(2 ** attempt, 60)
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                await self.sleep(wait_time)
            elif zero_bytes_attempts >= max_zero_bytes_attempts:
                break

        logger.error(f"❌ Download failed after {attempt} attempts: {name}")
        pbar.set_postfix(file=name, status="Download failed ❌")
        return False, last_error

    async def download(self, file_id: str, temp_destination: str, name: str, pbar, heartbeat, size: int) -> None:
        """One attempt; last_reason is the error behind a failure, which the library logs but does not raise."""
        started = last_shown = self.clock()

        # A coroutine: the library runs a plain function on a worker thread, where the archive's SQLite connection cannot be used.
        async def progress(current: int, total: int) -> None:
            nonlocal last_shown
            now = self.clock()
            if now - last_shown >= PROGRESS_SECONDS:
                pbar.set_postfix(file=name, status=format_progress(current, total or size, now - started))
                last_shown = now
            if heartbeat is not None:
                heartbeat()

        memo = LastWarning()
        original_level = library_logger.level
        library_logger.setLevel(logging.WARNING)
        library_logger.addHandler(memo)
        try:
            await self.client.download_media(file_id, temp_destination, progress=progress)
        finally:
            self.last_reason = memo.reason
            library_logger.removeHandler(memo)
            library_logger.setLevel(original_level)
