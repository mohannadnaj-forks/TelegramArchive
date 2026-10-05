"""Kinds of media, their fields, and where their files go."""
import os
import re
from dataclasses import dataclass

from pyrogram.file_id import FileId


@dataclass(frozen=True)
class MediaKind:
    attr: str  # the Kurigram Message attribute
    kind: str  # Media.kind in the archive
    setting: str  # key in Settings.media, i.e. MEDIA_EXPORT_<setting>
    fallback_ext: str


# In order of precedence: a message's media is the first of these it has.
MEDIA_KINDS = (
    MediaKind('photo', 'photo', 'photos', '.jpg'),
    MediaKind('video', 'video', 'videos', '.mp4'),
    MediaKind('animation', 'animation', 'animations', '.mp4'),
    MediaKind('sticker', 'sticker', 'stickers', '.webp'),
    MediaKind('video_note', 'round_video', 'video_messages', '.mp4'),
    MediaKind('audio', 'audio', 'audios', '.mp3'),
    MediaKind('voice', 'voice', 'voice_messages', '.ogg'),
    MediaKind('document', 'document', 'documents', ''),
)

EXTENSIONS = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif', 'image/webp': '.webp', 'image/heic': '.heic',
    'image/bmp': '.bmp', 'image/tiff': '.tif', 'image/svg+xml': '.svg',
    'video/mp4': '.mp4', 'video/quicktime': '.mov', 'video/webm': '.webm', 'video/x-matroska': '.mkv',
    'video/mpeg': '.mpeg', 'video/3gpp': '.3gp', 'video/x-msvideo': '.avi',
    'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a', 'audio/ogg': '.ogg', 'audio/opus': '.opus',
    'audio/aac': '.aac', 'audio/flac': '.flac', 'audio/x-flac': '.flac', 'audio/wav': '.wav', 'audio/x-wav': '.wav',
    'application/x-tgsticker': '.tgs', 'application/pdf': '.pdf', 'application/zip': '.zip',
    'application/x-rar-compressed': '.rar', 'application/vnd.rar': '.rar', 'application/x-7z-compressed': '.7z',
    'application/json': '.json', 'text/plain': '.txt', 'text/csv': '.csv', 'text/html': '.html',
    'application/msword': '.doc', 'application/vnd.ms-excel': '.xls', 'application/vnd.ms-powerpoint': '.ppt',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': '.xlsx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': '.pptx',
    'application/vnd.android.package-archive': '.apk', 'application/epub+zip': '.epub',
}

# Kurigram attribute -> Media field
MEDIA_FIELDS = (('mime_type', 'mime'), ('width', 'width'), ('height', 'height'), ('duration', 'duration'),
                ('title', 'title'), ('performer', 'performer'), ('emoji', 'emoji'))

# Kurigram invents 'video_<time of parsing>.mp4' for unnamed videos; such names differ on every run.
INVENTED_NAME = re.compile(r'(video|photo|audio|voice|document|animation|sticker)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\w+')


def find_media(message) -> tuple[MediaKind, object] | None:
    for kind in MEDIA_KINDS:
        media = getattr(message, kind.attr, None)
        if media is not None:
            return kind, media
    return None


def own_name(media) -> str | None:
    name = getattr(media, 'file_name', None)
    return None if not name or INVENTED_NAME.fullmatch(name) else name


def describe(kind: MediaKind, media) -> dict:
    """The Media fields that come from Telegram, without the path and state."""
    fields = {'kind': kind.kind}
    if getattr(media, 'file_size', None):
        fields['size'] = media.file_size
    for source, field in MEDIA_FIELDS:
        value = getattr(media, source, None)
        if value is not None and value != '':
            fields[field] = value
    name = own_name(media)
    if name:
        fields['name'] = name
    if kind.kind == 'sticker' and (getattr(media, 'is_animated', False) or getattr(media, 'is_video', False)):
        fields['animated'] = 'tgs' if media.is_animated else 'webm'
    return fields


def file_name(item_id: str, media, kind: MediaKind) -> str:
    # The item id keeps names unique; the rest is made safe for Windows, exFAT and NTFS.
    original = own_name(media)
    if original:
        stem, ext = os.path.splitext(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original).strip(' .'))
        return f'{item_id}_{stem[:120]}{ext[:16]}'
    return f'{item_id}{EXTENSIONS.get(getattr(media, "mime_type", None) or "", kind.fallback_ext)}'


def media_path(month: str, name: str) -> str:
    return f'media/{month}/{name}'


def thumbnail_path(month: str, item_id: str) -> str:
    return f'media/{month}/{item_id}.thumb.jpg'


def photo_size_id(file_id: str, size: str) -> str:
    # Photo thumbnails are the photo's own smaller sizes; 'm' fits within 320 px.
    decoded = FileId.decode(file_id)
    decoded.thumbnail_size = size
    return decoded.encode()
