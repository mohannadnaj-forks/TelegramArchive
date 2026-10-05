<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.svg">
    <img src="docs/images/logo.svg" alt="hamstra" width="360">
  </picture>
</p>

# hamstra-telegram

Archives Telegram chats to a folder on your disk: the messages in a SQLite file, the media files,
and an HTML viewer that opens without a server. A run can be stopped and continued, and a later run
adds what is new.

*hamstra* is Swedish for "to hoard", literally "to hamster". This is the Telegram member of a small
family of archivers that are meant to share one archive format.

> **This is a fork.** hamstra-telegram started as a fork of
> [mo1ein/TelegramArchive](https://github.com/mo1ein/TelegramArchive) by Moein Halvaei (MIT, 2022)
> and has been developed separately since September 2026. The command line, the settings and the
> archive format are no longer compatible with the original. If you are looking for the original
> project, follow the link. The licence and its copyright notice are unchanged.

It is not an official Telegram application and is not affiliated with Telegram.

## What it exports
- Private chats, groups, channels and Saved Messages, named by username, `t.me` link or numeric id,
  including channels that restrict saving content.
- With `--all`, every chat of the kinds switched on in the settings.
- Photos, videos, documents, audio, voice and video messages, stickers, animations and contact
  cards, each kind switched on or off in the settings.

## Install
You need [uv](https://docs.astral.sh/uv/getting-started/installation/), which also fetches Python
itself. The steps are the same on Windows, macOS and Linux.

From a clone, so that `git pull` updates the command:
```shell
git clone https://github.com/mohannadnaj-forks/hamstra-telegram
cd hamstra-telegram
uv tool install --python 3.13 --editable .
```
or without keeping a clone:
```shell
uv tool install --python 3.13 git+https://github.com/mohannadnaj-forks/hamstra-telegram
```
If the shell then does not find `hamstra-telegram`, run `uv tool update-shell` and open a new
terminal. `pipx install .` and `pip install .` work as well, on Python 3.10 or newer. From a clone
with the dependencies installed, `python bot.py` and `python -m hamstra_telegram` run the same
program without installing the command.

## Run
```shell
hamstra-telegram durov https://t.me/telegram --output /path/to/exports
hamstra-telegram me        # Saved Messages
hamstra-telegram --all     # every chat allowed by CHAT_EXPORT_* in settings.env
```
Chats are usernames, `t.me` links or numeric ids. Without `--output` the export goes to
`DOWNLOAD_PATH` from the settings, or `./exports` in the folder you run from. The settings file and
what the first run asks are described under [Settings](#settings).

A large chat takes a while. For a first try, a small limit such as `--max-total-size 40M` shows the
whole flow in a minute or two; the next run continues with the files it left out.

| Option | Default | |
|---|---|---|
| `--since`, `--until` `YYYY-MM-DD` | whole history | Only messages in this range, both days included. Ranges exported at different times merge into the same export. |
| `--max-file-size SIZE` | `200M` | Files larger than this are left out and marked as such in the archive. |
| `--max-total-size SIZE` | `10G` | How much one run downloads at most. The oldest files beyond it are left out and marked as such; the next run continues with them, newest first. |
| `--refresh` | off | Re-read the whole history. Without it, a run lists only messages it has not listed before, so edits to older messages are not picked up. |
| `--retry-failed` | off | Try again now the files whose download failed in three runs or more. Without it such a file waits until a run made a week or more after its last failure: Telegram can refuse a file for days and serve it later. |

Sizes take `K`, `M`, `G` suffixes; `0` means no limit.

To change the viewer without re-running an export, rebuild it from the existing `archive.db`:
```shell
hamstra-telegram --viewer-only durov --output /path/to/exports
```
It also accepts an archive folder in place of a chat name and does not connect to Telegram.

### Settings
The settings and the Telegram login are kept in one folder per user, the same from any copy of the code
and for the installed command:

| | |
|---|---|
| Windows | `%APPDATA%\hamstra\telegram` |
| macOS, Linux | `~/.config/hamstra/telegram` (under `$XDG_CONFIG_HOME` when that is set) |

`--config-dir DIR` or the environment variable `HAMSTRA_TELEGRAM_CONFIG_DIR` names another folder.
It holds:

- `settings.env`: the API ID and hash, which kinds of file a run downloads (`MEDIA_EXPORT_PHOTOS`,
  `MEDIA_EXPORT_VIDEOS`, …), which kinds of chat `--all` exports (`CHAT_EXPORT_CHANNELS`, …), and a
  few limits. The file lists every setting with a comment; a new one switches on photos and
  stickers only. An environment variable of the same name wins over a line in this file.
- `account.session`: the login. Anyone holding this file can act as your account.
- `account.lock`: held while a run is using the login.

The first run asks for the API ID and API hash, which you create at https://my.telegram.org under
"API development tools", and writes `settings.env` with every setting in it. Then Telegram asks for
your phone number, the login code it sends you, and your two-step password if you have one. A run
that is not typed at a terminal does not ask; it stops and says where the two values go.
Only one run can use the login at a time; to export several chats, name them all in one command.

#### Moving from `.env` and `.telegram/`
Earlier versions kept both next to the code, and they are no longer read there. Move them once, from
the folder holding `bot.py`, while no export is running:
```shell
mkdir -p ~/.config/hamstra/telegram && chmod 700 ~/.config/hamstra/telegram
mv .telegram/my_bot.session ~/.config/hamstra/telegram/account.session
mv .env ~/.config/hamstra/telegram/settings.env
```
```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\hamstra\telegram" | Out-Null
Move-Item .telegram\my_bot.session "$env:APPDATA\hamstra\telegram\account.session"
Move-Item .env "$env:APPDATA\hamstra\telegram\settings.env"
```
If another copy of the code has its own `.telegram/`, delete that copy's session file rather than
keeping two.

### The archive
Each chat is archived to `telegram-<username>/` with `archive.db` (a SQLite file holding the
messages, the state of every file and every run; [docs/export-format.md](docs/export-format.md)
describes it), the files under `media/<month>/`, the chat's picture under `account/`, and an
`index.html` viewer. The viewer opens straight from disk, no server needed; it reads
the messages from `data/` (one file per month, plus an index and a search file) and loads
only the months near what is on screen. Every run keeps the messages already exported, reads what is new
(the whole chat on the first run or with `--refresh`), and downloads whatever the current
settings want that is not on disk yet — so
raising a limit, switching a media type on, or a failed download is fixed by running again.
The archive records for each file whether it was downloaded and, if not, why (`disabled`,
`too_large`, `total_limit`, `failed`, or `unavailable` for a message deleted before its file
was fetched).
A run first lists the messages, newest first, then downloads their files, newest first. A file
being downloaded is `<its name>.tmp` next to where it will end up; the progress line shows the
bytes and speed of a large one.
Ctrl-C stops after the current message and saves progress, in either pass; the next run
continues where it stopped, without listing again what was already listed. The run also
stops with progress saved when free disk space falls below `MIN_FREE_DISK_MB`.

### Windows
Paths longer than 260 characters fail unless long paths are enabled. Enable them once from an
administrator PowerShell:
```powershell
Set-ItemProperty HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem -Name LongPathsEnabled -Value 1
```
The installed `hamstra-telegram` is started without a `.cmd` file. If you wrap it in one, Windows
asks `Terminate batch job (Y/N)?` after Ctrl-C; progress is already
saved by then, so either answer is fine.

### Docker
The Dockerfile and compose file have not been built or run since the settings moved and the
command was renamed; treat them as a starting point. Set `CHATS` and `HOST_DOWNLOAD_PATH` in the
environment, then `make build-up`. Log in once on the host first: the container uses the host's
settings folder, `~/.config/hamstra/telegram` unless `HAMSTRA_TELEGRAM_CONFIG_DIR` names another.

## Tests
With Python 3.10 or newer (`python3` where `python` is not found):
```shell
python -m venv .venv
.venv/bin/python -m pip install -e .    # .venv\Scripts\python on Windows
.venv/bin/python -m unittest
```
The tests run the program against a fake Telegram client (`tests/fake_telegram.py`), with no network
and no login. `HAMSTRA_TELEGRAM_SLOW_TESTS=1` adds a 200,000-message memory test.

## Contribute
Read [CONTRIBUTING.md](CONTRIBUTING.md).
