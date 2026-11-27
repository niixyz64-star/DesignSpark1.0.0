"""Browser-based iF Student Award collector. Full pagination remains unverified."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from archive_store import RECENT_YEARS, read_card, write_card, valid_image, rebuild_index, resolve_folder
from bs4 import BeautifulSoup
from reddot_crawler import save, safe_name

BASE = 'https://ifdesign.com'
VERSION = '1.1.0'

START = BASE + '/en/social-engagement/student-award/winner-overview/?awardId=1&find=&sort=desc&yearId=0'


def category_matches(category, filters):
    if not filters:
        return True
    actual = (category or '').strip().casefold()
    for value in filters:
        value = value.strip().casefold()
        if value.isdigit() and actual[:2] == f'{int(value):02d}':
            return True
        if value == actual:
            return True
    return False


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
    record['source'] = 'IF'
    record['award_level'] = record.get('selection_award', 'unverified')
    write_card(folder, record)


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
            self.page.get_by_text(re.compile(r'^\d+ Projects$')).first.wait_for(state='attached')
        return SimpleNamespace(text=self.page.content())


def filter_option(page, label):
    """Exclude the identically named removal chip by requiring a radio child."""
    radio = page.get_by_role('radio', name=label, exact=True)
    return page.locator('a').filter(has=radio).first


def require_selected_filter(page, label):
    from playwright.sync_api import expect
    radio = filter_option(page, label).get_by_role('radio', name=label, exact=True)
    try:
        expect(radio).to_be_checked(timeout=60000)
    except AssertionError as exc:
        raise RuntimeError(f'Filter not selected: {label}; actual URL: {page.url}') from exc


def select_list_filter(client, label):
    page = client.page
    option = filter_option(page, label)
    try:
        option.wait_for(state='attached')
    except Exception as exc:
        raise RuntimeError(f'Official filter unavailable or not published: {label}; URL: {page.url}') from exc
    radio = option.get_by_role('radio', name=label, exact=True)
    if not radio.is_checked():
        href = option.get_attribute('href')
        if not href:
            raise RuntimeError(f'Missing official filter link: {label}')
        target = urljoin(page.url, href)
        if urlparse(target).scheme != 'https' or urlparse(target).hostname != 'ifdesign.com':
            raise RuntimeError(f'Unexpected filter URL: {target}')
        client.get(target)
    require_selected_filter(page, label)


def fetch(args):
    out = Path(args.output).resolve() / 'IF' / str(args.year)
    report = {'year':args.year, 'award':args.award, 'state':'running', 'complete':False}
    destination = out / '_index' / ('task-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '.json')
    save(destination, report)
    client = None
    try:
        client = IFClient(args.delay, args.browser)
        result = collect(args, client)
        report.update(state='finished', complete=not bool(args.limit), limited=bool(args.limit))
        return result
    except BaseException as exc:
        report.update(state='interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed', error=str(exc))
        raise
    finally:
        save(destination, report)
        rebuild_index(out)
        if client is not None: client.close()


def collect(args, client):
    out = Path(args.output).resolve() / 'IF' / str(args.year)
    print('Incremental collection 1.1.0:', out, flush=True)
    current_records = {}
    projects = set(args.project_url or [])
    expected = None
    if not projects:
        if args.year not in RECENT_YEARS:
            raise RuntimeError(f'Supported list years: {RECENT_YEARS}')
        client.get(args.url)
        page = client.page
        year_label = f'Award year {args.year}'
        select_list_filter(client, year_label)
        award_label = {'student': 'iF DESIGN STUDENT AWARD',
                       'best': 'iF DESIGN STUDENT AWARD Best of the Year'}[args.award]
        select_list_filter(client, award_label)
        # Verify both rendered selections after navigation; selected filter chips
        # have removal URLs, so neither their href nor URL equality proves selection.
        require_selected_filter(page, year_label)
        require_selected_filter(page, award_label)
        selected_url = page.url
        print(f'Filters confirmed: {year_label}; {award_label}', flush=True)
        counter = page.get_by_text(re.compile(r'^\d+ Projects$')).first
        counter.wait_for(state='attached')
        expected = int(re.search(r'\d+', counter.inner_text()).group())
        if args.expected_count and expected != args.expected_count:
            raise RuntimeError(f'List count changed: expected {args.expected_count}, page shows {expected}')
        links = page.locator('#resultList a[href*="/winner-ranking/project/"]')
        while True:
            for href in links.evaluate_all('(nodes) => nodes.map(n => n.href)'):
                projects.add(href.split('#')[0])
            save(out / 'discovery.json', {'source': selected_url, 'year': args.year, 'award': args.award, 'categories': args.category, 'expected': expected,
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
    if not projects and expected == 0:
        print('Official selected scope contains 0 projects')
        return
    if not projects:
        raise RuntimeError('No project links found. Site may require JavaScript; pagination/API must be inspected. No archive reported as complete.')
    if args.command == 'discover':
        return
    failures = []
    selected = sorted(projects)
    matched, excluded = set(), set()
    stopped_by_limit = False
    for position, url in enumerate(selected, 1):
        print(f'Work {position}/{len(selected)}: {url}', flush=True)
        try:
            record = parse_project(client.get(url).text, args)
            if not record['image_urls']:
                raise ValueError('No official project images found')
            if not record['year_verified']:
                raise ValueError('Missing verified year or category: ' + url)
            if not category_matches(record['category'], args.category):
                excluded.add(url)
                print('Category excluded:', record['category'], flush=True)
                continue
            matched.add(url)
            record['selection_award'] = args.award if expected is not None else 'unverified'
            record['selection_source'] = selected_url if expected is not None else None
            record['source_url'] = url
            title = record['title']
            url_parts = urlparse(url).path.rstrip('/').split('/')
            project_id = url_parts[-1]
            project_name = url_parts[-2] if len(url_parts) > 1 else title
            record['source_id'] = project_id
            parent = out / safe_name(record['category'] or '待分类')
            suffix = '-' + record['selection_award']
            folder = parent / safe_name(f'{project_name}-{project_id}{suffix}')
            folder = resolve_folder(out, folder, project_id)
            old = read_card(folder)
            if expected is None:
                print('Single project: award level unverified; use list filters to verify', flush=True)
            (folder / 'image').mkdir(parents=True, exist_ok=True)
            record['images'] = [dict(url=u, status='pending', local_path=None) for u in record['image_urls']]
            record['archive_path'] = folder.relative_to(out).as_posix()
            current_records[url] = record
            write_record(folder, record)  # Save the template BEFORE any network image transfer.
            for i, image_url in enumerate(record['image_urls'], 1):
                previous = next((item for item in (old or {}).get('images', []) if item['url'] == image_url and valid_image(folder, item)), None)
                if previous and not args.refresh:
                    record['images'][i-1] = previous
                    write_record(folder, record)
                    continue
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
            if args.limit and len(matched) >= args.limit:
                stopped_by_limit = True
                break
        except Exception as exc:
            failures.append({'url': url, 'error': str(exc)})
            save(out / 'errors.json', failures)
            print('Failed:', url, str(exc), flush=True)
            if isinstance(exc, RuntimeError) and ('403' in str(exc) or '429' in str(exc)):
                raise
    records = list(current_records.values())
    rebuild_index(out)
    save(out / 'errors.json', failures)
    complete_urls = {r['source_url'] for r in records if r.get('images_complete') and r.get('year_verified')}
    missing = sorted(matched - complete_urls)
    save(out / 'run_report.json', {'year': args.year, 'award': args.award, 'categories': args.category, 'expected': expected,
         'version': VERSION, 'fresh_collection': False, 'limited': stopped_by_limit,
         'matched': len(matched), 'excluded': len(excluded), 'discovered': len(projects), 'missing_or_incomplete': missing, 'errors': failures,
         'complete': not stopped_by_limit and not missing and not failures and (expected is None or len(projects) == expected)})
    if missing or failures:
        raise SystemExit(f'Not complete: {len(missing)} missing/incomplete; see run_report.json')
    print(f'Completed selected scope: {len(matched)} matched works; {len(excluded)} excluded; limited={stopped_by_limit}.')


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['discover', 'fetch'])
    parser.add_argument('--url', default=START)
    parser.add_argument('--year', type=int, choices=RECENT_YEARS, default=max(RECENT_YEARS))
    parser.add_argument('--award', choices=['student', 'best'], default='student', help='Official Student Award or Best of the Year filter')
    parser.add_argument('--category', nargs='+', default=[], help='SDG numbers 01..17 or exact category names; multiple values use OR')
    parser.add_argument('--image-timeout', type=int, default=120, help='Read inactivity timeout in seconds')
    parser.add_argument('--image-retries', type=int, default=3)
    parser.add_argument('--refresh', action='store_true', help='Refresh official content and redownload images; user notes are preserved')
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
