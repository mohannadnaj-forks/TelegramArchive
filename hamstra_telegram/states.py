"""A medium's state: whether its file is on disk and, if not, why.

The state's fields sit in the Media object itself, as docs/export-format.md describes:
downloaded, pending, disabled {setting}, too_large {limit}, total_limit {limit}, failed {error},
unavailable {error}.
"""
from datetime import datetime, timedelta

STATE_FIELDS = ('state', 'setting', 'limit', 'error')
GIVE_UP_AFTER_RUNS = 3
RETRY_GIVEN_UP_AFTER = timedelta(days=7)


def downloaded() -> dict:
    return {'state': 'downloaded'}


def pending() -> dict:
    return {'state': 'pending'}


def disabled(setting: str) -> dict:
    return {'state': 'disabled', 'setting': setting}


def too_large(limit: int) -> dict:
    return {'state': 'too_large', 'limit': limit}


def total_limit(limit: int) -> dict:
    return {'state': 'total_limit', 'limit': limit}


def failed(error: str | None) -> dict:
    return {'state': 'failed', 'error': error or 'unknown error'}


def unavailable(error: str) -> dict:
    return {'state': 'unavailable', 'error': error}


def set_state(medium: dict, state: dict) -> None:
    for field in STATE_FIELDS:
        medium.pop(field, None)
    medium.update(state)


def before_download(size: int, enabled: bool, setting: str, max_file_size: int) -> dict | None:
    """The state of a file that is not on disk, or None when it is wanted."""
    if not enabled:
        return disabled(setting)
    if max_file_size and size > max_file_size:
        return too_large(max_file_size)
    return None


def given_up(medium: dict, now: datetime) -> bool:
    """A file whose download failed in GIVE_UP_AFTER_RUNS runs or more, the last time less than RETRY_GIVEN_UP_AFTER ago."""
    if medium['state'] != 'failed' or medium.get('failures', 0) < GIVE_UP_AFTER_RUNS:
        return False
    failed_at = medium.get('failed_at')
    return failed_at is not None and now - datetime.fromisoformat(failed_at) < RETRY_GIVEN_UP_AFTER


def is_wanted(medium: dict, date: str, media_enabled: dict, max_file_size: int,
              since: datetime | None, until: datetime | None, retry_failed: bool = False,
              now: datetime | None = None) -> bool:
    """Whether the downloading pass should fetch this file under the current settings; date is its item's."""
    state = medium['state']
    if medium['kind'] == 'contact' or state in ('downloaded', 'unavailable'):
        return False
    if not retry_failed and now is not None and given_up(medium, now):
        return False
    if state == 'disabled' and not media_enabled.get(medium['setting'].removeprefix('MEDIA_EXPORT_').lower(), False):
        return False
    if state == 'too_large' and max_file_size and (medium.get('size') or 0) > max_file_size:
        return False
    moment = datetime.fromisoformat(date)
    return (since is None or moment >= since) and (until is None or moment < until)
