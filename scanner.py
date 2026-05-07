"""
scanner.py

Detta är fakta-motorn i vårt verktyg.

Vad den gör:
1. Öppnar en riktig webbläsare (Chromium) i bakgrunden
2. Navigerar till den URL som användaren skickat in
3. Injicerar axe-core (industristandarden för tillgänglighetsregler) i sidan
4. Kör axe-core och samlar in alla fel den hittar
5. Returnerar en strukturerad lista med fel

VIKTIGT: All information här är FAKTA från axe-core, inget är genererat av AI.
LLM:en får bara se den här datan, den hittar aldrig på egna fel.
"""

import asyncio
import base64
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from playwright.async_api import async_playwright
from pydantic import BaseModel
from typing import List

from logger import get_logger

log = get_logger("scanner")

# Trådpool för att köra Playwright i en egen tråd med ProactorEventLoop
# (uvicorn på Windows använder SelectorEventLoop som inte stöder subprocesser)
_executor = ThreadPoolExecutor(max_workers=2)


# axe-core laddas ner från ett publikt CDN som en JavaScript-fil
# Vi injicerar den här i den sida vi vill granska
AXE_CDN_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"


class A11yIssue(BaseModel):
    """
    En enskild tillgänglighetsbrist. Vi använder Pydantic för att
    garantera att datan alltid har rätt struktur.
    """
    rule_id: str           # T.ex. "image-alt"
    wcag_reference: str    # T.ex. "WCAG 1.1.1"
    impact: str            # "critical", "serious", "moderate", "minor"
    description: str       # Vad axe-core säger är fel
    help_text: str         # Kort förklaring från axe
    help_url: str          # Länk till axe-core-dokumentationen
    affected_html: str     # Den HTML-snutt som är felaktig
    selector: str          # CSS-selektor så man kan hitta elementet
    screenshot_b64: str = ""  # Base64-kodad skärmdump av elementet (om tillgänglig)
    source_url: str = ""   # Vilken sida problemet hittades på (används vid sajt-scan)


async def scan_url(url: str) -> tuple[List[A11yIssue], str]:
    """
    Öppnar en webbläsare, besöker URL:en och kör axe-core.
    Returnerar en lista med hittade problem.

    På Windows körs Playwright i en separat tråd med ProactorEventLoop
    eftersom uvicorn använder SelectorEventLoop som inte stöder subprocesser.
    """
    if sys.platform == "win32":
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, _scan_url_sync, url)
    else:
        return await _scan_url_async(url)


def _scan_url_sync(url: str) -> tuple[List[A11yIssue], str]:
    """Kör Playwright i en ny ProactorEventLoop (för Windows-trådar)."""
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scan_url_async(url))
    finally:
        loop.close()


