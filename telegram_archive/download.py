"""Downloading one file, with retries, flood waits and the free-space check."""
import asyncio
import logging
import os
import shutil

from pyrogram.errors import FloodWait

from .settings import Settings

logger = logging.getLogger(__name__)


class LowDiskSpace(Exception):
    pass


def free_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


class Downloader:
    def __init__(self, client, settings: Settings, sleep=asyncio.sleep, free_bytes=free_bytes) -> None:
        self.client = client
        self.settings = settings
        self.sleep = sleep
        self.free_bytes = free_bytes

    async def fetch(self, file_id: str, destination: str, pbar) -> tuple[bool, str | None]:
        """Downloads file_id to destination unless it is there. Returns (ok, the last error)."""
        if os.path.exists(destination):
            return True, None

        settings = self.settings
        free_mb = self.free_bytes(os.path.dirname(destination)) // (1024 * 1024)
        if free_mb < settings.min_free_disk_mb:
            raise LowDiskSpace(f"{free_mb:,} MB free at {os.path.dirname(destination)}, below MIN_FREE_DISK_MB={settings.min_free_disk_mb:,}")

        temp_destination = f"{destination}.tmp" if settings.atomic_writes else destination
        zero_bytes_attempts = 0
        last_error = 'zero bytes written'
        max_zero_bytes_attempts = settings.zero_bytes_max_retries

        for attempt in range(1, settings.download_max_retries + 1):
            try:
                pbar.set_postfix(status=f"Downloading... (attempt {attempt})")

                pyrogram_logger = logging.getLogger("pyrogram")
                original_level = pyrogram_logger.level
                pyrogram_logger.setLevel(logging.CRITICAL)
                try:
                    await self.client.download_media(file_id, temp_destination)
                finally:
                    pyrogram_logger.setLevel(original_level)

                if os.path.exists(temp_destination) and os.path.getsize(temp_destination) > 0:
                    if settings.atomic_writes:
                        os.replace(temp_destination, destination)
                    file_size = os.path.getsize(destination)
                    logger.info(f"✅ Downloaded: {os.path.basename(destination)} ({file_size:,} bytes)")
                    pbar.set_postfix(status="Downloaded ✅")
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
                    pbar.set_postfix(status=f"Suspected flood wait: {estimated_wait}s...")
                    await self.sleep(estimated_wait)
                    zero_bytes_attempts = 0
                    continue

            except FloodWait as e:
                last_error = str(e)
                wait_time = e.value
                if wait_time > settings.flood_wait_max_sleep:
                    logger.error(f"❌ Required wait time ({wait_time}s) exceeds maximum ({settings.flood_wait_max_sleep}s)")
                    return False, f'FloodWait of {wait_time}s exceeds FLOOD_WAIT_MAX_SLEEP'
                logger.warning(f"Telegram says: [420 FLOOD_WAIT_X] - A wait of {wait_time} seconds is required")
                logger.info(f"💤 FloodWait: Waiting {wait_time} seconds (attempt {attempt}/{settings.download_max_retries})")
                pbar.set_postfix(status=f"Waiting {wait_time}s for rate limit...")
                await self.sleep(wait_time)
                continue

            except Exception as e:
                last_error = str(e)
                logger.error(f"Download attempt {attempt} failed: {str(e)}")
                if os.path.exists(temp_destination):
                    os.remove(temp_destination)
                if isinstance(e, OSError) and not os.path.isdir(os.path.dirname(destination)):
                    raise

            if attempt < settings.download_max_retries and zero_bytes_attempts < max_zero_bytes_attempts:
                wait_time = min(2 ** attempt, 60)
                logger.info(f"⏳ Waiting {wait_time}s before retry...")
                await self.sleep(wait_time)
            elif zero_bytes_attempts >= max_zero_bytes_attempts:
                break

        logger.error(f"❌ Download failed after {attempt} attempts: {os.path.basename(destination)}")
        pbar.set_postfix(status="Download failed ❌")
        return False, last_error
