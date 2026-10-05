"""The command line: python bot.py <chats> [options]."""
import argparse
import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, tzinfo
from typing import Mapping

from .download import Downloader
from .export import ChatExport, RunOptions, StopRequest
from .settings import (CONFIG_DIR_VARIABLE, SETTINGS_FILE, Settings, ask_api_pair, find_config_dir, read_settings,
                       save_api_pair, valid_api_pair)
from .store import Archive, ArchiveVersionError, find_archive, is_export_dir
from .telegram import SESSION_NAME, api_chat_id, create_client, dialog_ids
from .viewer import generate_index_html

LOG_FORMAT, LOG_DATE_FORMAT = "%(asctime)s - %(message)s", '%Y-%m-%d %H:%M:%S'


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


def day_start(day: datetime | None, zone: tzinfo | None) -> datetime | None:
    if day is None:
        return None
    return day.replace(tzinfo=zone) if zone else day.astimezone()


def parse_chat(value: str):
    value = re.sub(r'^(https?://)?(t\.me|telegram\.me)/', '', value.strip()).lstrip('@').split('/')[0].split('?')[0]
    return int(value) if re.fullmatch(r'-?\d+', value) else value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Archive Telegram chats: messages in archive.db, media files and an HTML viewer.")
    parser.add_argument('chats', nargs='*', help="usernames, t.me links or numeric ids; 'me' is Saved Messages")
    parser.add_argument('--all', action='store_true', help="export every chat allowed by the CHAT_EXPORT_* settings")
    parser.add_argument('-o', '--output', help="directory to export into (default: DOWNLOAD_PATH from the settings, else ./exports)")
    parser.add_argument('--since', type=parse_date, metavar='YYYY-MM-DD', help="only messages from this day on")
    parser.add_argument('--until', type=parse_date, metavar='YYYY-MM-DD', help="only messages up to and including this day")
    parser.add_argument('--max-file-size', type=parse_size, default='200M', metavar='SIZE', help="leave out files larger than this, e.g. 50M, 1G; 0 for no limit (default: 200M)")
    parser.add_argument('--max-total-size', type=parse_size, default='10G', metavar='SIZE', help="download at most this much in one run, leaving out the oldest files; the next run continues with them; 0 for no limit (default: 10G)")
    parser.add_argument('--refresh', action='store_true', help="re-read the whole history, refreshing edited messages, instead of only messages newer than the export")
    parser.add_argument('--retry-failed', action='store_true', help="try again now the files whose download failed in 3 runs or more, which otherwise wait 7 days after their last failure")
    parser.add_argument('--viewer-only', action='store_true', help="rebuild index.html and data/ from the existing archive.db, without connecting to Telegram; chats may also be export directories")
    parser.add_argument('--config-dir', metavar='DIR', help=f"folder holding {SETTINGS_FILE} and the Telegram login (default: {CONFIG_DIR_VARIABLE}, else the per-user folder)")
    return parser


def is_named(account: dict, chat) -> bool:
    """Whether the chat named on the command line (a username, 'me' or an id in either form) is this account."""
    if isinstance(chat, int):
        return re.sub(r'^\D+', '', account.get('id', '')) in (str(abs(chat)), str(chat).removeprefix('-100'))
    if chat == 'me':
        return account.get('kind') == 'saved'
    return (account.get('username') or '').lower() == chat.lower()


def rebuild_viewers(targets: list, output: str) -> None:
    for target in targets:
        if is_export_dir(target):
            export_directory, name = os.path.abspath(target), target
        else:
            chat = parse_chat(target)
            name = str(chat)
            export_directory = find_archive(output, lambda account: is_named(account, chat))
            if export_directory is None:
                print(f"❌ No archive of {name} under {output}")
                continue
        try:
            archive = Archive(export_directory)
        except ArchiveVersionError as e:
            print(f"❌ {e}")
            continue
        try:
            generate_index_html(export_directory, archive)
            print(f"✅ Rebuilt viewer for {archive.get('account', {}).get('name', name)}: {archive.count():,} messages "
                  f"-> {export_directory}/index.html")
        finally:
            archive.close()


@contextlib.contextmanager
def session_lock(session_dir: str):
    # Two clients on one session can lock its database or get the login revoked (AUTH_KEY_DUPLICATED).
    with open(os.path.join(session_dir, f'{SESSION_NAME}.lock'), 'a+') as lock:
        try:
            if os.name == 'nt':
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("❌ Another export is already running with this Telegram login. "
                     "Wait for it to finish, or name several chats in one command.")
        yield


