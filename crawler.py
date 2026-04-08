import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time


HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}


def normalize_url(url):
    parsed = urlparse(url)
    # Remove fragment and trailing slash
    clean = parsed._replace(fragment='').geturl().rstrip('/')
    return clean


def is_same_domain(url, base_domain):
    parsed = urlparse(url)
    return parsed.netloc == base_domain or parsed.netloc == 'www.' + base_domain or 'www.' + parsed.netloc == base_domain


def should_crawl(url):
    skip_extensions = (
        '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
        '.pdf', '.zip', '.rar', '.exe', '.mp4', '.mp3',
        '.css', '.js', '.ico', '.xml', '.txt', '.json'
    )
    path = urlparse(url).path.lower()
    return not any(path.endswith(ext) for ext in skip_extensions)


def crawl_website(base_url, max_pages=30, callback=None):
    """
    Crawl a website starting from base_url.

    Args:
        base_url: Starting URL
        max_pages: Maximum number of pages to crawl
        callback: Optional function(current, total, url) for progress updates

    Returns:
        List of page dicts with keys: url, status_code, soup, title, load_time, error
    """
    base_url = normalize_url(base_url)
    base_domain = urlparse(base_url).netloc

    visited = set()
    to_visit = [base_url]
    pages = []

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        url = normalize_url(url)

        if url in visited:
            continue
        if not should_crawl(url):
            continue

        visited.add(url)

        if callback:
            callback(len(visited), min(len(visited) + len(to_visit), max_pages), url)

        page_data = {
            'url': url,
            'status_code': 0,
            'soup': None,
            'title': '',
            'load_time': 0.0,
            'error': None
        }

        try:
            start = time.time()
            response = requests.get(
                url,
                headers=HEADERS,
                timeout=12,
                allow_redirects=True
            )
            page_data['load_time'] = round(time.time() - start, 2)
            page_data['status_code'] = response.status_code

            content_type = response.headers.get('Content-Type', '')
            if 'text/html' not in content_type:
                pages.append(page_data)
                continue

            soup = BeautifulSoup(response.text, 'lxml')

            # Extract footer before removing non-content elements
            page_data['footer_html'] = None
            footer_tag = soup.find('footer')
            if footer_tag:
                page_data['footer_html'] = str(footer_tag)

            # Remove non-content elements
            for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
                tag.decompose()

            page_data['soup'] = soup
            title_tag = soup.find('title')
            page_data['title'] = title_tag.get_text().strip() if title_tag else ''

            # Only follow links from successful pages
            if response.status_code == 200:
                for link in soup.find_all('a', href=True):
                    href = link['href'].strip()
                    if not href or href.startswith(('#', 'mailto:', 'tel:', 'javascript:')):
                        continue

                    full_url = normalize_url(urljoin(url, href))
                    parsed = urlparse(full_url)

                    if (parsed.scheme in ('http', 'https')
                            and is_same_domain(full_url, base_domain)
                            and full_url not in visited
                            and full_url not in to_visit
                            and should_crawl(full_url)):
                        to_visit.append(full_url)

        except requests.exceptions.Timeout:
            page_data['error'] = 'Timeout ao carregar a página'
        except requests.exceptions.ConnectionError:
            page_data['error'] = 'Erro de conexão'
        except Exception as e:
            page_data['error'] = str(e)

        pages.append(page_data)
        time.sleep(0.3)  # Polite crawling

    return pages


def fetch_page_playwright(url):
    """
    Fetch a JS-rendered page using Playwright (headless Chromium).
    Falls back to fetch_page if Playwright is not installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print('[PLAYWRIGHT] não instalado, usando requests')
        return fetch_page(url)

    url = normalize_url(url)
    if not should_crawl(url):
        return None

    page_data = {
        'url': url,
        'status_code': 0,
        'soup': None,
        'title': '',
        'load_time': 0.0,
        'error': None
    }

    try:
        start = time.time()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=HEADERS['User-Agent'])
                page = context.new_page()
                try:
                    response = page.goto(url, timeout=15000, wait_until='domcontentloaded')
                except Exception:
                    # Fallback: page might have partially loaded
                    response = None
                page_data['load_time'] = round(time.time() - start, 2)
                page_data['status_code'] = response.status if response else 200
                # Brief wait for JS rendering, but don't hang
                try:
                    page.wait_for_load_state('networkidle', timeout=5000)
                except Exception:
                    pass
                html = page.content()
            finally:
                browser.close()

        soup = BeautifulSoup(html, 'lxml')

        page_data['footer_html'] = None
        footer_tag = soup.find('footer')
        if footer_tag:
            page_data['footer_html'] = str(footer_tag)

        for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
            tag.decompose()
        page_data['soup'] = soup
        title_tag = soup.find('title')
        page_data['title'] = title_tag.get_text().strip() if title_tag else ''

    except Exception as e:
        page_data['error'] = str(e)
        print(f'[PLAYWRIGHT] Erro em {url}: {e}')

    time.sleep(0.5)
    return page_data


def fetch_page(url):
    """Fetch a single page and return a page_data dict (used for sitemap-based analysis)."""
    url = normalize_url(url)
    if not should_crawl(url):
        return None

    page_data = {
        'url': url,
        'status_code': 0,
        'soup': None,
        'title': '',
        'load_time': 0.0,
        'error': None
    }

    try:
        start = time.time()
        response = requests.get(url, headers=HEADERS, timeout=12, allow_redirects=True)
        page_data['load_time'] = round(time.time() - start, 2)
        page_data['status_code'] = response.status_code

        content_type = response.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            return page_data

        soup = BeautifulSoup(response.text, 'lxml')

        page_data['footer_html'] = None
        footer_tag = soup.find('footer')
        if footer_tag:
            page_data['footer_html'] = str(footer_tag)

        for tag in soup(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
            tag.decompose()

        page_data['soup'] = soup
        title_tag = soup.find('title')
        page_data['title'] = title_tag.get_text().strip() if title_tag else ''

    except requests.exceptions.Timeout:
        page_data['error'] = 'Timeout'
    except requests.exceptions.ConnectionError:
        page_data['error'] = 'Erro de conexão'
    except Exception as e:
        page_data['error'] = str(e)

    time.sleep(0.3)
    return page_data
