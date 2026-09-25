"""Settings read from the environment (and .env, which cli.main loads into it)."""
from dataclasses import dataclass, field
from typing import Mapping

MEDIA_SETTINGS = ('audios', 'videos', 'photos', 'stickers', 'animations', 'documents', 'voice_messages',
                  'video_messages', 'contacts')
CHAT_SETTINGS = {'contacts': 'CONTACTS', 'bot': 'BOTS', 'personal': 'PERSONALS', 'channel': 'CHANNELS',
                 'group': 'GROUPS', 'super_group': 'SUPER_GROUPS'}


def str_to_bool(value) -> bool:
    return str(value).lower() in ('true', '1', 't', 'yes')


def media_setting_name(key: str) -> str:
    return f'MEDIA_EXPORT_{key.upper()}'


@dataclass(frozen=True)
class Settings:
    api_id: str | None = None
    api_hash: str | None = None
    download_path: str | None = None
    media: dict = field(default_factory=lambda: dict.fromkeys(MEDIA_SETTINGS, False))
    chats: dict = field(default_factory=lambda: dict.fromkeys(CHAT_SETTINGS, False))
    flood_wait_max_sleep: int = 7200
    download_max_retries: int = 5
    zero_bytes_max_retries: int = 2
    suspected_flood_wait_duration: int = 300
    checkpoint_seconds: int = 10
    resume_enabled: bool = True
    min_free_disk_mb: int = 2048

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> 'Settings':
        return cls(
            api_id=env.get('API_ID'),
            api_hash=env.get('API_HASH'),
            download_path=env.get('DOWNLOAD_PATH'),
            media={key: str_to_bool(env.get(media_setting_name(key))) for key in MEDIA_SETTINGS},
            chats={key: str_to_bool(env.get(f'CHAT_EXPORT_{name}')) for key, name in CHAT_SETTINGS.items()},
            flood_wait_max_sleep=int(env.get('FLOOD_WAIT_MAX_SLEEP', '7200')),
            download_max_retries=int(env.get('DOWNLOAD_MAX_RETRIES', '5')),
            zero_bytes_max_retries=int(env.get('ZERO_BYTES_MAX_RETRIES', '2')),
            suspected_flood_wait_duration=int(env.get('SUSPECTED_FLOOD_WAIT_DURATION', '300')),
            checkpoint_seconds=int(env.get('CHECKPOINT_SECONDS', '10')),
            resume_enabled=str_to_bool(env.get('RESUME_ENABLED', 'True')),
            min_free_disk_mb=int(env.get('MIN_FREE_DISK_MB', '2048')),
        )
