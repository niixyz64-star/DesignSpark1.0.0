"""Red Dot Design Concept archive. Run --help for commands."""
import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

BASE = 'https://www.red-dot.org'
ROOT = Path(__file__).resolve().parent


def safe_name(value):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', value).strip().rstrip('. ')
    if not name:
        return 'Uncategorized'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Client:
    def __init__(self, delay):
        self.delay = max(1, delay)
        self.last = 0
        self.session = requests.Session()
        self.agent = 'RedDotResearchArchive/0.1'
        self.session.headers['User-Agent'] = self.agent
        r = self.request(BASE + '/robots.txt')
        r.raise_for_status()
        self.robots = RobotFileParser()
        self.robots.parse(r.text.splitlines())

    def get(self, url, params=None):
        prepared = requests.Request('GET', url, params=params).prepare().url
        if not self.robots.can_fetch(self.agent, prepared):
            raise RuntimeError('robots.txt disallows: ' + prepared)
        return self.request(url, params)

    def request(self, url, params=None):
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            try:
                self.last = time.monotonic()
                r = self.session.get(url, params=params, timeout=40)
                if r.status_code in (403, 429):
                    raise RuntimeError(f'Access restricted ({r.status_code}); stopped: {r.url}')
                r.raise_for_status()
                return r
            except (requests.ConnectionError, requests.Timeout):
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))

    def search(self, year, category, page=1):
        return self.get(BASE + '/search/search.json', {
            'solr[filter][]': [f'year:{year}', f'meta_categories:{category}'],
            'solr[page]': page,
        }).json()


def parse_detail(html):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.find('h1')
    description = soup.find('meta', attrs={'name': 'description'})
    credits = []
    for p in soup.find_all('p'):
        parent = p.parent
        value = parent.find('div', recursive=False)
        if value and 'font-light' in value.get('class', []):
            item = {'role': p.get_text(' ', strip=True), 'value': value.get_text(' ', strip=True)}
            if item not in credits:
                credits.append(item)
    images = []
    slider = soup.select_one('[data-project-slider-viewport]')
    for img in (slider or soup).find_all('img', src=True):
        url = urljoin(BASE, img['src'])
        if (slider is not None or '/projects/' in url) and url not in images:
            images.append(url)
    return {
        'detail_title': title.get_text(' ', strip=True) if title else None,
        'description': description.get('content') if description else None,
        'credits': credits,
        'image_urls': images,
    }


def render(record):
    credits = '\n'.join(f"- {c['role']}: {c['value']}" for c in record['credits']) or '未提供'
    images = '\n'.join(f"![图片 {i}]({item['local_path']})" if item.get('local_path') else f"- [图片 {i}]({item['url']})：{item['status']}" for i, item in enumerate(record.get('images', []), 1)) or '未提供'
    template = (ROOT / 'templates' / 'work.md').read_text(encoding='utf-8')
    values = dict(record, credits_text=credits, images_text=images)
    for key, value in values.items():
        template = template.replace('{{' + key + '}}', str(value if value is not None else '未提供'))
    return template


STAGE_PATHS = {'/12/177/', '/12/173/', '/12/175/'}


def run(args):
    years = list(dict.fromkeys(args.years or [args.year]))
    client = Client(args.delay)
    failures = []
    for year in years:
        child = argparse.Namespace(**vars(args))
        child.year = year
        print(f'Year: {year}', flush=True)
        try:
            run_year(child, client)
        except (SystemExit, requests.RequestException) as exc:
            failures.append(f'{year}: {exc}')
            print(f'Year failed: {year}: {exc}', flush=True)
    if failures:
        raise SystemExit('\n'.join(failures))


