# Archive format

What an archive holds, as written by this program: format `archive`, version 1. The shapes are
meant to be shared by archivers of other sources (an Instagram archiver is the next planned one);
`docs/glossary.md` defines the terms. Fields marked optional are absent when they do not apply;
they are never `null` or `""`.

## Folder

```
telegram-durov/                     telegram-<username>, or telegram-<account id> for a chat without one
  archive.db                        the archive (SQLite); everything below is described by it
  account/photo.jpg                 the chat's picture, when there is one
  media/2024-04/5148.mp4            files, one folder per month of their item
  media/2024-04/5148.thumb.jpg      a small image for a photo, video or sticker, when Telegram has one
  media/2024-04/5160_report.pdf     a file with a name of its own keeps it behind the item id
  media/2024-04/5170.vcf            a shared contact
  index.html, data/                 the viewer, generated from archive.db; safe to delete
```

The program finds an archive by the account recorded in `archive.db`, not by the folder's name,
so a folder can be renamed and a chat can change its username. A second archive whose name is
taken gets `-2`, `-3`.

File names are `<item id><ext>`, or `<item id>_<own name>` when the file has a name of its own,
limited to characters valid on exFAT, NTFS and Windows. The extension comes from a fixed table of
MIME types. A medium's `path` is decided once and stored; later runs use the stored path and never
compute it again, so a change to the naming rules only affects new files.

## `archive.db`

A SQLite database. Readers should open it without writing; the exporter commits a transaction at
every checkpoint (`CHECKPOINT_SECONDS`, 10 by default) and when a run ends, so a reader always sees
a consistent state, and a run that is killed keeps everything up to its last commit.

| table | columns | holds |
|---|---|---|
| `archive` | `key`, `value` (JSON) | one row each: `format` (`"archive"`), `version` (`1`), `source` (`"telegram"`), `account` (Account), `created`, `generator`, `extra` |
| `items` | `id`, `sort`, `date`, `data` (JSON) | one row per item: the Item without its media |
| `media` | `item_id`, `position`, `state`, `size`, `data` (JSON) | one row per medium, in the item's order: the Media object |
| `runs` | `id`, `data` (JSON) | one row per run, oldest first: the Run object |

`sort` orders items (the Telegram message id); `date`, `state` and `size` repeat fields of the JSON
for queries. The column set may grow; the JSON shapes below are the contract.

To read the items with their media in order:

```sql
SELECT i.data, m.data FROM items i LEFT JOIN media m ON m.item_id = i.id ORDER BY i.sort, m.position;
```

## Shapes

Written here as TypeScript types. An Instant is ISO 8601 with the UTC offset of the exporting
machine at that moment, `"2024-04-16T16:25:48+03:00"`. Its first ten characters are the day, and
its first seven the month, as the exporting machine saw them.

### Account

```ts
interface Account {
  id: string;           // "user42", "channel1234" (channels and supergroups), "chat555" (basic groups)
  kind: "channel" | "group" | "private" | "bot" | "saved";
  public: boolean;      // has a username
  name: string;         // title, or first and last name; never empty
  username?: string;
  url?: string;         // "https://t.me/durov"
  description?: string; // channel description or user bio
  photo?: string;       // "account/photo.jpg"
  counts?: { items?: number };  // messages in the chat when last read
}
```

### Item

```ts
interface Item {
  id: string;           // the message id, "5148"
  date: Instant;
  edited?: Instant;
  author?: Author;      // absent on a channel's own posts
  text?: Text;          // the message text or media caption
  media?: Media[];      // at most one for Telegram
  group?: string;       // items of one album share it
  reply_to?: string;    // id of the replied message; it may not be in the archive
  forward?: { from: Author; date?: Instant };
  counts?: { views?: number; forwards?: number };  // when the item was last listed
  location?: { latitude: number; longitude: number; name?: string; address?: string };
  url?: string;         // "https://t.me/durov/5148", for public channels and groups
  extra?: { telegram: { service?: string; signature?: string } };
}

interface Author { id?: string; name: string; username?: string }  // no id for a forward from a hidden account

interface Text { plain: string; entities?: Entity[] }

interface Entity {
  type: "bold" | "italic" | "underline" | "strikethrough" | "spoiler" | "code" | "pre" | "blockquote"
      | "link" | "text_link" | "mention" | "text_mention" | "hashtag" | "cashtag" | "bot_command"
      | "email" | "phone_number" | "bank_card" | "custom_emoji" | "unknown";
  offset: number;       // in UTF-16 code units, as Telegram and JavaScript count them
  length: number;
  url?: string;         // text_link
  language?: string;    // pre
  user_id?: string;     // text_mention
}
```

Entities can overlap and are kept in Telegram's order, which is not always by offset.

