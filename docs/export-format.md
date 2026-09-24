# Export format

What an export directory holds today, as written by `bot.py`. This describes the
format as it is, including its gaps.

## Directory

```
ChatExport_<chat>_<YYYY-MM-DD>/
  result.json          the export (or result_part1.json, result_part2.json, ... when JSON_FILE_PAGE_SIZE is set;
                       each part has the top-level fields below and a consecutive run of the messages)
  export_state.json    progress of the export, for resuming and for other programs to read (see below)
  export_journal.jsonl only while a run is going, or after one was killed: records saved since result.json
                       was last written (see below)
  chat_photo.jpg       the chat's picture, downloaded once
  index.html, data/    the viewer, regenerated from result.json on every run and by --viewer-only:
                       data/index.js, one data/<YYYY-MM>.js per month (records unchanged), data/search.js
  photos/              photo_<id>.jpg, and a small copy of each as photo_<id>.jpg_thumb.jpg
  video_files/         videos and animations: video_<id>.mp4, or <id>_<original name>; thumbnails as <file>_thumb.jpg
  round_video_messages/, voice_messages/, stickers/, files/, contacts/
```

`<chat>` is the chat's username, or its numeric id when it has none. The date is
the day the export was first started; later runs continue the same directory.

File names are built from the message id (see `media_file_name` in `bot.py`, pinned by
`test_naming.py`): `<kind>_<message id><ext>` when the file has no name of its own, or
`<message id>_<original name, sanitised>` when it has one. Names use only characters valid
on exFAT, NTFS and Windows. The name is the same on every run, which is what resume relies on.

## Top level

```json
{
  "name": "Example Channel",
  "type": "public_channel",
  "id": "1234567890",
  "messages": [ ... ]
}
```

| field | notes |
|---|---|
| `name` | channel/group title, or the user's first name for a private chat |
| `type` | `public_channel`, `public_group`, `public_supergroup` or `personal_chat` (private groups and channels are a TODO in `Archive.fill_chat_data`) |
| `id` | for channels and groups, a string without the API's `-100` prefix; for private chats, the numeric user id |
| `username` | the chat's public username, when it has one |
| `description` | channel/group description, or the user's bio |
| `photo` | `chat_photo.jpg` when the picture was downloaded |
| `messages` | sorted by `id`, ascending |

Not recorded: member count, or when the export ran.

Exports made before these fields existed gain them on their next normal run.

## Message record

Every message has:

| field | example | notes |
|---|---|---|
| `id` | `5148` | Telegram message id; increases with time within a chat |
| `type` | `"message"` | always `"message"`; service messages are not distinguished |
| `date` | `"2024-04-16T16:25:48"` | local time of the machine that exported it, no zone |
| `date_unixtime` | `1713273948` | integer seconds |
| `from` | `"Example Channel"` | the channel's title in channels; elsewhere the sender's full name, or the sending chat's title for anonymous group admins and channels posting into a group |
| `from_id` | `"channel1234567890"` | `channel<id>` or `user<id>`, as for `from`. Group exports made before 2026-09-23 have the group's title as `from` and no `from_id` until their next `--refresh` |

Present when they apply:

| field | notes |
|---|---|
| `text` | the message text, see *Text with entities* below; `""` when the message has neither text nor caption |
| `caption` | the media caption, same shape as `text`; a message has `text` or `caption`, not both |
| `reply_to_message_id` | id of the message replied to; nothing else about it |
| `media_group_id` | string shared by the messages of one album |
| `views` | view count at the time the message was last processed (channels) |
| `forwarded_from` | the original chat's title or the original sender's first name; no id, username or original date |
| `location_information` | `{latitude, longitude}` |
| `contact_information` | `{phone_number, fist_name, last_name}` (`fist_name` is spelled that way), plus `contact_vcard` path |

A message with `text: ""` and no media is typically a service message (channel created,
message pinned) or content the exporter does not record (polls, for example). The large
test export has 346 of them out of 13,318.

### Media

Only the first matching kind is recorded, in this order: photo, video, animation, sticker,
video note, audio, voice, document.