def run_year(args, client):
    if args.category != '/12/':
        return run_category(args, client)
    payload = client.search(args.year, '/12/')
    facet = next(f for f in payload['facets'] if f['field'] == 'meta_categories')
    concept = next(c for c in facet['children'] if c['value'] == '/12/')
    out = Path(args.output).resolve() / str(args.year) / 'Concept Design'
    save(out / 'categories.json', concept)
    # 官网将三个项目阶段与主题分类并列，阶段不建立大类目录。
    categories = [c for c in concept.get('children', []) if c['value'] not in STAGE_PATHS]
    save(out / 'category_manifest.json', {'year': args.year, 'count': len(categories),
                                         'categories': categories,
                                         'stage_filters': [c for c in concept.get('children', []) if c['value'] in STAGE_PATHS]})
    print(f'Design Concept: {len(categories)} topic categories')
    failures = []
    for category in categories:
        print(f"Category: {category['label']} ({category['count']})")
        (out / safe_name(category['label'])).mkdir(parents=True, exist_ok=True)
        if args.command == 'categories' or not category['count']:
            continue
        child = argparse.Namespace(**vars(args))
        child.category = category['value']
        try:
            run_category(child, client)
        except (SystemExit, requests.RequestException) as exc:
            failures.append({'category': category['label'], 'error': str(exc)})
            save(out / 'category_errors.json', failures)
    save(out / 'category_errors.json', failures)
    # 子分类查询产生的动态 facets 不覆盖完整目录快照。
    save(out / 'categories.json', concept)
    if failures:
        raise SystemExit(f'{len(failures)} categories have failures; see category_errors.json')


