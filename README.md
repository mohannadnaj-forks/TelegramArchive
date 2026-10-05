# Telegram-Archive
### Export telegram account chats
 - [x] All private chats
 - [x] Specefic chats (username or ID)
 - [x] export channels that restrict saving content
 - [ ] All contacts in your account

- #### All channels in your account
    - [x] public channels
    - [ ] private channels
- #### All groups in your account
    - [x] public groups
    - [ ] private groups

### Export media in each chat
You can choose to export and download each media type in `settings.env` (see [Settings](#settings)).
```
MEDIA_EXPORT_AUDIOS=false
MEDIA_EXPORT_VIDEOS=false
MEDIA_EXPORT_PHOTOS=true
MEDIA_EXPORT_STICKERS=false
MEDIA_EXPORT_ANIMATIONS=false
MEDIA_EXPORT_DOCUMENTS=false
MEDIA_EXPORT_VOICE_MESSAGES=false
MEDIA_EXPORT_VIDEO_MESSAGES=false
MEDIA_EXPORT_CONTACTS=false
```
And, for `--all`, which kinds of chat:
```
CHAT_EXPORT_PERSONALS=False
CHAT_EXPORT_CHANNELS=False
CHAT_EXPORT_GROUPS=False
CHAT_EXPORT_SUPER_GROUPS=False
CHAT_EXPORT_CONTACTS=True
CHAT_EXPORT_BOTS=False
```

### Export assholes chat
- [ ] Export telegram chat for each T (time: minutes) to backup asshole people chat who delete chats both-side.

### Export format
- [x] json
- [x] html viewer

## Run
```shell
make build-manual   # creates .venv and installs requirements
make run CHATS="durov https://t.me/telegram" OUT=/path/to/exports
```
or directly:
```shell
.venv/bin/python bot.py durov https://t.me/telegram --output /path/to/exports
.venv/bin/python bot.py me                 # Saved Messages
.venv/bin/python bot.py --all              # every chat allowed by CHAT_EXPORT_* in settings.env
```

| Option | Default | |
|---|---|---|
| `--since`, `--until` `YYYY-MM-DD` | whole history | Only messages in this range, both days included. Ranges exported at different times merge into the same export. |
| `--max-file-size SIZE` | `200M` | Files larger than this are left out and marked as such in the archive. |
| `--max-total-size SIZE` | `10G` | How much one run downloads at most. The oldest files beyond it are left out and marked as such; the next run continues with them, newest first. |
| `--refresh` | off | Re-read the whole history. Without it, a run lists only messages it has not listed before, so edits to older messages are not picked up. |
| `--retry-failed` | off | Try again now the files whose download failed in three runs or more. Without it such a file waits until a run made a week or more after its last failure: Telegram can refuse a file for days and serve it later. |

Sizes take `K`, `M`, `G` suffixes; `0` means no limit. With make, pass these through `ARGS="..."`.

To change the viewer without re-running an export, rebuild it from the existing `archive.db`:
```shell
.venv/bin/python bot.py --viewer-only durov --output /path/to/exports
make viewer CHATS="durov" OUT=/path/to/exports
```
It also accepts an archive folder in place of a chat name and does not connect to Telegram.
Chats are usernames, `t.me` links or numeric ids. Without `--output` the export goes to
`DOWNLOAD_PATH` from the settings, or `./exports`.

### Settings
The settings and the Telegram login are kept in one folder per user, the same from any copy of the code:

| | |
|---|---|
| Windows | `%APPDATA%\hamstra\telegram` |
| macOS, Linux | `~/.config/hamstra/telegram` (under `$XDG_CONFIG_HOME` when that is set) |

`--config-dir DIR` or the environment variable `HAMSTRA_TELEGRAM_CONFIG_DIR` names another folder.
It holds:

- `settings.env`: the API ID and hash, and the settings shown above. An environment variable of
  the same name wins over a line in this file.
- `account.session`: the login. Anyone holding this file can act as your account.
- `account.lock`: held while a run is using the login.

The first run asks for the API ID and API hash, which you create at https://my.telegram.org under
"API development tools", and writes `settings.env` with every setting in it. Then Telegram asks for
your phone number, the login code it sends you, and your two-step password if you have one. A run
that is not typed at a terminal does not ask; it stops and says where the two values go.
Only one run can use the login at a time; to export several chats, name them all in one command.

#### Moving from `.env` and `.telegram/`
Earlier versions kept both next to the code, and they are no longer read there. Move them once, from
the folder holding `bot.py`:
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

Each chat is archived to `telegram-<username>/` with `archive.db` (a SQLite file holding the
messages, the state of every file and every run; [docs/export-format.md](docs/export-format.md)
describes it), the files under `media/<month>/`, and an `index.html` viewer. The viewer opens straight from disk, no server needed; it reads
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
When the program is started from a `.cmd` file, Windows asks `Terminate batch job (Y/N)?`
after Ctrl-C. Progress is already saved by then, so either answer is fine.

### Docker
Set `CHATS` and `HOST_DOWNLOAD_PATH` in the environment, then `make build-up`. Log in once with
`make run` first: the container uses the settings folder of the host, `~/.config/hamstra/telegram`
unless `HAMSTRA_TELEGRAM_CONFIG_DIR` names another.

## Tests
```shell
.venv/bin/python -m unittest
```
The tests run the program against a fake Telegram client (`tests/fake_telegram.py`), with no network
and no login. `HAMSTRA_TELEGRAM_SLOW_TESTS=1` adds a 200,000-message memory test.

## Contribute
Read [CONTRIBUTING.md](CONTRIBUTING.md).