| field | notes |
|---|---|
| `photo` | path relative to the export directory, e.g. `photos/photo_4.jpg`; photos only |
| `file` | path for every other kind, e.g. `video_files/video_5148.mp4` |
| `thumbnail` | `<file>_thumb.jpg` when Telegram supplied a thumbnail and the file was downloaded; otherwise the same value as `file` (for videos this means there is no image to show); absent for voice messages. For photos, `<photo>_thumb.jpg` (the photo's own `m` size, within 320 px) when the photo and its thumbnail were downloaded, and absent otherwise |
| `media_type` | `video_file`, `animation`, `sticker`, `video_message`, `audio_file`, `voice_message`; absent for photos and documents |
| `mime_type`, `duration_seconds`, `width`, `height`, `performer`, `title`, `sticker_emoji` | copied from the media object when Telegram provides them; `duration_seconds` is a float |
| `file_status` | see below |

When the file is not on disk, `photo`/`file` holds a sentence explaining why instead of a
path (`NOT_INCLUDED` in `configs.py`), for compatibility with Telegram Desktop's export.
Use `file_status` to decide; don't parse the sentence.

Telegram's video thumbnails are small (320 px on the long side in the test exports). Photo
thumbnails were added later; an export made before them gets them on a `--refresh` run,
since a normal run re-fetches only messages whose file is not downloaded.

### `file_status`

Present on every message that has media.

| `state` | other fields | meaning |
|---|---|---|
| `downloaded` | `size` | the file is on disk at `photo`/`file` |
| `pending` | `size` | wanted, not downloaded yet; the run that listed it was stopped before its downloads finished |
| `disabled` | `setting` | that media kind is switched off, e.g. `MEDIA_EXPORT_PHOTOS` |
| `too_large` | `size`, `limit` | over `--max-file-size` |
| `total_limit` | `size`, `limit` | would have passed `--max-total-size` |
| `failed` | `error` | download failed after retries |

A file found on disk is `downloaded` whatever the current settings say.

### Text with entities

`text` and `caption` are either a plain string, or a list whose last element is the full
string and whose other elements are entities in Telegram's order:

```json
"caption": [
  {"type": "bold", "text": "New release "},
  {"type": "hashtag", "text": "#update"},
  {"type": "bold", "text": "#update"},
  {"type": "text_link", "href": "https://t.me/example", "text": "@Example Channel"},
  "📦 New release #update is out today.\n\n@Example Channel"
]
```

Entity types: `link`, `text_link` (with `href`), `hashtag`, `cashtag`, `mention`,
`text_mention`, `bot_command`, `email`, `phone_number`, `bank_card`, `bold`, `italic`,
`underline`, `strikethrough`, `spoiler`, `code`, `pre` (with `language`, always empty),
`blockquote`, `custom_emoji`, `unknown`.

Entities also carry `offset` and `length`, in UTF-16 code units as Telegram counts them
(the same units as JavaScript string indices). Entities can overlap (above, `#update` is
both a hashtag and bold) and are not always in offset order. Records written before
offsets were added have only the text; a reader then has to search for it, which can
pick the wrong occurrence of a repeated word.

### Albums

Telegram sends an album as several messages sharing a `media_group_id`, which the export
records. Older records without it can only be grouped by adjacent messages with the
same timestamp.

## How runs update the export

A run has two passes.

1. **Listing** walks the history from newest to oldest, within `--since`/`--until` when given,
   and writes a record for every message it has not listed before. A file that is wanted and
   not on disk is recorded as `pending`. Progress is saved every `CHECKPOINT_SECONDS` (10 by
   default) and when the run stops, so a stopped listing keeps what it listed.
2. **Downloading** goes through the records whose file is wanted (`pending`, `failed`,
   `total_limit`, or `disabled`/`too_large` when the settings now allow it), newest first,
   fetches those messages again by id, and downloads their files. `--max-total-size` is
   applied here, so it keeps the newest files.

The id ranges already listed are kept in `export_state.json`, and later runs skip them: a
stopped listing continues below what it reached, and a finished export lists only messages
posted since. Edits to and deletions of messages already listed are only picked up with
`--refresh`, which lists the whole history again; a stopped `--refresh` starts over.

An export without `export_state.json` is listed again in full on its next run, keeping its files.

### Saving: `export_journal.jsonl`

A checkpoint appends the records added or changed since the last one to
`export_journal.jsonl`, one JSON record per line; a later line for the same `id` replaces an
earlier one. `result.json` and the viewer are rewritten only when a run ends (finished,
stopped or failed), and the journal is then deleted. If a run is killed, the next run (and
`--viewer-only`) reads `result.json` plus the journal, ignoring a last line cut short.
While a run is going, `result.json` is therefore behind; `export_state.json` has the current
counts.

### `export_state.json`

```json
{
  "listed": [[1, 5148]],
  "run": {
    "status": "complete",
    "stage": "complete",
    "pid": 4242,
    "started": "2026-09-24T04:50:22",
    "updated": "2026-09-24T05:10:02",
    "messages_in_chat": 5148,
    "listed_this_run": 12,
    "messages_exported": 5148,
    "files": {"downloaded": {"count": 1176, "bytes": 4003020}, "pending": {"count": 3, "bytes": 10240}}
  }
}
```

| field | notes |
|---|---|
| `listed` | inclusive message id ranges already listed, merged; a range starting at `1` reaches the beginning of the chat |
| `run.status` | `running`, `stopped` (Ctrl-C), `failed` (an error, including low disk space) or `complete` |
| `run.stage` | `listing`, `downloading` or `complete`: where the run was when it was last saved |
| `run.messages_in_chat` | Telegram's count at the start of the run, or `null` if it could not be read |
| `run.files` | count and bytes of files per `file_status` state |

The file is written after the journal, so it never claims more than `result.json` and the
journal hold together.
Deleting it only makes the next run list the history again.

## Scale reference

A large export used during development: 13,318 messages,
10,543 videos, 2,413 photos, 1 animation; 24 months with messages, the busiest with 1,212;
`result.json` 25 MB indented, about 14 MB as compact JSON (median message about 1 KB,
mostly caption and entities); 2.9 million characters of text and captions.