def run_category(args, client=None):
    out = Path(args.output).resolve() / str(args.year) / 'Concept Design'
    client = client or Client(args.delay)
    initial = client.search(args.year, args.category)
    categories = next(f for f in initial['facets'] if f['field'] == 'meta_categories')
    concept = next(c for c in categories['children'] if c['value'] == '/12/')
    parent_category = next((c['label'] for c in concept.get('children', [])
                            if c['value'] == args.category), concept['label'])
    archive_root = out / safe_name(parent_category)
    save(out / 'categories.json', concept)
    if args.command == 'categories':
        for c in concept.get('children', []):
            print(f"{c['value']:14} {c['count']:4} {c['label']}")
        return
    docs = {}
    page = 1
    while True:
        payload = initial if page == 1 else client.search(args.year, args.category, page)
        batch = payload['result']['docs']
        before = len(docs)
        for doc in batch:
            data = doc.get('data', {})
            if str(data.get('year')) != str(args.year) or data.get('competition') != 'Design Concept':
                raise RuntimeError('Unexpected year or competition in search response')
            docs[urljoin(BASE, doc['url'])] = doc
        print(f"Page {page}: {len(batch)} results; unique {len(docs)}/{payload['meta']['count']}")
        if len(docs) >= payload['meta']['count']:
            break
        if len(docs) == before:
            raise RuntimeError('Pagination stalled; refusing to report a complete archive')
        page += 1
    save(out / ('discovery-' + args.category.strip('/').replace('/', '-') + '.json'), list(docs.values()))
    # 官网 /12/13/ 为建筑、室内与城市设计大类；细分类从列表字段精确匹配。
    if args.detail_category:
        docs = {url: doc for url, doc in docs.items()
                if doc.get('data', {}).get('category', '').split('|', 1)[0].strip().casefold()
                == args.detail_category.strip().casefold()}
        save(out / ('discovery-detail-' + re.sub(r'[^a-z0-9]+', '-', args.detail_category.lower()).strip('-') + '.json'), list(docs.values()))
        print(f'Detail category: {args.detail_category}; matched {len(docs)} works')
        if not docs:
            raise RuntimeError('No exact detail-category matches; check the category label')
    if args.command == 'discover':
        return
    errors = []
    processed = 0
    for url, doc in docs.items():
        if args.limit and processed >= args.limit:
            break
        processed += 1
        slug = url.rstrip('/').split('/')[-1]
        detail_category = doc.get('data', {}).get('category', '').split('|', 1)[0].strip() or 'Uncategorized'
        folder = archive_root / safe_name(detail_category) / slug
        metadata = folder / 'metadata.json'
        if metadata.exists() and not args.refresh and json.loads(metadata.read_text(encoding='utf-8')).get('images') and all(i.get('local_path') and (folder / i['local_path']).exists() for i in json.loads(metadata.read_text(encoding='utf-8'))['images']):
            old = json.loads(metadata.read_text(encoding='utf-8'))
            old['category_paths'] = sorted(set(old.get('category_paths', []) + [args.category]))
            save(metadata, old)
            print('Skip:', slug)
            continue
        try:
            html = client.get(url).text
            detail = parse_detail(html)
            if not detail['detail_title']:
                raise ValueError('Missing detail title; check parser')
            data = doc['data']
            raw_category = data.get('category', '')
            parts = raw_category.split('|', 1)
            paths = [args.category]
            if metadata.exists():
                paths += json.loads(metadata.read_text(encoding='utf-8')).get('category_paths', [])
            record = dict(detail, source_id=slug.rsplit('-', 1)[-1], title=doc['title'],
                          year=args.year, competition=data['competition'], award=data['award'],
                          category_raw=raw_category, category_detail=parts[0].strip(),
                          category_parent=parent_category,
                          project_stage=parts[1].strip() if len(parts) > 1 else None,
                          category_paths=sorted(set(paths)), source_url=url,
                          image_license='unverified', fetched_at=datetime.now(timezone.utc).isoformat())
            image_folder = folder / 'image'
            image_folder.mkdir(parents=True, exist_ok=True)
            record['images'] = []
            for i, image_url in enumerate(record['image_urls'], 1):
                item = {'url': image_url, 'local_path': None, 'status': 'pending'}
                if not client.robots.can_fetch(client.agent, image_url):
                    item['status'] = 'blocked_by_robots'
                else:
                    try:
                        response = client.get(image_url)
                        mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                        ext = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif', 'image/avif': '.avif'}.get(mime)
                        if not ext or not response.content:
                            raise ValueError('Unexpected or empty image response: ' + mime)
                        relative = f'image/{i:03d}{ext}'
                        temporary = (folder / relative).with_suffix(ext + '.tmp')
                        temporary.write_bytes(response.content)
                        temporary.replace(folder / relative)
                        item.update(local_path=relative, status='downloaded')
                    except (requests.RequestException, ValueError) as exc:
                        item['status'] = 'failed: ' + str(exc)
                record['images'].append(item)
                if item['status'] != 'downloaded':
                    errors.append({'url': image_url, 'error': item['status']})
            save(metadata, record)
            (folder / 'README.md').write_text(render(record), encoding='utf-8')
            for obsolete in ('notes.md', 'source.html'):
                (folder / obsolete).unlink(missing_ok=True)
            print('Saved:', slug)
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append({'url': url, 'error': str(exc)})
            print('Failed:', url, exc)
    save(out / 'last_errors.json', errors)
    records = [dict(json.loads(p.read_text(encoding='utf-8')),
                    archive_path=p.parent.relative_to(out).as_posix())
               for p in out.glob('*/*/*/metadata.json')]
    save(out / 'index.json', records)
    fields = ['source_id', 'title', 'year', 'award', 'category_parent', 'category_detail', 'project_stage', 'source_url', 'archive_path']
    try:
        csv_file = (out / 'index.csv').open('w', encoding='utf-8-sig', newline='')
    except PermissionError:
        csv_file = (out / 'index.latest.csv').open('w', encoding='utf-8-sig', newline='')
        print('index.csv is locked; exported index.latest.csv instead')
    with csv_file as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(records)
    if errors:
        raise SystemExit(f'{len(errors)} failures; see last_errors.json and rerun to retry')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['categories', 'discover', 'fetch'])
    parser.add_argument('--year', type=int, default=2026)
    parser.add_argument('--years', type=int, nargs='+', help='Multiple years, e.g. --years 2025 2026; overrides --year')
    parser.add_argument('--category', default='/12/', help='/12/ discovers all topic categories; use /12/13/ for a single category')
    parser.add_argument('--detail-category', default='',
                        help='Exact category before | in official list; pass an empty string to disable')
    parser.add_argument('--output', default=str(ROOT / 'data'))
    parser.add_argument('--delay', type=float, default=3)
    parser.add_argument('--limit', type=int, default=0, help='Maximum works considered; 0 means all')
    parser.add_argument('--refresh', action='store_true')
    return parser


if __name__ == '__main__':
    run(build_parser().parse_args())
