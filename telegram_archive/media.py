"""Kinds of media, their folders, and file names."""
import mimetypes
import os
import re
from dataclasses import dataclass

from pyrogram.file_id import FileId


@dataclass(frozen=True)
class MediaKind:
    attr: str  # the Kurigram Message attribute
    setting: str  # key in Settings.media, i.e. MEDIA_EXPORT_<setting>
    folder: str
    media_type: str | None  # the record's media_type; photos and documents have none
    fallback_ext: str

    @property
    def path_key(self) -> str:
        return 'photo' if self.folder == 'photos' else 'file'


# In order of precedence: a message's media is the first of these it has.
MEDIA_KINDS = (
    MediaKind('photo', 'photos', 'photos', None, '.jpg'),
    MediaKind('video', 'videos', 'video_files', 'video_file', '.mp4'),
    MediaKind('animation', 'animations', 'video_files', 'animation', '.mp4'),
    MediaKind('sticker', 'stickers', 'stickers', 'sticker', '.webp'),
    MediaKind('video_note', 'video_messages', 'round_video_messages', 'video_message', '.mp4'),
    MediaKind('audio', 'audios', 'files', 'audio_file', '.mp3'),
    MediaKind('voice', 'voice_messages', 'voice_messages', 'voice_message', '.ogg'),
    MediaKind('document', 'documents', 'files', None, ''),
)

# Kurigram attribute -> record field
MEDIA_FIELDS = (('mime_type', 'mime_type'), ('duration', 'duration_seconds'), ('width', 'width'),
                ('height', 'height'), ('performer', 'performer'), ('title', 'title'), ('emoji', 'sticker_emoji'))


def find_media(message) -> tuple[MediaKind, object] | None:
    for kind in MEDIA_KINDS:
        media = getattr(message, kind.attr, None)
        if media is not None:
            return kind, media
    return None


def media_fields(kind: MediaKind, media) -> dict:
    fields = {'media_type': kind.media_type} if kind.media_type else {}
    for source, field in MEDIA_FIELDS:
        value = getattr(media, source, None)
        if value is not None:
            fields[field] = value
    return fields


INVENTED_NAME = re.compile(r'(video|photo|audio|voice|document|animation|sticker)_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}\.\w+')


def media_file_name(message_id: int, media, kind: str, fallback_ext: str = '') -> str:
    # The message id keeps names unique and the same on every run; the rest is made safe for Windows, exFAT and NTFS.
    original = getattr(media, 'file_name', None)
    # Kurigram invents 'video_<time of parsing>.mp4' for unnamed videos; such names differ on every run.
    if original and INVENTED_NAME.fullmatch(original):
        original = None
    if original:
        stem, ext = os.path.splitext(re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', original).strip(' .'))
        return f'{message_id}_{stem[:120]}{ext[:16]}'
    ext = mimetypes.guess_extension(getattr(media, 'mime_type', None) or '') or fallback_ext
    return f'{kind}_{message_id}{ext}'


def photo_size_id(file_id: str, size: str) -> str:
    # Photo thumbnails are the photo's own smaller sizes; 'm' fits within 320 px.
    decoded = FileId.decode(file_id)
    decoded.thumbnail_size = size
    return decoded.encode()