async def _scan_url_async(url: str) -> tuple[List[A11yIssue], str]:
    """
    Öppnar en webbläsare, besöker URL:en och kör axe-core.
    Returnerar en lista med hittade problem.
    """
    issues: List[A11yIssue] = []
    t0 = time.time()
    log.info("[SCAN] Startar skanning av %s", url)

    # async_playwright är kontexthanteraren som startar browsern
    async with async_playwright() as p:
        # Starta Chromium i "headless"-läge (utan synligt fönster)
        log.debug("[SCAN] Startar Chromium (headless)…")
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        log.debug("[SCAN] Browser öppen, navigerar till sidan")

        # Gå till sidan och vänta tills allt laddat klart
        # (inklusive JavaScript – viktigt för moderna sajter)
        try:
            t_nav = time.time()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            log.info("[SCAN] Sidan laddad (%.1fs): %s", time.time() - t_nav, url)
        except Exception as exc:
            log.warning("[SCAN] networkidle timeout, försöker domcontentloaded: %s", exc)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                log.info("[SCAN] Sidan laddad (domcontentloaded): %s", url)
            except Exception as exc2:
                log.error("[SCAN] Kunde inte navigera till %s: %s", url, exc2)
                await browser.close()
                raise

        # Injicera axe-core i sidan. Nu finns "axe"-objektet tillgängligt
        # i sidans JavaScript-kontext.
        log.debug("[SCAN] Injicerar axe-core från %s", AXE_CDN_URL)
        await page.add_script_tag(url=AXE_CDN_URL)

        # Kör axe.run() i webbläsaren. Resultatet skickas tillbaka som JSON.
        # Vi kan köra JavaScript direkt i sidan med page.evaluate().
        log.debug("[SCAN] Kör axe.run() i webbläsaren…")
        t_axe = time.time()
        results = await page.evaluate("async () => await axe.run()")
        violation_count = len(results.get("violations", []))
        log.info("[SCAN] axe-core klar (%.1fs) — %d överträdelse-regler", time.time() - t_axe, violation_count)

        # Försök stänga cookie-bannern innan skärmdumpar tas så den inte täcker elementen
        _COOKIE_SELECTORS = [
            "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
            "#onetrust-accept-btn-handler",
            "button[id*='accept-all']",
            "button[class*='accept-all']",
            "button[class*='acceptAll']",
        ]
        for _sel in _COOKIE_SELECTORS:
            try:
                _btn = page.locator(_sel).first
                if await _btn.is_visible(timeout=400):
                    log.debug("[SCAN] Stänger cookie-banner: %s", _sel)
                    await _btn.click()
                    await page.wait_for_timeout(600)
                    break
            except Exception:
                pass

        # axe returnerar en lista "violations" (fel den är säker på)
        # Regler där varje element får sin egen skärmdump (beskuren till elementet)
        IMAGE_RULES = {
            "image-alt", "input-image-alt", "role-img-alt",
            "svg-img-alt", "image-redundant-alt",
        }

        # Vi översätter dem till vårt egna format
        screenshots_taken = 0
        for violation in results.get("violations", []):
            wcag_ref = _pick_wcag_reference(violation.get("tags", []))

            rule_id = violation.get("id", "")
            nodes = violation.get("nodes", [])
            is_image_rule = rule_id in IMAGE_RULES
            log.debug("[SCAN] Bearbetar regel %s — %d element (%s)",
                      rule_id, len(nodes), violation.get("impact", "?"))

            if is_image_rule:
                # För bildregel: ta en beskuren skärmdump (full sidupplösning, klippt till elementet)
                node_screenshots: list[str] = []
                for node in nodes:
                    targets = node.get("target", [])
                    shot = ""
                    if targets:
                        try:
                            locator = page.locator(targets[0]).first
                            await locator.scroll_into_view_if_needed(timeout=2000)
                            # Vänta tills den riktiga bilden laddats:
                            # currentSrc får inte vara data: (blur-placeholder) OCH naturalWidth > 0
                            try:
                                await page.wait_for_function(
                                    "(sel) => { const el = document.querySelector(sel); "
                                    "if (!el || !el.complete) return false; "
                                    "if (el.currentSrc && el.currentSrc.startsWith('data:')) return false; "
                                    "return el.naturalWidth > 0; }",
                                    targets[0],
                                    timeout=4000,
                                )
                            except Exception:
                                pass
                            bbox = await locator.bounding_box()
                            if bbox:
                                pad = 16
                                min_w, min_h = 300, 200
                                vp = page.viewport_size or {"width": 1280, "height": 800}
                                w = max(bbox["width"]  + pad * 2, min_w)
                                h = max(bbox["height"] + pad * 2, min_h)
                                cx = bbox["x"] + bbox["width"]  / 2
                                cy = bbox["y"] + bbox["height"] / 2
                                clip = {
                                    "x": max(0, cx - w / 2),
                                    "y": max(0, cy - h / 2),
                                    "width":  min(w, vp["width"]),
                                    "height": min(h, vp["height"]),
                                }
                                img_bytes = await page.screenshot(clip=clip, type="jpeg", quality=75, timeout=4000)
                                shot = base64.b64encode(img_bytes).decode()
                                screenshots_taken += 1
                        except Exception as exc:
                            log.debug("[SCAN] Kunde inte ta bildskärmdump för %s: %s", targets[0] if targets else "?", exc)
                            shot = ""
                    node_screenshots.append(shot)
            else:
                # För övriga regler: en kontextskärmdump (hela viewporten) för första elementet
                screenshot_b64 = ""
                first_target = nodes[0].get("target", []) if nodes else []
                if first_target:
                    try:
                        selector = first_target[0]
                        locator = page.locator(selector).first
                        await locator.scroll_into_view_if_needed(timeout=2000)
                        bbox = await locator.bounding_box()
                        vp = page.viewport_size or {"width": 1280, "height": 800}
                        if bbox:
                            # Clip runt elementet (max 900×500) — undviker jättestora PNG:er
                            cx = bbox["x"] + bbox["width"] / 2
                            cy = bbox["y"] + bbox["height"] / 2
                            cw = min(900, vp["width"])
                            ch = min(500, vp["height"])
                            clip_ctx = {
                                "x": max(0, cx - cw / 2),
                                "y": max(0, cy - ch / 2),
                                "width": cw,
                                "height": ch,
                            }
                        else:
                            clip_ctx = None
                        await page.evaluate(
                            """sel => {
                                const el = document.querySelector(sel);
                                if (el) {
                                    el.dataset._a11yOld = el.style.outline;
                                    el.style.outline = '3px solid #e53e3e';
                                    el.style.outlineOffset = '2px';
                                }
                            }""",
                            selector,
                        )
                        shot_kwargs = dict(type="jpeg", quality=70, timeout=4000)
                        if clip_ctx:
                            shot_kwargs["clip"] = clip_ctx
                        img_bytes = await page.screenshot(**shot_kwargs)
                        screenshot_b64 = base64.b64encode(img_bytes).decode()
                        screenshots_taken += 1
                        await page.evaluate(
                            """sel => {
                                const el = document.querySelector(sel);
                                if (el) {
                                    el.style.outline = el.dataset._a11yOld || '';
                                    el.style.outlineOffset = '';
                                    delete el.dataset._a11yOld;
                                }
                            }""",
                            selector,
                        )
                    except Exception as exc:
                        log.debug("[SCAN] Kontextskärmdump misslyckades för %s: %s", selector, exc)
                        screenshot_b64 = ""

            for i, node in enumerate(nodes):
                shot = node_screenshots[i] if is_image_rule else screenshot_b64
                issues.append(A11yIssue(
                    rule_id=rule_id,
                    wcag_reference=wcag_ref,
                    impact=violation.get("impact") or "unknown",
                    description=violation.get("description", ""),
                    help_text=violation.get("help", ""),
                    help_url=violation.get("helpUrl", ""),
                    affected_html=node.get("html", ""),
                    selector=", ".join(node.get("target", [])),
                    screenshot_b64=shot,
                    source_url=url,
                ))

        log.info("[SCAN] %d skärmdumpar tagna", screenshots_taken)
        log.debug("[SCAN] Hämtar site-logo…")
        site_logo_b64 = await _fetch_site_logo(page)
        if site_logo_b64:
            log.debug("[SCAN] Logo hittad (%d byte base64)", len(site_logo_b64))
        else:
            log.debug("[SCAN] Ingen logo hittad")
        await browser.close()

    log.info("[SCAN] ✓ Klar med %s — %d problem på %.1fs", url, len(issues), time.time() - t0)
    return issues, site_logo_b64


