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

from archive_store import RECENT_YEARS, read_card, write_card, valid_image, rebuild_index, resolve_folder
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
    destination = Path(args.output).resolve() / 'RedDot-Design Concept' / '_index' / ('task-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
    report = {'state':'running', 'complete':False, 'year':args.year, 'years':args.years,
              'category':args.category, 'award':args.award, 'limit':args.limit}
    save(destination, report)
    try:
        result = _run(args)
        report.update(state='finished', complete=not bool(args.limit), limited=bool(args.limit))
        return result
    except BaseException as exc:
        report.update(state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',error=str(exc))
        raise
    finally:
        save(destination, report)


def _run(args):
    years = list(dict.fromkeys(args.years or [args.year]))
    if any(year not in RECENT_YEARS for year in years):
        raise ValueError(f'Supported years: {RECENT_YEARS}')
    client = Client(args.delay)
    failures = []
    for year in years:
        child = argparse.Namespace(**vars(args))
        child.year = year
        child.progress = {'seen': set(), 'count': 0}
        print(f'Year: {year}', flush=True)
        try:
            run_year(child, client)
        except (SystemExit, requests.RequestException) as exc:
            failures.append(f'{year}: {exc}')
            print(f'Year failed: {year}: {exc}', flush=True)
    if failures:
        raise SystemExit('\n'.join(failures))


def resolve_category_name(concept, name):
    """Resolve only against the current response, never a local catalogue."""
    def normalized(value):
        return re.sub(r'[^a-z0-9]+', '', value.casefold())
    nodes=[]
    def walk(node):
        if node.get('value') in STAGE_PATHS: return
        if node.get('value') != '/12/': nodes.append(node)
        for child in node.get('children', []): walk(child)
    walk(concept)
    exact=[node for node in nodes if node['label'].strip().casefold()==name.strip().casefold()]
    matches=exact or [node for node in nodes if normalized(node['label'])==normalized(name)]
    paths={node['value']:node for node in matches}
    if len(paths)!=1:
        labels='; '.join(f"{n['label']} ({n['value']})" for n in (matches or nodes))
        raise ValueError(f'Category name unavailable or ambiguous: {name}. Live official categories: {labels}')
    return next(iter(paths.values()))


def export_category_index(out, concept, year):
    categories = [c for c in concept.get('children', []) if c['value'] not in STAGE_PATHS]
    rows = []
    def walk(node, parent=None):
        rows.append({'name':node['label'], 'path':node['value'], 'parent':parent,
                     'count':node.get('count',0)})
        for child in node.get('children',[]): walk(child,node['label'])
    for category in categories: walk(category)
    save(out / '_index' / 'categories.json', {'year':year,'topic_count':len(categories),
         'source':'live official search facets','categories':rows})
    table = '# Red Dot Design Concept categories\n\n'
    table += f'Year: {year}; topic categories: {len(categories)}\n\n'
    table += '| Category | Official path | Parent | Works |\n|---|---|---|---|\n'
    for row in rows:
        name = row['name'].replace('|',' / ')
        table += f"| {name} | {row['path']} | {row['parent'] or '-'} | {row['count']} |\n"
    (out / '_index' / 'categories.md').write_text(table,encoding='utf-8')
    return categories


def run_year(args, client):
    # Always load the complete live directory before resolving a selected topic.
    payload = client.search(args.year, '/12/')
    facet = next(f for f in payload['facets'] if f['field']=='meta_categories')
    concept = next(c for c in facet['children'] if c['value']=='/12/')
    out = Path(args.output).resolve() / 'RedDot-Design Concept' / str(args.year)
    categories = export_category_index(out,concept,args.year)
    if not args.category.startswith('/'):
        match = resolve_category_name(concept,args.category)
        print(f"Live official category: {match['label']} -> {match['value']}",flush=True)
        args.category = match['value']
    if args.command == 'categories':
        for row in categories: print(f"{row['value']}  {row['label']} ({row.get('count',0)})")
        print(f'Topic categories: {len(categories)}')
        return
    if args.category != '/12/':
        return run_category(args, client)
    save(out / 'categories.json', concept)
    print(f'Design Concept: {len(categories)} topic categories')
    failures = []
    for category in categories:
        if args.limit and args.progress['count'] >= args.limit:
            break
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
    out = Path(args.output).resolve() / 'RedDot-Design Concept' / str(args.year)
    client = client or Client(args.delay)
    initial = client.search(args.year, args.category)
    categories = next(f for f in initial['facets'] if f['field'] == 'meta_categories')
    concept = next(c for c in categories['children'] if c['value'] == '/12/')
    def containing(node, value):
        if node['value'] == value: return True
        return any(containing(c, value) for c in node.get('children', []))
    topic = next((c for c in concept.get('children', []) if containing(c,args.category)), None)
    if topic is None:
        raise ValueError('Unknown topic category; use categories to inspect official paths')
    parent_category = topic['label']
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
    if args.award:
        docs = {url: doc for url, doc in docs.items() if doc.get('data', {}).get('award', '').casefold() == args.award.casefold()}
    if args.command == 'discover':
        print(f'Matching works: {len(docs)}')
        return
    errors = []
    processed = 0
    for url, doc in docs.items():
        if args.limit and args.progress['count'] >= args.limit:
            break
        if url in args.progress['seen']: continue
        args.progress['seen'].add(url)
        args.progress['count'] += 1
        processed += 1
        slug = url.rstrip('/').split('/')[-1]
        detail_category = doc.get('data', {}).get('category', '').split('|', 1)[0].strip() or 'Uncategorized'
        award = str(doc.get('data', {}).get('award') or 'unverified')
        award_suffix = 'RedDot' if re.sub(r'[^a-z]', '', award.casefold()) == 'reddot' else award
        work_name = doc.get('title', '').strip()
        if not work_name: raise ValueError('Missing official work name')
        folder = archive_root / safe_name(detail_category) / safe_name(work_name + '-' + award_suffix)
        existing = read_card(folder)
        if existing and str(existing.get('source_id')) != slug.rsplit('-',1)[-1]:
            raise ValueError(f'Same title and award used by different works: {folder}; no overwrite performed')
        folder = resolve_folder(out, folder, slug.rsplit('-',1)[-1])
        old = read_card(folder)
        if old and not args.refresh and old.get('images') and all(valid_image(folder,i) for i in old['images']):
            old.update(category_parent=parent_category, category_detail=detail_category, award=award, award_level=award)
            write_card(folder,old)
            print('Skip complete:', slug)
            continue
        try:
            html = client.get(url).text
            detail = parse_detail(html)
            if not detail['image_urls']:
                raise ValueError('No official project images found')
            if not detail['detail_title']:
                raise ValueError('Missing detail title; check parser')
            data = doc['data']
            raw_category = data.get('category', '')
            parts = raw_category.split('|', 1)
            paths = [args.category]
            if old:
                paths += old.get('category_paths', [])
            record = dict(detail, source_id=slug.rsplit('-', 1)[-1], title=doc['title'],
                          year=args.year, competition=data['competition'], award=data['award'],
                          category_raw=raw_category, category_detail=parts[0].strip(),
                          category_parent=parent_category,
                          project_stage=parts[1].strip() if len(parts) > 1 else None,
                          category_paths=sorted(set(paths)), source_url=url,
                          image_license='unverified', fetched_at=datetime.now(timezone.utc).isoformat())
            image_folder = folder / 'image'
            image_folder.mkdir(parents=True, exist_ok=True)
            record.update(source='RedDot', award_level=data['award'])
            record['images'] = [dict(url=u,local_path=None,status='pending') for u in record['image_urls']]
            write_card(folder, record)
            for i, image_url in enumerate(record['image_urls'], 1):
                item = {'url': image_url, 'local_path': None, 'status': 'pending'}
                previous = next((item for item in (old or {}).get('images',[]) if item['url']==image_url and valid_image(folder,item)), None)
                if previous and not args.refresh:
                    record['images'][i-1] = previous
                    continue
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
                record['images'][i-1] = item
                write_card(folder, record)
                if item['status'] != 'downloaded':
                    errors.append({'url': image_url, 'error': item['status']})
            write_card(folder, record)
            if not record['images_complete']:
                errors.append({'url': url, 'error': 'Missing or invalid images'})
            print('Saved:', slug)
        except RuntimeError:
            raise
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append({'url': url, 'error': str(exc)})
            print('Failed:', url, exc)
    save(out / 'last_errors.json', errors)
    records = rebuild_index(out)
    save(out / '_index' / 'last_run.json', {'year':args.year,'category':args.category,
        'award':args.award,'available_matches':len(docs),'processed':processed,
        'limit':args.limit,'errors':errors,'complete':not errors,
        'scope':'selected scope, possibly limited; not necessarily the full year'})
    if args.limit and args.progress['count'] < args.limit:
        print(f"Available so far: {args.progress['count']}/{args.limit}; fewer matches may exist")
    if errors:
        raise SystemExit(f'{len(errors)} failures; see last_errors.json and rerun to retry')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['categories', 'discover', 'fetch'])
    parser.add_argument('--year', type=int, choices=RECENT_YEARS, default=max(RECENT_YEARS))
    parser.add_argument('--years', type=int, nargs='+', help='Multiple years, e.g. --years 2025 2026; overrides --year')
    parser.add_argument('--category', default='/12/', help='Live official category name (e.g. HomeFuture) or category path; /12/ means all topics')
    parser.add_argument('--award', default='', help='Exact official award label; omitted means all award levels')
    parser.add_argument('--detail-category', default='',
                        help='Exact category before | in official list; pass an empty string to disable')
    parser.add_argument('--output', default=str(ROOT / 'data'))
    parser.add_argument('--delay', type=float, default=3)
    parser.add_argument('--limit', type=int, default=0, help='Maximum works considered; 0 means all')
    parser.add_argument('--refresh', action='store_true')
    return parser


if __name__ == '__main__':
    run(build_parser().parse_args())
