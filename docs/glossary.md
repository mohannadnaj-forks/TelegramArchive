# Glossary

The words used for an archive's parts, in the code and in `docs/export-format.md`. They are meant to
hold for any source, not only Telegram.

| term | meaning |
|---|---|
| **source** | the service archived from: `telegram` |
| **account** | what one archive is of: a Telegram chat (a channel, a group, a private chat, a chat with a bot, or Saved Messages) |
| **archive** | one folder holding everything archived from one account: `archive.db`, the media, the viewer |
| **item** | one unit of content as the source defines it: a Telegram message |
| **group** | items the source shows as one, such as a Telegram album; the viewer shows a group as one **post** |
| **medium** (plural **media**) | a file attached to an item (a photo, video, voice message, document, contact card, …) with its **state**: whether it is on disk and, if not, why |
| **author** | who an item is from, when that is not the account itself: a user, or a chat posting as itself |
| **text** | an item's text or caption, with **entities** (formatting, links, mentions) at UTF-16 offsets |
| **run** | one invocation of the exporter on one archive: a **listing** pass, then a **downloading** pass |
| **listing** | reading items from the source and recording them; wanted media found are `pending` |
| **downloading** | fetching the files of media that are wanted and not on disk |
| **coverage** | the part of the account's history already listed (for Telegram, message id ranges) |
