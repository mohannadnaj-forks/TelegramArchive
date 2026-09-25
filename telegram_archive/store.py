"""An archive on disk: archive.db (SQLite) in the archive's folder.

docs/export-format.md describes the format. Changes are saved in a transaction that commit() ends,
so a run that is killed keeps everything up to its last commit.
"""
import glob
import json
import os
import re
import sqlite3
from datetime import datetime

ARCHIVE_FILE = 'archive.db'
FORMAT = 'archive'
VERSION = 1

SCHEMA = '''
CREATE TABLE IF NOT EXISTS archive (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, sort INTEGER NOT NULL, date TEXT NOT NULL, data TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS items_sort ON items (sort);
CREATE TABLE IF NOT EXISTS media (
    item_id TEXT NOT NULL, position INTEGER NOT NULL, state TEXT NOT NULL, size INTEGER, data TEXT NOT NULL,
    PRIMARY KEY (item_id, position));
CREATE INDEX IF NOT EXISTS media_state ON media (state);
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
'''


class ArchiveVersionError(Exception):
    pass


def find_export_dir(output: str, username: str, resume: bool) -> str:
    """The newest ChatExport_<username>_<date> folder under output, or a new one dated today."""
    pattern = os.path.join(glob.escape(output), f'ChatExport_{glob.escape(username)}_*')
    own = re.compile(rf'ChatExport_{re.escape(username)}_\d{{4}}-\d{{2}}-\d{{2}}')
    existing = sorted(p for p in glob.glob(pattern) if own.fullmatch(os.path.basename(p))) if resume else []
    today = datetime.now().strftime("%Y-%m-%d")
    return existing[-1] if existing else os.path.join(output, f'ChatExport_{username}_{today}')


def is_export_dir(path: str) -> bool:
    return os.path.isfile(os.path.join(path, ARCHIVE_FILE))


def dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


class Archive:
    def __init__(self, path: str) -> None:
        """Opens the archive in the folder at path, creating both when they do not exist."""
        self.path = path
        os.makedirs(path, exist_ok=True)
        self.db = sqlite3.connect(os.path.join(path, ARCHIVE_FILE))
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.db.executescript(SCHEMA)
        version = self.get('version')
        if version is None:
            self.set('format', FORMAT)
            self.set('version', VERSION)
            self.db.commit()
        elif self.get('format') != FORMAT or version != VERSION:
            found = f"{os.path.join(path, ARCHIVE_FILE)} is format {self.get('format')!r} version {version}"
            self.db.close()
            raise ArchiveVersionError(f"{found}; this program reads version {VERSION}")

    def close(self) -> None:
        self.db.close()

    def commit(self) -> None:
        self.db.commit()

    def get(self, key: str, default=None):
        row = self.db.execute('SELECT value FROM archive WHERE key = ?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        self.db.execute('INSERT OR REPLACE INTO archive (key, value) VALUES (?, ?)', (key, dumps(value)))

    def put_item(self, item: dict, sort: int) -> None:
        """Adds or replaces the item and its media; sort orders items (Telegram: the message id)."""
        media = item.get('media') or []
        data = {k: v for k, v in item.items() if k != 'media'}
        self.db.execute('INSERT OR REPLACE INTO items (id, sort, date, data) VALUES (?, ?, ?, ?)',
                        (item['id'], sort, item['date'], dumps(data)))
        self.db.execute('DELETE FROM media WHERE item_id = ?', (item['id'],))
        for position, medium in enumerate(media):
            self.set_medium(item['id'], position, medium)

    def set_medium(self, item_id: str, position: int, medium: dict) -> None:
        self.db.execute('INSERT OR REPLACE INTO media (item_id, position, state, size, data) VALUES (?, ?, ?, ?, ?)',
                        (item_id, position, medium['state'], medium.get('size'), dumps(medium)))

    def media_of(self, item_id: str) -> list:
        rows = self.db.execute('SELECT data FROM media WHERE item_id = ? ORDER BY position', (item_id,))
        return [json.loads(data) for (data,) in rows]

    def item(self, item_id: str) -> dict | None:
        row = self.db.execute('SELECT data FROM items WHERE id = ?', (item_id,)).fetchone()
        if row is None:
            return None
        item = json.loads(row[0])
        media = self.media_of(item_id)
        return {**item, 'media': media} if media else item

    def items(self, newest_first: bool = False):
        """Every item with its media, in sort order."""
        order = 'DESC' if newest_first else 'ASC'
        rows = self.db.execute(f'SELECT i.id, i.data, m.data FROM items i LEFT JOIN media m ON m.item_id = i.id '
                               f'ORDER BY i.sort {order}, m.position')
        current, current_id = None, None
        for item_id, data, medium in rows:
            if item_id != current_id:
                if current is not None:
                    yield current
                current, current_id = json.loads(data), item_id
            if medium is not None:
                current.setdefault('media', []).append(json.loads(medium))
        if current is not None:
            yield current

    def media_not_downloaded(self) -> list:
        """(item id, position, item date, medium) for every file not on disk, newest item first."""
        rows = self.db.execute("SELECT m.item_id, m.position, i.date, m.data FROM media m JOIN items i ON i.id = m.item_id "
                               "WHERE m.state != 'downloaded' ORDER BY i.sort DESC, m.position")
        for item_id, position, date, data in rows:
            yield item_id, position, date, json.loads(data)

    def count(self) -> int:
        return self.db.execute('SELECT COUNT(*) FROM items').fetchone()[0]

    def file_states(self) -> dict:
        rows = self.db.execute('SELECT state, COUNT(*), COALESCE(SUM(size), 0) FROM media GROUP BY state')
        return {state: {'count': count, 'bytes': size} for state, count, size in rows}

    def downloaded_bytes(self) -> int:
        return self.file_states().get('downloaded', {}).get('bytes', 0)

    def start_run(self, run: dict) -> int:
        return self.db.execute('INSERT INTO runs (data) VALUES (?)', (dumps(run),)).lastrowid

    def update_run(self, run_id: int, run: dict) -> None:
        self.db.execute('UPDATE runs SET data = ? WHERE id = ?', (dumps(run), run_id))

    def runs(self) -> list:
        return [json.loads(data) for (data,) in self.db.execute('SELECT data FROM runs ORDER BY id')]
