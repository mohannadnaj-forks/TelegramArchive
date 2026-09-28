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

from dotenv import load_dotenv

from .download import Downloader
from .export import ChatExport, RunOptions, StopRequest
from .settings import Settings
from .store import Archive, ArchiveVersionError, find_archive, is_export_dir
from .telegram import api_chat_id, create_client, dialog_ids
from .viewer import generate_index_html

PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SESSION_DIR = os.path.join(PROGRAM_DIR, '.telegram')


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


def build_parser(download_path: str | None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Telegram chats to JSON, media files and an HTML viewer.")
    parser.add_argument('chats', nargs='*', help="usernames, t.me links or numeric ids; 'me' is Saved Messages")
    parser.add_argument('--all', action='store_true', help="export every chat allowed by the CHAT_EXPORT_* settings")
    parser.add_argument('-o', '--output', default=download_path or 'exports', help="directory to export into (default: DOWNLOAD_PATH from .env, else ./exports)")
    parser.add_argument('--since', type=parse_date, metavar='YYYY-MM-DD', help="only messages from this day on")
    parser.add_argument('--until', type=parse_date, metavar='YYYY-MM-DD', help="only messages up to and including this day")
    parser.add_argument('--max-file-size', type=parse_size, default='200M', metavar='SIZE', help="leave out files larger than this, e.g. 50M, 1G; 0 for no limit (default: 200M)")
    parser.add_argument('--max-total-size', type=parse_size, default='10G', metavar='SIZE', help="download at most this much in one run, leaving out the oldest files; the next run continues with them; 0 for no limit (default: 10G)")
    parser.add_argument('--refresh', action='store_true', help="re-read the whole history, refreshing edited messages, instead of only messages newer than the export")
    parser.add_argument('--viewer-only', action='store_true', help="rebuild index.html and data/ from the existing archive.db, without connecting to Telegram; chats may also be export directories")
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
    with open(os.path.join(session_dir, 'my_bot.lock'), 'a+') as lock:
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


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", datefmt='%Y-%m-%d %H:%M:%S')
    for name in ("telethon", "httpx", "pyrogram", "urllib3"):
        logging.getLogger(name).setLevel(logging.ERROR)
        logging.getLogger(name).propagate = False


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


def run(argv: list, env: Mapping[str, str], client_factory=create_client, session_dir: str = SESSION_DIR,
        sleep=asyncio.sleep, free_bytes=None, clock=time.time, zone: tzinfo | None = None) -> None:
    """Runs the command line against env; the other arguments are for tests."""
    settings = Settings.from_env(env)
    parser = build_parser(settings.download_path)
    args = parser.parse_args(argv)
    if args.since and args.until and args.since > args.until:
        parser.error("--since is after --until")
    if not args.chats and not args.all:
        parser.error("name at least one chat, or pass --all")
    if args.viewer_only and args.all:
        parser.error("--viewer-only needs the chats or export directories to rebuild")

    options = RunOptions(
        output=os.path.abspath(os.path.expanduser(args.output)),
        since=day_start(args.since, zone),
        until=day_start(args.until + timedelta(days=1), zone) if args.until else None,
        max_file_size=args.max_file_size,
        max_total_size=args.max_total_size,
        refresh=args.refresh,
        zone=zone,
    )
    if args.viewer_only:
        rebuild_viewers(args.chats, options.output)
        return

    if not (settings.api_id or '').strip().isdigit() or not settings.api_hash:
        sys.exit("❌ API_ID and API_HASH are not set. Create an application at https://my.telegram.org, "
                 "then put its API_ID and API_HASH in .env (see .env.example) or in the environment.")
    configure_logging()
    os.makedirs(session_dir, exist_ok=True)
    stop = StopRequest()
    previous_handler = signal.signal(signal.SIGINT, stop.handle)
    try:
        with session_lock(session_dir):
            os.makedirs(options.output, exist_ok=True)
            client = client_factory(settings, session_dir)
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


def main() -> None:
    load_dotenv()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding='utf-8', errors='replace')
    run(sys.argv[1:], os.environ)
