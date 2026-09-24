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
You can choose to export and download each media type in `.env` file.
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
make env            # creates .env; set API_ID and API_HASH from https://my.telegram.org
make build-manual   # creates .venv and installs requirements
make run CHATS="durov https://t.me/telegram" OUT=/path/to/exports
```
or directly:
```shell
.venv/bin/python bot.py durov https://t.me/telegram --output /path/to/exports
.venv/bin/python bot.py me                 # Saved Messages
.venv/bin/python bot.py --all              # every chat allowed by CHAT_EXPORT_* in .env
```

| Option | Default | |
|---|---|---|
| `--since`, `--until` `YYYY-MM-DD` | whole history | Only messages in this range, both days included. Ranges exported at different times merge into the same export. |
| `--max-file-size SIZE` | `200M` | Files larger than this are left out and marked as such in the JSON. |
| `--max-total-size SIZE` | `10G` | Files that would take a chat's media past this are left out; the rest of the export completes. Run again with a larger value to fetch them. |
| `--refresh` | off | Re-read the whole history. Without it, once an export is complete, a run lists only newer messages plus those whose file is still to download, so edits to older messages are not picked up. |

Sizes take `K`, `M`, `G` suffixes; `0` means no limit. With make, pass these through `ARGS="..."`.

To change the viewer without re-running an export, rebuild it from the existing `result.json`:
```shell
.venv/bin/python bot.py --viewer-only durov --output /path/to/exports
make viewer CHATS="durov" OUT=/path/to/exports
```
It also accepts an export directory in place of a chat name and does not connect to Telegram.
Chats are usernames, `t.me` links or numeric ids. Without `--output` the export goes to
`DOWNLOAD_PATH` from `.env`, or `./exports`.

The first run asks for your phone number, the login code Telegram sends you, and your
two-step password if you have one. The resulting session is stored in `.telegram/`
(git-ignored); anyone holding that file can act as your account.

Each chat is exported to `ChatExport_<chat>_<date>/` with `result.json`, the media folders
and an `index.html` viewer. The viewer opens straight from disk, no server needed; it reads
the messages from `data/` (one file per month, plus an index and a search file) and loads
only the months near what is on screen. Every run keeps the messages already exported, reads what is new
(the whole chat on the first run or with `--refresh`), and downloads whatever the current
settings want that is not on disk yet — so
raising a limit, switching a media type on, or a failed download is fixed by running again.
Each message's `file_status` in the JSON says whether its file was downloaded and, if not,
why (`disabled`, `too_large`, `total_limit`, `failed`).
Ctrl-C stops after the current message and saves progress; the run also stops with
progress saved when free disk space falls below `MIN_FREE_DISK_MB`.

### Docker
Set `CHATS` and `HOST_DOWNLOAD_PATH` in `.env`, then `make build-up`. Log in once with
`make run` first so that `.telegram/` holds a session for the container to use.

## Tests
```shell
.venv/bin/python -m unittest test_naming
```

## Contribute
Read [CONTRIBUTING.md](CONTRIBUTING.md).
