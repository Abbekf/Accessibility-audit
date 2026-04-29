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
import sys
from concurrent.futures import ThreadPoolExecutor
from playwright.async_api import async_playwright
from pydantic import BaseModel
from typing import List

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


async def scan_url(url: str) -> List[A11yIssue]:
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


def _scan_url_sync(url: str) -> List[A11yIssue]:
    """Kör Playwright i en ny ProactorEventLoop (för Windows-trådar)."""
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_scan_url_async(url))
    finally:
        loop.close()


async def _scan_url_async(url: str) -> List[A11yIssue]:
    """
    Öppnar en webbläsare, besöker URL:en och kör axe-core.
    Returnerar en lista med hittade problem.
    """
    issues: List[A11yIssue] = []

    # async_playwright är kontexthanteraren som startar browsern
    async with async_playwright() as p:
        # Starta Chromium i "headless"-läge (utan synligt fönster)
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Gå till sidan och vänta tills allt laddat klart
        # (inklusive JavaScript – viktigt för moderna sajter)
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Injicera axe-core i sidan. Nu finns "axe"-objektet tillgängligt
        # i sidans JavaScript-kontext.
        await page.add_script_tag(url=AXE_CDN_URL)

        # Kör axe.run() i webbläsaren. Resultatet skickas tillbaka som JSON.
        # Vi kan köra JavaScript direkt i sidan med page.evaluate().
        results = await page.evaluate("async () => await axe.run()")

        # axe returnerar en lista "violations" (fel den är säker på)
        # Vi översätter dem till vårt egna format
        for violation in results.get("violations", []):
            wcag_tags = [t for t in violation.get("tags", []) if t.startswith("wcag")]
            wcag_ref = _format_wcag(wcag_tags[0]) if wcag_tags else "Okänd"

            # Ta en kontextskärmdump: scrolla till elementet, markera det med
            # en röd ram och fotografera hela viewporten så man ser var på sidan det sitter.
            screenshot_b64 = ""
            nodes = violation.get("nodes", [])
            first_target = nodes[0].get("target", []) if nodes else []
            if first_target:
                try:
                    selector = first_target[0]
                    locator = page.locator(selector).first
                    await locator.scroll_into_view_if_needed(timeout=2000)
                    # Lägg på en synlig markering
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
                    img_bytes = await page.screenshot(timeout=4000)
                    screenshot_b64 = base64.b64encode(img_bytes).decode()
                    # Ta bort markeringen igen
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
                except Exception:
                    screenshot_b64 = ""

            for node in nodes:
                issues.append(A11yIssue(
                    rule_id=violation.get("id", ""),
                    wcag_reference=wcag_ref,
                    impact=violation.get("impact") or "unknown",
                    description=violation.get("description", ""),
                    help_text=violation.get("help", ""),
                    help_url=violation.get("helpUrl", ""),
                    affected_html=node.get("html", ""),
                    selector=", ".join(node.get("target", [])),
                    screenshot_b64=screenshot_b64,
                ))

        await browser.close()

    return issues


def _format_wcag(tag: str) -> str:
    """
    Formaterar 'wcag111' till 'WCAG 1.1.1' för snyggare visning.
    """
    # Plocka ut siffrorna efter "wcag"
    nums = tag.replace("wcag", "")
    if len(nums) == 3:
        return f"WCAG {nums[0]}.{nums[1]}.{nums[2]}"
    elif len(nums) == 2:
        return f"WCAG {nums[0]}.{nums[1]}"
    return f"WCAG ({tag})"
