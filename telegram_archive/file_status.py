"""file_status: whether a message's file is on disk and, if not, why.

The states are plain dicts in the JSON, as docs/export-format.md describes:
downloaded {size}, pending {size}, disabled {setting}, too_large {size, limit},
total_limit {size, limit}, failed {error}.
"""
from datetime import datetime

FILE_NOT_FOUND = '(File not included. Change data exporting settings to download.)'
# Written in place of the file's path, for compatibility with Telegram Desktop's export.
NOT_INCLUDED = {
    'pending': '(File not downloaded yet. Run the export again to continue.)',
    'disabled': FILE_NOT_FOUND,
    'too_large': '(File exceeds maximum size. Change data exporting settings to download.)',
    'total_limit': '(File not included. Total media size limit reached; run again with a larger --max-total-size.)',
    'failed': '(File not included. Download failed; run again to retry.)',
}
DATE_FORMAT = '%Y-%m-%dT%H:%M:%S'


def downloaded(size: int) -> dict:
    return {'state': 'downloaded', 'size': size}


def pending(size: int) -> dict:
    return {'state': 'pending', 'size': size}


def disabled(setting: str) -> dict:
    return {'state': 'disabled', 'setting': setting}


def too_large(size: int, limit: int) -> dict:
    return {'state': 'too_large', 'size': size, 'limit': limit}


def total_limit(size: int, limit: int) -> dict:
    return {'state': 'total_limit', 'size': size, 'limit': limit}


def failed(error: str | None) -> dict:
    return {'state': 'failed', 'error': error}


def before_download(size: int, enabled: bool, setting: str, max_file_size: int) -> dict | None:
    """The state of a wanted file that is not on disk, or None when it should be downloaded."""
    if not enabled:
        return disabled(setting)
    if max_file_size and size > max_file_size:
        return too_large(size, max_file_size)
    return None


def is_wanted(record: dict, media_enabled: dict, max_file_size: int, since: datetime | None, until: datetime | None) -> bool:
    """Whether the downloading pass should fetch this record's file under the current settings."""
    status = record.get('file_status') or {}
    state = status.get('state')
    if state in ('pending', 'failed', 'total_limit'):
        pass
    elif state == 'disabled':
        if not media_enabled.get(status.get('setting', '').removeprefix('MEDIA_EXPORT_').lower(), False):
            return False
    elif state == 'too_large':
        if max_file_size and (status.get('size') or 0) > max_file_size:
            return False
    else:
        return False
    date = datetime.strptime(record['date'], DATE_FORMAT)
    return (since is None or date >= since) and (until is None or date < until)


def path_key(record: dict) -> str:
    return 'photo' if 'photo' in record else 'file'


def set_status(record: dict, key: str, status: dict, path: str | None = None) -> None:
    """Records the status, and under key ('photo' or 'file') the file's path or the sentence saying why it is not there."""
    record[key] = path if status['state'] == 'downloaded' else NOT_INCLUDED[status['state']]
    record['file_status'] = status


def count_states(records) -> dict:
    states = {}
    for record in records:
        status = record.get('file_status')
        if status:
            entry = states.setdefault(status['state'], {'count': 0, 'bytes': 0})
            entry['count'] += 1
            entry['bytes'] += status.get('size') or 0
    return states


def downloaded_bytes(records) -> int:
    sizes = {r.get('photo') or r.get('file'): r['file_status'].get('size') or 0
             for r in records if r.get('file_status', {}).get('state') == 'downloaded'}
    return sum(sizes.values())