`extra.telegram.service` names a service message (`pinned_message`, `channel_chat_created`, …, from
Kurigram's `MessageServiceType` in lower case); such items usually have no text or media.
`extra.telegram.signature` is the author's name as a channel shows it under a signed post.

An item with no text, no media, no location and no `service` holds content this program does not
record (a poll, for example).

### Media

```ts
interface Media {
  kind: "photo" | "video" | "animation" | "sticker" | "round_video" | "audio" | "voice" | "document" | "contact";
  path?: string;        // where the file is, or will be, relative to the archive folder
  state: "downloaded" | "pending" | "disabled" | "too_large" | "total_limit" | "failed" | "unavailable";
  setting?: string;     // disabled: the setting that switched the kind off, "MEDIA_EXPORT_PHOTOS"
  limit?: number;       // too_large, total_limit: the limit in bytes
  error?: string;       // failed, unavailable
  size?: number;        // bytes, from Telegram; from the disk when Telegram gave none
  mime?: string;
  name?: string;        // the file's own name, when it has one
  width?: number; height?: number;
  duration?: number;    // seconds, may be fractional
  thumbnail?: string;   // path of a small image, present only when that file is on disk
  title?: string; performer?: string;  // audio
  emoji?: string;       // sticker
  animated?: "tgs" | "webm";           // animated and video stickers
  contact?: { phone: string; first_name: string; last_name?: string };  // kind "contact"; its file is a vCard
}
```

| `state` | meaning | fetched by a later run |
|---|---|---|
| `downloaded` | the file is on disk at `path` | no |
| `pending` | wanted, not downloaded yet (the run stopped before its downloads finished) | yes |
| `disabled` | that kind is switched off in `MEDIA_EXPORT_*` | once it is switched on |
| `too_large` | over `--max-file-size` | once the limit allows it |
| `total_limit` | would have taken the run's downloads past `--max-total-size` | yes, within the next run's limit |
| `failed` | the download failed after its retries | yes |
| `unavailable` | the message was gone when its file was to be downloaded | no |

A file found on disk at `path` is `downloaded`, whatever the settings say.

### Run

```ts
interface Run {
  status: "running" | "stopped" | "failed" | "complete";
  stage: "listing" | "downloading" | "complete";   // where the run was when it last saved
  pid: number;
  started: Instant;
  updated?: Instant;    // written at every checkpoint
  options?: { since: string | null; until: string | null; max_file_size: number; max_total_size: number; refresh: boolean };
  listed?: number;      // items listed by this run
  items_at_source?: number | null;  // Telegram's count of messages at the start of the run
  error?: string;       // status "failed"
}
```

A run that still says `running` while its `pid` is gone and its `updated` is old was killed. File
counts are not stored; count the `media` table by `state`.

### `extra` of the archive

`{"telegram": {"listed": [[1, 5148]]}}`: the inclusive message id ranges already listed, merged. A
range starting at `1` reaches the beginning of the chat.

## How runs update the archive

A run has two passes.

1. **Listing** walks the history from newest to oldest, within `--since`/`--until` when given, and
   writes an item for every message it has not listed before. A file that is wanted and not on disk
   is `pending`. The listed ranges are saved with the items, so a stopped listing continues below
   what it reached, and a finished archive lists only messages posted since. `--refresh` lists the
   whole history again, which is how edits and deletions of older messages are picked up.
2. **Downloading** goes through the media that are not on disk and are wanted under the current
   settings, newest first, fetches their messages again by id (100 at a time, again when the file
   references are 30 minutes old), and downloads the files. `--max-total-size` is applied here, so
   it keeps the newest files.

Items are never removed: messages deleted on Telegram, or outside a later run's date range, stay.

## Versioning

`format` and `version` in the `archive` table (and in the viewer's `data/index.js`) say what the
archive is. Adding an optional field keeps the version; readers ignore fields they do not know.
Renaming, removing or changing the meaning of a field raises the version; the release that does it
comes with a conversion script, and the program refuses an archive of another version with a
sentence saying so.

## The viewer's files

`index.html` loads its data through `<script>` tags, so it works from `file://` with no server:

| file | holds |
|---|---|
| `data/index.js` | `archiveIndex({format, version, source, account, updated, total, months})`; each month has its counts by kind (`photo`, `video`, `other`, `text`), `missing`, and its `chunks` |
| `data/<YYYY-MM>.js` | `archiveChunk(name, items)`: the month's items with their media, as above. A month over 2,000 items is split into `<YYYY-MM>.1.js`, `.2.js`, …, never inside an album |
| `data/search/<YYYY-MM>.js` | `archiveSearch(month, rows)`: `[id, "YYYY-MM-DDTHH:MM", text]` per item, the text being the item's text and the forward's name |
