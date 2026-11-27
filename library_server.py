"""Read-only DesignSpark library. python library_server.py [--root PATH]"""
import argparse
import hashlib
import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

PROJECT = Path(__file__).resolve().parent
WEB = PROJECT / 'library_web'
IMAGE_TYPES = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.avif'}


class Library:
    def __init__(self, roots):
        self.roots = roots
        self.items = {}
        self.warnings = []
        self.lock = threading.RLock()

    def scan(self):
        items, warnings, folders = {}, [], {}
        for root in self.roots:
            if not root.is_dir():
                warnings.append(f'目录不存在：{root}')
                continue
            for name in ('metadata.json', '卡片.md'):
                for path in root.rglob(name):
                    if path.resolve().is_relative_to(root):
                        folders.setdefault(path.parent, {})[name] = path
        for folder, files in sorted(folders.items()):
            try:
                body = ''
                if '卡片.md' in files:
                    body = files['卡片.md'].read_text(encoding='utf-8-sig')
                    header, sep, body = body.partition('\n---\n')
                    if not sep or not header.startswith('---'):
                        raise ValueError('卡片缺少 JSON frontmatter')
                    data = json.loads(header[3:].strip())
                else:
                    data = json.loads(files['metadata.json'].read_text(encoding='utf-8-sig'))
                    if (folder / 'README.md').is_file():
                        body = (folder / 'README.md').read_text(encoding='utf-8-sig')
                source = 'IF' if 'ifdesign.com' in data.get('source_url', '') or str(data.get('source', '')).upper() == 'IF' or 'IF' in folder.parts else 'RedDot'
                year = str(data.get('award_year') or data.get('year') or data.get('requested_year') or next((p for p in folder.parts if p.isdigit() and len(p) == 4), '未知年份'))
                category = data.get('category_parent') if source == 'RedDot' else data.get('category')
                category = category or data.get('official_category') or data.get('category_detail') or '待分类'
                subcategory = data.get('category_detail') if source == 'RedDot' else None
                award = str(data.get('award_level') or data.get('award') or data.get('selection_award') or '未标注')
                key = hashlib.sha256(str(folder.resolve()).encode()).hexdigest()[:20]
                pictures = []
                if (folder / 'image').is_dir():
                    for pic in sorted((folder / 'image').iterdir()):
                        if pic.suffix.lower() in IMAGE_TYPES and pic.is_file() and pic.resolve().is_relative_to(folder.resolve()):
                            pictures.append('/media/' + key + '/' + pic.name)
                item = dict(id=key, title=str(data.get('title') or data.get('detail_title') or folder.name), source=source,
                            year=year, category=str(category), subcategory=subcategory, award=award,
                            description=str(data.get('description') or '暂无官方简介'), images=pictures,
                            source_url=data.get('source_url', ''), folder=str(folder), body=body,
                            credits=data.get('credits', []), source_id=data.get('source_id', ''),
                            card_file=str(files.get('卡片.md') or files.get('metadata.json')))
                items[key] = item
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                warnings.append(f'{folder}: {exc}')
        # Merge display duplicates across old archives and fresh test folders.
        # Prefer the new card format, then the copy with more local images.
        canonical = {}
        for item in items.values():
            identity = (item['source'], item['year'], str(item['source_id'] or item['source_url'] or item['id']))
            previous = canonical.get(identity)
            rank = (item['card_file'].endswith('卡片.md'), len(item['images']))
            previous_rank = (previous['card_file'].endswith('卡片.md'), len(previous['images'])) if previous else None
            if previous is None or rank > previous_rank:
                canonical[identity] = item
        items = {item['id']: item for item in canonical.values()}
        with self.lock:
            self.items, self.warnings = items, warnings

    def snapshot(self):
        with self.lock:
            return dict(items=list(self.items.values()), warnings=self.warnings, roots=[str(p) for p in self.roots])


def handler(library):
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, payload, mime, status=200):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.headers.get('Host', '').split(':')[0] not in ('127.0.0.1', 'localhost'):
                return self.send_bytes(b'Forbidden host', 'text/plain', 403)
            url = urlparse(self.path)
            if url.path == '/api/library':
                if parse_qs(url.query).get('refresh') == ['1']:
                    library.scan()
                return self.send_bytes(json.dumps(library.snapshot(), ensure_ascii=False).encode(), 'application/json; charset=utf-8')
            if url.path.startswith('/media/'):
                parts = unquote(url.path).split('/')
                if len(parts) == 4:
                    with library.lock:
                        item = library.items.get(parts[2])
                    if item:
                        base = (Path(item['folder']) / 'image').resolve()
                        image = (base / parts[3]).resolve()
                        if image.parent == base and image.suffix.lower() in IMAGE_TYPES and image.is_file():
                            try:
                                return self.send_bytes(image.read_bytes(), mimetypes.guess_type(image.name)[0] or 'application/octet-stream')
                            except OSError: pass
                return self.send_bytes(b'Image not found', 'text/plain', 404)
            pages = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if url.path in pages:
                path = WEB / pages[url.path]
                return self.send_bytes(path.read_bytes(), (mimetypes.guess_type(path.name)[0] or 'text/plain') + '; charset=utf-8')
            self.send_bytes(b'Not found', 'text/plain', 404)

        def log_message(self, *args): pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', action='append', help='Library root; repeat for multiple folders')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    roots = [Path(p).resolve() for p in args.root] if args.root else [PROJECT / 'data', *sorted(PROJECT.glob('fresh-*'))]
    library = Library(roots)
    library.scan()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(library))
    print(f'DesignSpark: http://127.0.0.1:{args.port}  ({len(library.items)} works)', flush=True)
    print('Read-only. Press Ctrl+C to stop.', flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__': main()
