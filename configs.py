import os
from dotenv import load_dotenv

# Load .env in local dev (has no effect in Docker unless .env is mounted/copied)
load_dotenv()

def str_to_bool(val):
    return str(val).lower() in ('true', '1', 't', 'yes')

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")

# todo: check if this path exist? or not and go to failed and panic
DOWNLOAD_PATH = os.environ.get("DOWNLOAD_PATH")

MEDIA_EXPORT = {
    'audios': str_to_bool(os.environ.get("MEDIA_EXPORT_AUDIOS")),
    'videos': str_to_bool(os.environ.get("MEDIA_EXPORT_VIDEOS")),
    'photos': str_to_bool(os.environ.get("MEDIA_EXPORT_PHOTOS")),
    'stickers': str_to_bool(os.environ.get("MEDIA_EXPORT_STICKERS")),
    'animations': str_to_bool(os.environ.get("MEDIA_EXPORT_ANIMATIONS")),
    'documents': str_to_bool(os.environ.get("MEDIA_EXPORT_DOCUMENTS")),
    'voice_messages': str_to_bool(os.environ.get("MEDIA_EXPORT_VOICE_MESSAGES")),
    'video_messages': str_to_bool(os.environ.get("MEDIA_EXPORT_VIDEO_MESSAGES")),
    'contacts': str_to_bool(os.environ.get("MEDIA_EXPORT_CONTACTS")),
}

CHAT_EXPORT = {
    'contacts': str_to_bool(os.environ.get("CHAT_EXPORT_CONTACTS")), # TODO
    'bot': str_to_bool(os.environ.get("CHAT_EXPORT_BOTS")),
    'personal': str_to_bool(os.environ.get("CHAT_EXPORT_PERSONALS")),
    'channel': str_to_bool(os.environ.get("CHAT_EXPORT_CHANNELS")),
    'group': str_to_bool(os.environ.get("CHAT_EXPORT_GROUPS")),
    'super_group': str_to_bool(os.environ.get("CHAT_EXPORT_SUPER_GROUPS")),
}

page_size = os.environ.get('JSON_FILE_PAGE_SIZE', '').strip().split('#')[0].strip()
JSON_FILE_PAGE_SIZE = None if page_size.lower() in ('', 'none') else int(page_size)

# FloodWait handling
FLOOD_WAIT_MAX_SLEEP = int(os.environ.get("FLOOD_WAIT_MAX_SLEEP", "7200"))
DOWNLOAD_MAX_RETRIES = int(os.environ.get("DOWNLOAD_MAX_RETRIES", "5"))

# Smart flood wait detection
ZERO_BYTES_MAX_RETRIES = int(os.environ.get("ZERO_BYTES_MAX_RETRIES", "2"))
SUSPECTED_FLOOD_WAIT_DURATION = int(os.environ.get("SUSPECTED_FLOOD_WAIT_DURATION", "300"))

# Checkpointing
RESUME_ENABLED = str_to_bool(os.environ.get("RESUME_ENABLED", "True"))

# File safety
ATOMIC_WRITES = str_to_bool(os.environ.get("ATOMIC_WRITES", "True"))
MIN_FREE_DISK_MB = int(os.environ.get("MIN_FREE_DISK_MB", "2048"))

FILE_NOT_FOUND = '(File not included. Change data exporting settings to download.)'
NOT_INCLUDED = {
    'pending': '(File not downloaded yet. Run the export again to continue.)',
    'disabled': FILE_NOT_FOUND,
    'too_large': '(File exceeds maximum size. Change data exporting settings to download.)',
    'total_limit': '(File not included. Total media size limit reached; run again with a larger --max-total-size.)',
    'failed': '(File not included. Download failed; run again to retry.)',
}
