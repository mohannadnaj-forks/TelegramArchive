"""The HTML viewer: index.html (a copy of _index.html) and its data/ folder."""
import json
import logging
import os

from .store import ExportFolder

logger = logging.getLogger(__name__)

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '_index.html')
VIEWER_CHUNK_MESSAGES = 2000
VIDEO_TYPES = ('video_file', 'video_message', 'animation')


def compact_js(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def plain_text(value) -> str:
    if isinstance(value, list):
        return value[-1] if value and isinstance(value[-1], str) else ''
    return value or ''


def viewer_index(chat_data: dict, updated: str | None) -> tuple:
    # The viewer loads data/index.js, then one data/<month>.js file at a time, all through <script> tags
    # so that it works from file:// without a server. Chunks hold the result.json records unchanged.
    months = {}
    for message in chat_data['messages']:
        months.setdefault(message['date'][:7], []).append(message)
    chunks, index_months = {}, []
    for key in sorted(months):
        messages = months[key]
        parts = [messages[i:i + VIEWER_CHUNK_MESSAGES] for i in range(0, len(messages), VIEWER_CHUNK_MESSAGES)]
        entry = {'key': key, 'count': len(messages), 'video': 0, 'photo': 0, 'other': 0, 'text': 0, 'missing': 0, 'chunks': []}
        for message in messages:
            status = message.get('file_status')
            if 'photo' in message:
                entry['photo'] += 1
            elif message.get('media_type') in VIDEO_TYPES:
                entry['video'] += 1
            elif status or 'file' in message:
                entry['other'] += 1
            else:
                entry['text'] += 1
            if status and status.get('state') != 'downloaded':
                entry['missing'] += 1
        for number, part in enumerate(parts, 1):
            name = key if len(parts) == 1 else f'{key}.{number}'
            chunks[name] = part
            entry['chunks'].append({
                'name': name, 'count': len(part),
                'first_id': part[0]['id'], 'last_id': part[-1]['id'],
                'first_date': part[0]['date'], 'last_date': part[-1]['date'],
                'missing': sum(1 for m in part if m.get('file_status', {}).get('state') not in (None, 'downloaded')),
            })
        index_months.append(entry)
    index = {
        'source': 'telegram',
        'chat': {k: v for k, v in chat_data.items() if k != 'messages'},
        'updated': updated,
        'total': len(chat_data['messages']),
        'months': index_months,
    }
    search = [
        [m['id'], m['date'][:16], ' '.join(filter(None, (plain_text(m.get('text')), plain_text(m.get('caption')), m.get('forwarded_from'))))]
        for m in chat_data['messages']
    ]
    return index, chunks, search


def generate_index_html(export_path: str, chat_data: dict, template_path: str = TEMPLATE) -> None:
    """Writes index.html and its data/ folder for viewing the exported chat."""
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_template = f.read()
    except FileNotFoundError:
        logger.error(f"❌ HTML template not found: {template_path}")
        return

    index, chunks, search = viewer_index(chat_data, ExportFolder(export_path).updated())
    data_dir = os.path.join(export_path, 'data')
    files = {f'{name}.js': f'archiveChunk({compact_js(name)},{compact_js(part)});\n' for name, part in chunks.items()}
    files['search.js'] = f'archiveSearch({compact_js(search)});\n'
    files['index.js'] = f'archiveIndex({compact_js(index)});\n'
    try:
        os.makedirs(data_dir, exist_ok=True)
        for name, content in files.items():
            with open(os.path.join(data_dir, f'{name}.tmp'), 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(os.path.join(data_dir, f'{name}.tmp'), os.path.join(data_dir, name))
        for name in os.listdir(data_dir):
            if name not in files:
                os.remove(os.path.join(data_dir, name))
        if os.path.exists(os.path.join(export_path, 'data.js')):
            os.remove(os.path.join(export_path, 'data.js'))
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
