"""
crawler.py

Hittar alla interna sidor på en webbplats genom att följa länkar från rot-URL:en.
Använder Playwright så att JavaScript-renderade sidor fungerar korrekt.
"""

import asyncio
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse, urldefrag

from playwright.async_api import async_playwright

_executor = ThreadPoolExecutor(max_workers=1)

MAX_PAGES  = 50
MAX_DEPTH  = 3


async def crawl(start_url: str, max_pages: int = MAX_PAGES, max_depth: int = MAX_DEPTH) -> list[str]:
    """
    Returnerar en lista med unika interna URL:er (inklusive start_url) upp till max_pages.
    Körs i en separat ProactorEventLoop på Windows precis som scanner.py.
    """
    if sys.platform == "win32":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            _executor, _crawl_sync, start_url, max_pages, max_depth
        )
    return await _crawl_async(start_url, max_pages, max_depth)


def _crawl_sync(start_url: str, max_pages: int, max_depth: int) -> list[str]:
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_crawl_async(start_url, max_pages, max_depth))
    finally:
        loop.close()


async def _crawl_async(start_url: str, max_pages: int, max_depth: int) -> list[str]:
    parsed = urlparse(start_url)
    base_origin = f"{parsed.scheme}://{parsed.netloc}"

    visited: set[str] = set()
    # queue items: (url, depth)
    queue: deque[tuple[str, int]] = deque([(start_url, 0)])
    found: list[str] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        while queue and len(found) < max_pages:
            url, depth = queue.popleft()
            url = _normalise(url)
            if url in visited:
                continue
            visited.add(url)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                found.append(url)
            except Exception:
                continue

            if depth >= max_depth:
                continue

            # Hämta alla <a href> på sidan
            hrefs: list[str] = await page.evaluate("""
                () => Array.from(document.querySelectorAll('a[href]'))
                         .map(a => a.href)
            """)

            for href in hrefs:
                href, _ = urldefrag(href)        # ta bort #-ankare
                href = _normalise(href)
                if not href.startswith(base_origin):
                    continue                      # externt – hoppa över
                if href in visited:
                    continue
                if _should_skip(href):
                    continue
                queue.append((href, depth + 1))

        await browser.close()

    return found


def _normalise(url: str) -> str:
    """Ta bort trailing slash (utom för rot) och query-strängar."""
    p = urlparse(url)
    # Behåll bara scheme + netloc + path, ingen query/fragment
    path = p.path.rstrip("/") or "/"
    return f"{p.scheme}://{p.netloc}{path}"


def _should_skip(url: str) -> bool:
    """Hoppa över URL:er som inte är vanliga HTML-sidor."""
    skip_exts = (
        ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp",
        ".mp4", ".mp3", ".zip", ".docx", ".xlsx", ".css", ".js",
    )
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in skip_exts)
