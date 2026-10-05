"""Settings from settings.env in the per-user folder and from the environment, which wins."""
import os
import re
from dataclasses import dataclass, field
from importlib import resources
from typing import Mapping

from dotenv import dotenv_values, set_key

CONFIG_DIR_VARIABLE = 'HAMSTRA_TELEGRAM_CONFIG_DIR'
SETTINGS_FILE = 'settings.env'
SETTINGS_TEMPLATE = 'settings.example.env'

MEDIA_SETTINGS = ('audios', 'videos', 'photos', 'stickers', 'animations', 'documents', 'voice_messages',
                  'video_messages', 'contacts')
CHAT_SETTINGS = {'contacts': 'CONTACTS', 'bot': 'BOTS', 'personal': 'PERSONALS', 'channel': 'CHANNELS',
                 'group': 'GROUPS', 'super_group': 'SUPER_GROUPS'}


def str_to_bool(value) -> bool:
    return str(value).lower() in ('true', '1', 't', 'yes')


def media_setting_name(key: str) -> str:
    return f'MEDIA_EXPORT_{key.upper()}'


def default_config_dir(env: Mapping[str, str], windows: bool = os.name == 'nt') -> str:
    if windows:
        base = env.get('APPDATA') or os.path.join(os.path.expanduser('~'), 'AppData', 'Roaming')
    else:
        base = env.get('XDG_CONFIG_HOME', '')
        if not os.path.isabs(base):
            base = os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'hamstra', 'telegram')


def find_config_dir(option: str | None, env: Mapping[str, str]) -> str:
    chosen = option or env.get(CONFIG_DIR_VARIABLE)
    return os.path.abspath(os.path.expanduser(chosen)) if chosen else default_config_dir(env)


def read_settings(config_dir: str, env: Mapping[str, str]) -> dict:
    """The values of settings.env, each replaced by the environment's where it has the same name and is not empty."""
    saved = {key: value for key, value in dotenv_values(os.path.join(config_dir, SETTINGS_FILE)).items() if value is not None}
    return {**env, **{key: value for key, value in saved.items() if not env.get(key)}}


def valid_api_pair(api_id: str | None, api_hash: str | None) -> bool:
    return bool((api_id or '').strip().isdigit() and api_hash)


def save_api_pair(config_dir: str, api_id: str, api_hash: str) -> str:
    """Writes the pair to settings.env, created from the example with every setting when it is not there yet."""
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    path = os.path.join(config_dir, SETTINGS_FILE)
    if not os.path.exists(path):
        template = resources.files(__package__).joinpath(SETTINGS_TEMPLATE).read_text(encoding='utf-8')
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8', newline='\n') as f:
            f.write(template)
    set_key(path, 'API_ID', api_id, quote_mode='never', encoding='utf-8')
    set_key(path, 'API_HASH', api_hash, quote_mode='never', encoding='utf-8')
    return path


def ask_api_pair(ask, say=print) -> tuple:
    say("Telegram asks every application for an API ID and an API hash. They identify this program, not your account.\n"
        "Create them once at https://my.telegram.org (API development tools) and paste them here.")
    while not (api_id := ask("API ID: ").strip()).isdigit():
        say("The API ID is a number.")
    while not re.fullmatch(r'[0-9a-f]{32}', api_hash := ask("API hash: ").strip().lower()):
        say("The API hash is 32 characters, digits and the letters a to f.")
    return api_id, api_hash


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