def library_handler(stream=None) -> logging.Handler:
    """Prints the Telegram library's errors, except the send failure it logs while disconnecting."""
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.ERROR)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE_FORMAT))
    handler.addFilter(lambda record: not record.getMessage().startswith('Send failed'))
    return handler


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE_FORMAT)
    for name in ("telethon", "httpx", "pyrogram", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
        logging.getLogger(name).propagate = False
    library = logging.getLogger("pyrogram")
    if not library.handlers:
        library.addHandler(library_handler())


async def export_chats(client, chats: list, export_all: bool, settings: Settings, options: RunOptions,
                       stop: StopRequest, downloader_options: dict, clock) -> None:
    async with client:
        print("\033[32mStarting...\033[0m")
        chat_ids = [api_chat_id(c) for c in chats]
        if export_all:
            chat_ids.extend(await dialog_ids(client, settings.chats))

        for cid in chat_ids:
            chat = await client.get_chat(cid)
            title = getattr(chat, 'title', None) or getattr(chat, 'first_name', 'Unknown')
            print(f"📋 Exporting: {title} (@{getattr(chat, 'username', None) or chat.id})")
            downloader = Downloader(client, settings, **downloader_options)
            if not await ChatExport(client, chat, cid, settings, options, stop, downloader, clock).run():
                return


def api_settings(settings: Settings, config_dir: str, env: Mapping[str, str], ask) -> Settings:
    """The settings with an API pair: as they are, or after asking for the pair and saving it."""
    if valid_api_pair(settings.api_id, settings.api_hash):
        return settings
    if ask is None:
        sys.exit("❌ API_ID and API_HASH are not set. Create an application at https://my.telegram.org, then run this "
                 f"in a terminal to be asked for them, or put them in {os.path.join(config_dir, SETTINGS_FILE)} "
                 "or in the environment.")
    try:
        api_id, api_hash = ask_api_pair(ask)
    except (EOFError, KeyboardInterrupt):
        sys.exit("\n❌ No API ID and hash given.")
    print(f"Saved to {save_api_pair(config_dir, api_id, api_hash)}")
    return Settings.from_env({**read_settings(config_dir, env), 'API_ID': api_id, 'API_HASH': api_hash})


def run(argv: list, env: Mapping[str, str], client_factory=create_client, sleep=asyncio.sleep, free_bytes=None,
        clock=time.time, zone: tzinfo | None = None, ask=None) -> None:
    """Runs the command line against env; ask reads an answer when there is someone to ask, and the rest is for tests."""
    parser = build_parser()
    args = parser.parse_args(argv)
    config_dir = find_config_dir(args.config_dir, env)
    settings = Settings.from_env(read_settings(config_dir, env))
    if args.since and args.until and args.since > args.until:
        parser.error("--since is after --until")
    if not args.chats and not args.all:
        parser.error("name at least one chat, or pass --all")
    if args.viewer_only and args.all:
        parser.error("--viewer-only needs the chats or export directories to rebuild")

    options = RunOptions(
        output=os.path.abspath(os.path.expanduser(args.output or settings.download_path or 'exports')),
        since=day_start(args.since, zone),
        until=day_start(args.until + timedelta(days=1), zone) if args.until else None,
        max_file_size=args.max_file_size,
        max_total_size=args.max_total_size,
        refresh=args.refresh,
        retry_failed=args.retry_failed,
        zone=zone,
    )
    if args.viewer_only:
        rebuild_viewers(args.chats, options.output)
        return

    settings = api_settings(settings, config_dir, env, ask)
    configure_logging()
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    stop = StopRequest()
    previous_handler = signal.signal(signal.SIGINT, stop.handle)
    try:
        with session_lock(config_dir):
            os.makedirs(options.output, exist_ok=True)
            client = client_factory(settings, config_dir)
            downloader_options = {'sleep': sleep, **({'free_bytes': free_bytes} if free_bytes else {})}
            try:
                asyncio.run(export_chats(client, [parse_chat(c) for c in args.chats], args.all, settings, options,
                                         stop, downloader_options, clock))
            except KeyboardInterrupt:
                print("\n👋 Interrupted.")
            except ArchiveVersionError as e:
                sys.exit(f"❌ {e}")
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def configure_streams() -> None:
    """UTF-8 output, written line by line also when redirected to a file."""
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)


def main() -> None:
    configure_streams()
    run(sys.argv[1:], os.environ, ask=input if sys.stdin.isatty() and sys.stdout.isatty() else None)
