"""The HTML viewer: index.html (a copy of _index.html) and its data/ folder, written from the archive."""
import itertools
import json
import logging
import os

from .store import FORMAT, VERSION, Archive

logger = logging.getLogger(__name__)

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_index.html')
VIEWER_CHUNK_ITEMS = 2000
VIDEO_KINDS = ('video', 'round_video', 'animation')


def compact_js(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def split_chunks(items: list) -> list:
    # Chunks of VIEWER_CHUNK_ITEMS, each extended to the end of a group it would otherwise cut.
    parts, start = [], 0
    while start < len(items):
        end = start + VIEWER_CHUNK_ITEMS
        while end < len(items) and items[end].get('group') is not None \
                and items[end].get('group') == items[end - 1].get('group'):
            end += 1
        parts.append(items[start:end])
        start = end
    return parts


def search_text(item: dict) -> str:
    forward = (item.get('forward') or {}).get('from', {}).get('name')
    return ' '.join(filter(None, ((item.get('text') or {}).get('plain'), forward)))


def is_missing(item: dict) -> bool:
    return any(m['state'] != 'downloaded' for m in item.get('media') or ())


def month_entry(key: str, items: list, parts: list) -> dict:
    entry = {'key': key, 'count': len(items), 'video': 0, 'photo': 0, 'other': 0, 'text': 0, 'missing': 0, 'chunks': []}
    for item in items:
        media = item.get('media') or []
        kind = media[0]['kind'] if media else None
        entry['photo' if kind == 'photo' else 'video' if kind in VIDEO_KINDS else 'other' if kind else 'text'] += 1
        entry['missing'] += is_missing(item)
    for number, part in enumerate(parts, 1):
        entry['chunks'].append({
            'name': key if len(parts) == 1 else f'{key}.{number}', 'count': len(part),
            'first_id': part[0]['id'], 'last_id': part[-1]['id'],
            'first_date': part[0]['date'], 'last_date': part[-1]['date'],
            'missing': sum(map(is_missing, part)),
        })
    return entry


def viewer_files(archive: Archive):
    """(file name, content) for every file under data/, index.js last; items are read one month at a time."""
    months, search, total = [], [], 0
    for key, group in itertools.groupby(archive.items(), key=lambda item: item['date'][:7]):
        items = list(group)
        parts = split_chunks(items)
        entry = month_entry(key, items, parts)
        months.append(entry)
        total += len(items)
        for chunk, part in zip(entry['chunks'], parts):
            yield f"{chunk['name']}.js", f"archiveChunk({compact_js(chunk['name'])},{compact_js(part)});\n"
        search.extend([item['id'], item['date'][:16], search_text(item)] for item in items)
    yield 'search.js', f'archiveSearch({compact_js(search)});\n'
    runs = archive.runs()
    index = {'format': FORMAT, 'version': VERSION, 'source': archive.get('source', 'telegram'),
             'account': archive.get('account', {}), 'updated': runs[-1].get('updated') if runs else None,
             'total': total, 'months': months}
    yield 'index.js', f'archiveIndex({compact_js(index)});\n'


def generate_index_html(export_path: str, archive: Archive, template_path: str = TEMPLATE) -> None:
    """Writes index.html and its data/ folder for viewing the archive."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_template = f.read()
    except FileNotFoundError:
        logger.error(f"❌ HTML template not found: {template_path}")
        return

    data_dir = os.path.join(export_path, 'data')
    written = set()
    try:
        os.makedirs(data_dir, exist_ok=True)
        for name, content in viewer_files(archive):
            with open(os.path.join(data_dir, f'{name}.tmp'), 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(os.path.join(data_dir, f'{name}.tmp'), os.path.join(data_dir, name))
            written.add(name)
        for name in os.listdir(data_dir):
            if name not in written:
                os.remove(os.path.join(data_dir, name))
    except Exception as e:
        logger.warning(f"⚠️ Failed to generate viewer data: {e}")
        return

    html_file_path = os.path.join(export_path, 'index.html')
    try:
        with open(html_file_path, 'w', encoding='utf-8') as f:
            f.write(html_template)
        logger.info(f"📄 Generated HTML viewer: {html_file_path}")
    except Exception as e:
        logger.warning(f"⚠️ Failed to generate HTML viewer: {e}")
