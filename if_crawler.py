"""Browser-based iF Student Award collector. Full pagination remains unverified."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qsl

from bs4 import BeautifulSoup
from reddot_crawler import save, safe_name

BASE = 'https://ifdesign.com'
START = BASE + '/en/social-engagement/student-award/winner-overview/?awardId=1&find=&sort=desc&yearId=0'


def labelled_value(soup, labels):
    """Read explicit label/value pairs; never infer a year from copyright text."""
    for tag in soup.select('dt, th'):
        if tag.get_text(' ', strip=True).rstrip(':').casefold() in labels:
            value = tag.find_next_sibling(['dd', 'td'])
            if value:
                return value.get_text(' ', strip=True)
    return None


def parse_project(html, args):
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.find('h1')
    if not title:
        raise ValueError('Missing project title')
    category_tag = soup.select_one(args.category_selector) if args.category_selector else None
    category = category_tag.get_text(' ', strip=True) if category_tag else labelled_value(soup, {'category', 'discipline', 'sdg', 'sustainable development goal'})
    year_tag = soup.select_one(args.year_selector) if args.year_selector else None
    year_text = year_tag.get_text(' ', strip=True) if year_tag else labelled_value(soup, {'year', 'award year'})
    match = re.search(r'\b(20\d{2})\b', year_text or '')
    official_year = int(match.group(1)) if match else None
    if official_year is not None and official_year != args.year:
        raise ValueError(f'Wrong award year: {official_year}; requested {args.year}')
    meta = soup.find('meta', attrs={'name': 'description'})
    images = []
    tags = soup.select(args.image_selector) if args.image_selector else soup.select('meta[property="og:image"][content]')
    for tag in tags:
        if tag.name == 'img' and tag.get('alt', '').strip() != title.get_text(' ', strip=True):
            continue
        value = tag.get('content') or tag.get('src') or tag.get('data-src')
        if value:
            link = urljoin(BASE, value)
            if link not in images:
                images.append(link)
    return {'title': title.get_text(' ', strip=True), 'award_year': official_year,
            'requested_year': args.year, 'category': category,
            'verified': False, 'year_verified': official_year == args.year,
            'description': meta.get('content') if meta else None,
            'image_urls': images, 'images': [], 'image_license': 'unverified',
            'fetched_at': datetime.now(timezone.utc).isoformat()}


def write_record(folder, record):
    record['images_complete'] = bool(record['images']) and all(
        i.get('status') == 'downloaded' and i.get('local_path') and
        (folder / i['local_path']).is_file() for i in record['images'])
    save(folder / 'metadata.json', record)
    pictures = '\n\n'.join(f"![{record['title']}]({i['local_path']})" if i.get('local_path')
        else f"- 图片尚未下载：{i['status']}" for i in record['images'])
    (folder / 'README.md').write_text(
        f"# {record['title']}\n\n- 年份：{record['award_year']}\n- 大类：{record['category']}\n"
        f"- 来源：{record['source_url']}\n\n## 官方简介\n\n{record['description'] or '未提供'}"
        f"\n\n## 作品图片\n\n{pictures}\n", encoding='utf-8')


def download_image(client, url, folder, number, attempts, read_timeout):
    import time
    import requests
    from PIL import Image
    if not client.rules_for(url).can_fetch(client.agent, url):
        return {'url': url, 'status': 'blocked_by_robots', 'local_path': None}
    item = {'url': url, 'status': 'pending', 'local_path': None}
    temp = folder / 'image' / f'{number:03d}.part'
    for attempt in range(1, attempts + 1):
        try:
            client.pace()
            print(f'  Image {number}, attempt {attempt}/{attempts}', flush=True)
            # Only the public asset host uses streaming HTTP; website DOM stays Playwright.
            with client.assets.get(url, stream=True, timeout=(20, read_timeout)) as response:
                if response.status_code in (401, 403, 429):
                    raise RuntimeError(f'Asset access restricted ({response.status_code})')
                response.raise_for_status()
                expected = int(response.headers.get('Content-Length', '0'))
                mime = response.headers.get('Content-Type', '').split(';')[0].lower()
                if not mime.startswith('image/'):
                    raise ValueError('Response is not an image: ' + mime)
                size = 0
                with temp.open('wb') as f:
                    for chunk in response.iter_content(128 * 1024):
                        if chunk:
                            f.write(chunk)
                            size += len(chunk)
                if not size or (expected and not response.headers.get('Content-Encoding') and size != expected):
                    raise ValueError('Incomplete image transfer')
            with Image.open(temp) as image:
                fmt = image.format
                image.verify()
            ext = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp', 'GIF': '.gif', 'AVIF': '.avif'}.get(fmt)
            if not ext:
                raise ValueError('Unsupported image format: ' + str(fmt))
            relative = f'image/{number:03d}{ext}'
            temp.replace(folder / relative)
            return dict(url=url, status='downloaded', local_path=relative, bytes=size)
        except RuntimeError:
            raise
        except (requests.RequestException, OSError, ValueError) as exc:
            item.update(status='download_failed', error=f'{type(exc).__name__}: {str(exc)[:300]}')
            if attempt < attempts:
                time.sleep(min(2 ** attempt, 10))
        finally:
            temp.unlink(missing_ok=True)
    return item


class IFClient:
    """Visible browser, normal TLS verification, no stealth or challenge bypass."""
    def __init__(self, delay, browser='msedge'):
        import time
        from playwright.sync_api import sync_playwright
        self.delay = max(1, delay)
        self.last = 0
        self.agent = 'DesignAwardResearchArchive/0.1'
        self.rules = {}
        self.rule_errors = {}
        import requests
        self.assets = requests.Session()
        self.runtime = sync_playwright().start()
        try:
            options = {'headless': False}
            if browser != 'chromium':
                options['channel'] = browser
            self.browser = self.runtime.chromium.launch(**options)
            self.context = self.browser.new_context()
            self.page = self.context.new_page()
            self.page.set_default_timeout(60000)
            self.robots = self.rules_for(BASE)
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            if hasattr(self, 'browser'):
                self.browser.close()
        finally:
            self.assets.close()
            self.runtime.stop()

    def pace(self):
        import time
        time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
        self.last = time.monotonic()

    def rules_for(self, url):
        from urllib.robotparser import RobotFileParser
        host = urlparse(url)
        origin = f'{host.scheme}://{host.netloc}'
        if origin in self.rules:
            return self.rules[origin]
        if origin in self.rule_errors:
            raise RuntimeError(self.rule_errors[origin])
        page = self.context.new_page()
        try:
            self.pace()
            response = page.goto(origin + '/robots.txt', wait_until='domcontentloaded', timeout=60000)
            # RFC 9309: a 4xx robots response may be treated as unavailable.
            # Azure Blob root /robots.txt returns 400; keep 401/403/429 as stops.
            unavailable = response is not None and (response.status in (404, 410) or
                (host.hostname == 'ifdalivestorage.blob.core.windows.net' and response.status == 400))
            if response is None or (response.status != 200 and not unavailable):
                message = f'Cannot verify robots rules for {origin}: {response.status if response else "no response"}'
                self.rule_errors[origin] = message
                raise RuntimeError(message)
            rules = RobotFileParser()
            if response.status == 200:
                text = page.locator('body').inner_text()
                if '<html' in text.lower() or ('user-agent' not in text.lower() and 'disallow' not in text.lower()):
                    raise RuntimeError('Unexpected robots response; stopped: ' + origin)
                rules.parse(text.splitlines())
            else:
                rules.parse(['User-agent: *', 'Allow: /'])
            self.rules[origin] = rules
            return rules
        finally:
            page.close()

    def get(self, url):
        from types import SimpleNamespace
        if urlparse(url).scheme != 'https':
            raise RuntimeError('Only HTTPS URLs are supported')
        if not self.rules_for(url).can_fetch(self.agent, url):
            raise RuntimeError('robots.txt disallows: ' + url)
        self.pace()
        if urlparse(url).hostname != 'ifdesign.com':
            response = self.context.request.get(url, timeout=60000)
            if not response.ok:
                raise RuntimeError(f'Image request failed ({response.status}): {url}')
            return SimpleNamespace(content=response.body(), headers={'Content-Type': response.headers.get('content-type', '')})
        response = self.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        if response is None or response.status >= 400:
            raise RuntimeError(f'Browser access failed ({response.status if response else "no response"}): {url}')
        # Native rendered DOM, not raw HTML. No CAPTCHA solving or access-limit bypass.
        reject = self.page.get_by_role('button', name='REJECT COOKIES', exact=True)
        if reject.count() and reject.is_visible():
            reject.click()
        if '/winner-ranking/project/' in url:
            self.page.locator('#Award_Year').wait_for(state='attached')
            self.page.locator('h1').wait_for()
            self.page.locator('img[src*="/user-uploads/"]').first.wait_for(state='attached')
        else:
            self.page.locator('a[href*="/winner-ranking/project/"]').first.wait_for()
        return SimpleNamespace(text=self.page.content())


def fetch(args):
    client = IFClient(args.delay, args.browser)
    try:
        return collect(args, client)
    finally:
        client.close()


def collect(args, client):
    out = Path(args.output).resolve() / 'IF' / str(args.year)
    projects = set(args.project_url or [])
    expected = None
    if not projects:
        if args.year not in (2025, 2026):
            raise RuntimeError('Supported list years: 2025, 2026')
        client.get(args.url)
        page = client.page
        year_link = page.get_by_role('link', name=f'Award year {args.year}', exact=True).first
        year_link.wait_for(state='attached')
        href = year_link.get_attribute('href')
        if not href:
            raise RuntimeError('Missing official year filter link')
        selected_url = urljoin(page.url, href)
        if urlparse(selected_url).hostname != 'ifdesign.com':
            raise RuntimeError('Unexpected year filter host')
        # Follow the live official filter, not a guessed yearId mapping.
        client.get(selected_url)
        selected_query = dict(parse_qsl(urlparse(page.url).query))
        target_query = dict(parse_qsl(urlparse(selected_url).query))
        if selected_query.get('yearId') != target_query.get('yearId'):
            raise RuntimeError('Year filter navigation did not match target')
        page.get_by_role('link', name=f'Award year {args.year}', exact=True).first.wait_for(state='attached')
        counter = page.get_by_text(re.compile(r'^\d+ Projects$')).first
        counter.wait_for(state='attached')
        expected = int(re.search(r'\d+', counter.inner_text()).group())
        if args.expected_count and expected != args.expected_count:
            raise RuntimeError(f'List count changed: expected {args.expected_count}, page shows {expected}')
        links = page.locator('#resultList a[href*="/winner-ranking/project/"]')
        while True:
            for href in links.evaluate_all('(nodes) => nodes.map(n => n.href)'):
                projects.add(href.split('#')[0])
            save(out / 'discovery.json', {'source': selected_url, 'year': args.year, 'expected': expected,
                                         'projects': sorted(projects), 'complete': len(projects) == expected})
            print(f'Discovery: {len(projects)}/{expected}', flush=True)
            if len(projects) >= expected:
                break
            before = links.count()
            client.pace()
            links.last.scroll_into_view_if_needed()
            page.mouse.wheel(0, 900)
            try:
                links.nth(before).wait_for(state='attached', timeout=45000)
            except Exception as exc:
                raise RuntimeError(f'Pagination stalled at {len(projects)}/{expected}; discovery saved, rerun later') from exc
        if len(projects) != expected:
            raise RuntimeError('Unique URL count differs from official total')
    if not projects:
        raise RuntimeError('No project links found. Site may require JavaScript; pagination/API must be inspected. No archive reported as complete.')
    if args.command == 'discover':
        return
    failures = []
    selected = sorted(projects)[:args.limit or None]
    for position, url in enumerate(selected, 1):
        print(f'Work {position}/{len(selected)}: {url}', flush=True)
        try:
            existing = next((p for p in out.glob('*/*/metadata.json')
                             if json.loads(p.read_text(encoding='utf-8')).get('source_url') == url), None)
            if existing and not args.refresh:
                old = json.loads(existing.read_text(encoding='utf-8'))
                if old.get('year_verified') and old.get('category') and old.get('images_complete') and (existing.parent / 'README.md').exists() and all(
                    i.get('local_path') and (existing.parent / i['local_path']).is_file() and
                    (existing.parent / i['local_path']).stat().st_size > 0 for i in old.get('images', [])):
                    print('Skip complete:', url, flush=True)
                    continue
            record = parse_project(client.get(url).text, args)
            if not record['year_verified'] or not record['category']:
                raise ValueError('Missing verified year or category: ' + url)
            record['source_url'] = url
            title = record['title']
            url_parts = urlparse(url).path.rstrip('/').split('/')
            project_id = url_parts[-1]
            project_name = url_parts[-2] if len(url_parts) > 1 else title
            record['source_id'] = project_id
            parent = out / safe_name(record['category'] or '待核验分类')
            folder = parent / safe_name(f'{project_name}-{project_id}')
            legacy = parent / safe_name(project_id)
            if legacy.is_dir() and not folder.exists():
                legacy.rename(folder)
            (folder / 'image').mkdir(parents=True, exist_ok=True)
            previous = json.loads((folder / 'metadata.json').read_text(encoding='utf-8')) if (folder / 'metadata.json').exists() else {}
            old_images = {i['url']: i for i in previous.get('images', [])}
            record['images'] = [dict(url=u, status='pending', local_path=None) for u in record['image_urls']]
            write_record(folder, record)  # Save the template BEFORE any network image transfer.
            for i, image_url in enumerate(record['image_urls'], 1):
                old_image = old_images.get(image_url)
                if not args.refresh and old_image and old_image.get('local_path') and (folder / old_image['local_path']).is_file():
                    from PIL import Image
                    try:
                        with Image.open(folder / old_image['local_path']) as image:
                            image.verify()
                        record['images'][i-1] = old_image
                        write_record(folder, record)
                        continue
                    except (OSError, ValueError):
                        pass
                try:
                    item = download_image(client, image_url, folder, i, args.image_retries, args.image_timeout)
                except RuntimeError as exc:
                    record['images'][i-1].update(status='access_or_rules_error', error=str(exc))
                    write_record(folder, record)
                    raise
                record['images'][i-1] = item
                write_record(folder, record)
            print('Saved candidate:', title)
            print('Images:', sum(i['status'] == 'downloaded' for i in record['images']), '/', len(record['images']))
        except Exception as exc:
            failures.append({'url': url, 'error': str(exc)})
            save(out / 'errors.json', failures)
            print('Failed:', url, str(exc), flush=True)
            if isinstance(exc, RuntimeError) and ('403' in str(exc) or '429' in str(exc)):
                raise
    records = [dict(json.loads(p.read_text(encoding='utf-8')), archive_path=p.parent.relative_to(out).as_posix())
               for p in out.glob('*/*/metadata.json')]
    save(out / 'index.json', records)
    save(out / 'errors.json', failures)
    complete_urls = {r['source_url'] for r in records if r.get('images_complete') and r.get('year_verified')}
    missing = sorted(projects - complete_urls)
    save(out / 'run_report.json', {'year': args.year, 'expected': expected,
         'discovered': len(projects), 'missing_or_incomplete': missing, 'errors': failures,
         'complete': not missing and not failures and (expected is None or len(projects) == expected)})
    if missing or failures:
        raise SystemExit(f'Not complete: {len(missing)} missing/incomplete; see run_report.json')
    print(f'Completed: {len(projects)} works, including images.')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'fetch'])
    parser.add_argument('--url', default=START)
    parser.add_argument('--year', type=int, default=2026)
    parser.add_argument('--image-timeout', type=int, default=120, help='Read inactivity timeout in seconds')
    parser.add_argument('--image-retries', type=int, default=3)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--expected-count', type=int, default=0, help='Optional expected total; default reads selected year total from page')
    parser.add_argument('--browser', choices=['msedge', 'chrome', 'chromium'], default='msedge',
                        help='Default: installed Edge; only chromium requires playwright browser download')
    parser.add_argument('--project-url', action='append', help='Official project URL for a single-template trial; repeatable')
    parser.add_argument('--category-selector', default='button[class*="--tag--"]', help='Official category selector, verified on ZITO!')
    parser.add_argument('--year-selector', default='#Award_Year', help='Award badge year, verified on ZITO!')
    parser.add_argument('--image-selector', default='img[src*="/user-uploads/"]', help='Project upload images; excludes navigation assets')
    parser.add_argument('--output', default=str(Path(__file__).resolve().parent / 'data'))
    parser.add_argument('--delay', type=float, default=3)
    parser.add_argument('--limit', type=int, default=0, help='Default: all works; use 1 for a sample')
    return parser


if __name__ == '__main__':
    fetch(build_parser().parse_args())
