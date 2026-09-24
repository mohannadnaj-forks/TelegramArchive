"""An export folder on disk: result.json or its parts, export_journal.jsonl and export_state.json.

docs/export-format.md describes the format; tests/test_legacy_exports.py checks that exports
written by earlier versions still read.
"""
import glob
import json
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger(__name__)

RESULT_FILE = 'result.json'
JOURNAL_FILE = 'export_journal.jsonl'
STATE_FILE = 'export_state.json'


def find_export_dir(output: str, username: str, resume: bool) -> str:
    """The newest ChatExport_<username>_<date> folder under output, or a new one dated today."""
    pattern = os.path.join(glob.escape(output), f'ChatExport_{glob.escape(username)}_*')
    own = re.compile(rf'ChatExport_{re.escape(username)}_\d{{4}}-\d{{2}}-\d{{2}}')
    existing = sorted(p for p in glob.glob(pattern) if own.fullmatch(os.path.basename(p))) if resume else []
    today = datetime.now().strftime("%Y-%m-%d")
    return existing[-1] if existing else os.path.join(output, f'ChatExport_{username}_{today}')


def is_export_dir(path: str) -> bool:
    return os.path.isdir(path) and any(os.path.exists(os.path.join(path, name))
                                       for name in (RESULT_FILE, 'result_part1.json', JOURNAL_FILE))


class ExportFolder:
    def __init__(self, path: str) -> None:
        self.path = path
        self.result_path = os.path.join(path, RESULT_FILE)
        self.journal_path = os.path.join(path, JOURNAL_FILE)
        self.state_path = os.path.join(path, STATE_FILE)

    def part_path(self, number: int) -> str:
        return os.path.join(self.path, f'result_part{number}.json')

    def create(self) -> None:
        os.makedirs(self.path, exist_ok=True)

    def load(self) -> dict:
        """The export (chat fields and 'messages'), with the records saved to the journal since it was last written."""
        data = self.load_result()
        journal = self.read_journal()
        if journal:
            messages = {m['id']: m for m in data.get('messages', [])}
            messages.update(journal)
            data['messages'] = list(messages.values())
            logger.info(f"📂 Recovered {len(journal):,} messages saved since result.json was last written")
        return data

    def load_result(self) -> dict:
        # When both result.json and parts are present, the newer of the two is the export.
        part1 = self.part_path(1)
        parts_newer = os.path.exists(part1) and os.path.exists(self.result_path) \
            and os.path.getmtime(part1) > os.path.getmtime(self.result_path)
        if os.path.exists(self.result_path) and not parts_newer:
            try:
                with open(self.result_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"📂 Found existing export with {len(data.get('messages', []))} messages")
                    return data
            except Exception as e:
                logger.warning(f"⚠️ Failed to load existing export: {e}")

        part_num = 1
        all_messages = []
        chat_data = {}
        while os.path.exists(self.part_path(part_num)):
            try:
                with open(self.part_path(part_num), 'r', encoding='utf-8') as f:
                    part_data = json.load(f)
                    if part_num == 1:
                        chat_data = part_data.copy()
                        chat_data['messages'] = []
                    all_messages.extend(part_data.get('messages', []))
                part_num += 1
            except Exception as e:
                logger.warning(f"⚠️ Failed to load split file {self.part_path(part_num)}: {e}")
                break
        if all_messages:
            chat_data['messages'] = all_messages
            logger.info(f"📂 Found existing split export with {len(all_messages)} messages from {part_num - 1} parts")
            return chat_data
        return {}

    def read_journal(self) -> dict:
        # One message record per line; later lines replace earlier ones. A line cut short by a crash is skipped.
        records = {}
        try:
            with open(self.journal_path, encoding='utf-8') as f:
                for line in f:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    records[record['id']] = record
        except FileNotFoundError:
            pass
        return records

    def append_journal(self, records: list) -> None:
        try:
            with open(self.journal_path, 'rb') as f:
                f.seek(-1, os.SEEK_END)
                cut_short = f.read(1) != b'\n'
        except OSError:
            cut_short = False
        with open(self.journal_path, 'a', encoding='utf-8') as f:
            if cut_short:
                f.write('\n')
            for record in records:
                f.write(json.dumps(record, default=str) + '\n')
            f.flush()
            os.fsync(f.fileno())

    def remove_journal(self) -> None:
        if os.path.exists(self.journal_path):
            os.remove(self.journal_path)

    def write_result(self, chat_data: dict, page_size: int | None) -> None:
        """Writes result.json, or parts of about page_size bytes, and removes the other form."""
        chat_data['messages'].sort(key=lambda m: m['id'])
        if page_size:
            self.write_parts(chat_data, page_size)
            if os.path.exists(self.result_path):
                os.remove(self.result_path)
            return
        write_json(self.result_path, chat_data, indent=4)
        self.remove_parts_from(1)

    def write_parts(self, data: dict, page_size: int) -> None:
        # Every part carries the chat's fields.
        chat = {key: value for key, value in data.items() if key != 'messages'}
        base_size = len(json.dumps({**chat, 'messages': []}, indent=4, default=str).encode('utf-8'))
        parts, current, size = [], [], base_size
        for msg in data['messages']:
            text = json.dumps(msg, indent=4, default=str)
            msg_size = len(text.encode('utf-8')) + 8 * (text.count('\n') + 1) + 2
            if current and size + msg_size > page_size:
                parts.append(current)
                current, size = [], base_size
            current.append(msg)
            size += msg_size
        parts.append(current)
        for number, messages in enumerate(parts, 1):
            write_json(self.part_path(number), {**chat, 'messages': messages}, indent=4)
        self.remove_parts_from(len(parts) + 1)

    def remove_parts_from(self, number: int) -> None:
        while os.path.exists(self.part_path(number)):
            os.remove(self.part_path(number))
            number += 1

    def load_state(self) -> dict:
        try:
            with open(self.state_path, encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}
        except Exception as e:
            logger.warning(f"⚠️ Failed to read {STATE_FILE}; the history is listed again: {e}")
            return {}

    def write_state(self, state: dict) -> None:
        write_json(self.state_path, state, indent=2, default=None)

    def updated(self) -> str | None:
        """When result.json (or its first part) was last written."""
        path = self.result_path if os.path.exists(self.result_path) else self.part_path(1)
        if not os.path.exists(path):
            return None
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%dT%H:%M:%S')


def write_json(path: str, data, indent: int, default=str) -> None:
    with open(f'{path}.tmp', 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent, default=default)
    os.replace(f'{path}.tmp', path)
