"""
Fetches and parses sitemap.xml files (including sitemap index files).
Returns a flat list of page URLs from the sitemap.
"""

import time
import requests
from urllib.parse import urlparse
from xml.etree import ElementTree as ET


_NS = {
    'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9',
    'image': 'http://www.google.com/schemas/sitemap-image/1.1',
    'news': 'http://www.google.com/schemas/sitemap-news/0.9',
}

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; SEOAnalyzer/1.0)',
    'Accept': 'text/xml,application/xml,*/*',
}


def _fetch(url, timeout=15):
    resp = requests.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _parse_urls_from_xml(content):
    """Parse <url><loc> entries from a standard sitemap XML."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        raise ValueError(f'XML inválido: {e}')

    # Strip namespace for comparison
    tag = root.tag.split('}')[-1] if '}' in root.tag else root.tag

    urls = []

    if tag == 'sitemapindex':
        # Sitemap index — collect child sitemap URLs
        for sitemap_el in root.iter():
            local = sitemap_el.tag.split('}')[-1] if '}' in sitemap_el.tag else sitemap_el.tag
            if local == 'loc':
                parent = sitemap_el.tag.split('}')[0].lstrip('{')
                urls.append(('sitemap', sitemap_el.text.strip()))
    elif tag == 'urlset':
        for url_el in root.iter():
            local = url_el.tag.split('}')[-1] if '}' in url_el.tag else url_el.tag
            if local == 'loc':
                # Skip image/news loc sub-elements (they are nested inside url)
                urls.append(('url', url_el.text.strip()))

    return tag, urls


def get_sitemap_urls(sitemap_url, max_urls=500, _depth=0):
    """
    Fetches sitemap_url and returns a list of page URLs.
    Handles sitemap index files recursively (max depth 3).
    """
    if _depth > 3:
        return []

    try:
        content = _fetch(sitemap_url)
    except Exception as e:
        raise ConnectionError(f'Não foi possível acessar o sitemap: {e}')

    tag, entries = _parse_urls_from_xml(content)

    if tag == 'sitemapindex':
        # Recurse into each child sitemap
        all_urls = []
        for kind, child_url in entries:
            if len(all_urls) >= max_urls:
                break
            try:
                time.sleep(0.2)
                child_urls = get_sitemap_urls(child_url, max_urls - len(all_urls), _depth + 1)
                all_urls.extend(child_urls)
            except Exception as e:
                print(f'[SITEMAP] Erro ao ler sub-sitemap {child_url}: {e}')
        return all_urls[:max_urls]

    # Standard urlset — filter only http/https URLs
    page_urls = [
        url for kind, url in entries
        if kind == 'url' and url.startswith('http')
    ]
    return list(dict.fromkeys(page_urls))[:max_urls]  # deduplicate, preserve order


def guess_sitemap_url(site_url):
    """Returns the most common sitemap location for a given site URL."""
    parsed = urlparse(site_url)
    base = f'{parsed.scheme}://{parsed.netloc}'
    return f'{base}/sitemap.xml'