async def _fetch_site_logo(page) -> str:
    """Försöker hämta sidans logotyp: og:image → apple-touch-icon → favicon."""
    import httpx
    from urllib.parse import urljoin

    candidates = await page.evaluate("""() => {
        const metas = [
            document.querySelector('meta[property="og:image"]')?.content,
            document.querySelector('meta[name="twitter:image"]')?.content,
            document.querySelector('link[rel="apple-touch-icon"]')?.href,
            document.querySelector('link[rel~="icon"][sizes="192x192"]')?.href,
            document.querySelector('link[rel~="icon"][sizes="180x180"]')?.href,
            document.querySelector('link[rel~="icon"][sizes="128x128"]')?.href,
            document.querySelector('link[rel~="icon"]')?.href,
        ];
        return metas.filter(Boolean);
    }""")

    for url in candidates:
        try:
            async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
                r = await client.get(url)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return base64.b64encode(r.content).decode()
        except Exception as exc:
            log.debug("[SCAN] Logo-kandidat misslyckades (%s): %s", url, exc)
            continue
    return ""


# axe-core listar alltid level-tag (wcag2a, wcag22aa, …) FÖRE kriterietaggen
# (wcag111, wcag1410). Vi behöver kriterietaggen — den ser ut som "wcag" + 3-4
# siffror där sista siffran kan vara tvåsiffrig (ex. wcag1410 = WCAG 1.4.10).
_WCAG_CRITERION_RE = re.compile(r"^wcag(\d)(\d)(\d{1,2})$")


def _pick_wcag_reference(tags: list) -> str:
    """
    Väljer den mest specifika WCAG-kriterietaggen och formaterar den.
    Hoppar över level-taggar (wcag2a, wcag22aa, …) som inte pekar på ett kriterium.
    """
    for tag in tags:
        m = _WCAG_CRITERION_RE.match(tag)
        if m:
            return f"WCAG {m.group(1)}.{m.group(2)}.{int(m.group(3))}"
    # Fallback: ingen kriterietagg — visa nåt vettigt om det fanns en wcag-tagg
    wcag_only = [t for t in tags if t.startswith("wcag")]
    if wcag_only:
        return f"WCAG ({wcag_only[0]})"
    return "Okänd"


def _format_wcag(tag: str) -> str:
    """Bakåtkompatibel hjälpare som format 'wcag111' → 'WCAG 1.1.1'."""
    return _pick_wcag_reference([tag])
