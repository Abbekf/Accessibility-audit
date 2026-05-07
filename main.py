"""
main.py

Huvudapplikationen. Startar ett webb-API där man kan skicka in en URL
och få tillbaka en PDF-rapport.

Starta med:
    uvicorn main:app --reload

Sen kan du testa i din webbläsare:
    http://localhost:8000/docs    (interaktiv API-dokumentation)
    http://localhost:8000/scan?url=https://www.delorean.se

Flödet:
1. POST /scan med en URL i kroppen
2. scanner.py öppnar sidan och hittar fel med axe-core
3. explainer.py ber Claude förklara varje fel på svenska
4. reporter.py genererar en PDF
5. PDF:en returneras till användaren
"""

import asyncio
import json
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, HTMLResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path

from scanner import scan_url
from explainer import explain_issue
from reporter import generate_html, generate_presentation_html
from crawler import crawl
from favicon_route import router as favicon_router
from logger import get_logger, new_run, get_buffer, subscribe, unsubscribe

log = get_logger("main")


app = FastAPI(
    title="Tillgänglighetsrevisor",
    description="Ett AI-assisterat verktyg som granskar webbsidor mot WCAG.",
)
app.include_router(favicon_router)


@app.on_event("startup")
async def _on_startup() -> None:
    log.info("Tillgänglighetsrevisor startar — alla loggar går till logs/scan.log")


class ScanRequest(BaseModel):
    """
    Indata till /scan-endpointen.
    Pydantic validerar automatiskt att URL:en är giltig.
    """
    url: HttpUrl
    provider: str = "auto"
    model: str = ""


# Färdig presentations-HTML per URL, genererad under /scan
_presentation_cache: dict = {}  # {url_str: str}

MAX_AI_IMAGES_SITE = 20   # max bildanalyser per sajt-scan


async def _explain_issues(issues, provider: str, model: str,
                          seen_rule: dict | None = None,
                          image_count: list | None = None):
    """
    Förklarar en lista issues med AI.
    seen_rule och image_count skickas in utifrån vid sajt-scan
    så att deduplicering fungerar globalt över alla sidor.
    """
    from explainer import ExplainedIssue, IMAGE_RULES

    if seen_rule is None:
        seen_rule = {}
    if image_count is None:
        image_count = [0]

    issues_to_explain = []
    for issue in issues:
        if issue.rule_id in IMAGE_RULES:
            if image_count[0] < MAX_AI_IMAGES_SITE:
                issues_to_explain.append(issue)
                image_count[0] += 1
        elif issue.rule_id not in seen_rule:
            seen_rule[issue.rule_id] = None
            issues_to_explain.append(issue)

    explanations_list = await asyncio.gather(*[
        explain_issue(issue, provider=provider, model=model)
        for issue in issues_to_explain[:80]
    ])

    explanation_by_rule: dict = {}
    explanation_by_obj: dict = {}
    for exp in explanations_list:
        if exp.issue.rule_id in IMAGE_RULES:
            explanation_by_obj[id(exp.issue)] = exp
        else:
            explanation_by_rule[exp.issue.rule_id] = exp

    # Uppdatera seen_rule med förklaringar
    for rule_id, exp in explanation_by_rule.items():
        seen_rule[rule_id] = exp

    explained = []
    for issue in issues:
        if issue.rule_id in IMAGE_RULES and id(issue) in explanation_by_obj:
            explained.append(explanation_by_obj[id(issue)])
        elif issue.rule_id in seen_rule and seen_rule[issue.rule_id] is not None:
            tmpl = seen_rule[issue.rule_id]
            explained.append(ExplainedIssue(
                issue=issue,
                plain_swedish=tmpl.plain_swedish,
                suggested_fix=tmpl.suggested_fix,
                confidence=tmpl.confidence,
                citations=tmpl.citations,
                retrieved_chunks=tmpl.retrieved_chunks,
            ))

    return explained


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    """Serverar det enkla UI:t för att skriva in URL."""
    ui_path = Path(__file__).parent / "ui.html"
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@app.post("/scan")
async def scan(request: ScanRequest) -> Response:
    """Granskar en enskild URL och returnerar en HTML-rapport."""
    url_str = str(request.url)
    new_run(f"scan {url_str}")
    t0 = time.time()
    log.info("[/scan] %s — provider=%s model=%s", url_str, request.provider, request.model or "(default)")

    try:
        issues, site_logo_b64 = await scan_url(url_str)

        if not issues:
            log.info("[/scan] Inga problem hittades på %s (%.1fs)", url_str, time.time() - t0)
            return Response(
                content=b"Inga problem hittades av axe-core. "
                        b"OBS! Manuell granskning rekommenderas fortfarande.",
                media_type="text/plain; charset=utf-8",
            )

        log.info("[/scan] Förklarar %d problem med AI…", len(issues))
        explained_list = await _explain_issues(issues, request.provider, request.model)
        log.info("[/scan] AI-förklaringar klara — %d unika regler", len(explained_list))

        log.debug("[/scan] Genererar HTML-rapport…")
        _presentation_cache[url_str] = generate_presentation_html(url_str, explained_list)
        html_content = generate_html(url_str, explained_list, site_logo_b64=site_logo_b64)

        filename = f"a11y-rapport-{url_str.replace('https://', '').replace('/', '-')}.html"
        log.info("[/scan] ✓ Klar (%.1fs) — rapport %d KB",
                 time.time() - t0, len(html_content) // 1024)
        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    except Exception as e:
        log.exception("[/scan] Fel under granskning av %s", url_str)
        raise HTTPException(status_code=500, detail=f"Fel under granskning: {e}")


@app.post("/scan-site")
async def scan_site(request: ScanRequest) -> Response:
    """Crawlar sajten, skannar alla sidor och returnerar en samlad rapport."""
    url_str = str(request.url)
    new_run(f"scan-site {url_str}")
    t0 = time.time()
    log.info("[/scan-site] %s — provider=%s model=%s",
             url_str, request.provider, request.model or "(default)")

    try:
        # Steg 1: Crawla och hitta alla sidor
        pages = await crawl(url_str)
        log.info("[/scan-site] Crawl klar: %d sidor hittade", len(pages))

        if not pages:
            log.warning("[/scan-site] Crawl hittade 0 sidor — kan inte fortsätta")
            return Response(
                content=b"Crawl hittade inga sidor. Kontrollera att URL:en svarar och att roboten kan ladda startsidan.",
                media_type="text/plain; charset=utf-8",
                status_code=400,
            )

        # Steg 2: Skanna varje sida (max 3 parallellt)
        log.info("[/scan-site] Skannar %d sidor (max 3 parallellt)…", len(pages))
        semaphore = asyncio.Semaphore(3)
        progress = {"done": 0, "failed": 0}

        async def _scan_page(page_url: str):
            async with semaphore:
                try:
                    res = await scan_url(page_url)
                    progress["done"] += 1
                    log.info("[/scan-site] [%d/%d] ✓ %s",
                             progress["done"] + progress["failed"], len(pages), page_url)
                    return res
                except Exception as exc:
                    progress["failed"] += 1
                    log.warning("[/scan-site] [%d/%d] ✗ %s: %s",
                                progress["done"] + progress["failed"], len(pages), page_url, exc)
                    return [], ""

        results = await asyncio.gather(*[_scan_page(p) for p in pages])

        all_issues = []
        site_logo_b64 = ""
        for issues, logo in results:
            all_issues.extend(issues)
            if logo and not site_logo_b64:
                site_logo_b64 = logo

        log.info("[/scan-site] Skanning klar — %d problem totalt över %d sidor (%d misslyckades)",
                 len(all_issues), len(pages), progress["failed"])

        if not all_issues:
            return Response(
                content=f"Inga problem hittades på {len(pages)} sidor. OBS! Manuell granskning rekommenderas fortfarande.",
                media_type="text/plain; charset=utf-8",
            )

        # Steg 3: AI-förklaring med global deduplicering över alla sidor
        log.info("[/scan-site] Förklarar %d problem med AI (global dedup)…", len(all_issues))
        seen_rule: dict = {}
        image_count: list = [0]
        explained_list = await _explain_issues(
            all_issues, request.provider, request.model,
            seen_rule=seen_rule, image_count=image_count,
        )
        log.info("[/scan-site] AI-förklaringar klara — %d explicita förklaringar",
                 len(explained_list))

        # Steg 4: Generera rapport (med sidantalet i URL-fältet)
        report_label = f"{url_str} ({len(pages)} sidor)"
        log.debug("[/scan-site] Genererar HTML-rapport…")
        _presentation_cache[url_str] = generate_presentation_html(report_label, explained_list)
        html_content = generate_html(report_label, explained_list, site_logo_b64=site_logo_b64)

        filename = f"a11y-sajt-{url_str.replace('https://', '').replace('/', '-')}.html"
        log.info("[/scan-site] ✓ Klar (%.1fs) — rapport %d KB",
                 time.time() - t0, len(html_content) // 1024)
        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    except Exception as e:
        log.exception("[/scan-site] Fel under sajt-granskning av %s", url_str)
        raise HTTPException(status_code=500, detail=f"Fel under sajt-granskning: {e}")


@app.post("/presentation")
async def presentation(request: ScanRequest) -> Response:
    """Returnerar den pre-genererade presentationen från senaste skanningen."""
    url_str = str(request.url)
    html_content = _presentation_cache.get(url_str)
    if not html_content:
        log.warning("[/presentation] Ingen cache hittad för %s", url_str)
        raise HTTPException(
            status_code=404,
            detail="Ingen presentation hittades. Kör en granskning först."
        )
    log.info("[/presentation] Levererar cache för %s (%d KB)", url_str, len(html_content) // 1024)
    filename = f"presentation-{url_str.replace('https://', '').replace('/', '-')}.html"
    return Response(
        content=html_content.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Logg-endpoints så UI:t kan visa scraping-loggen live
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/logs")
async def logs_snapshot(limit: int = 500, since_run: str | None = None) -> JSONResponse:
    """Returnerar en snapshot av de senaste loggraderna."""
    return JSONResponse({"entries": get_buffer(limit=limit, since_run=since_run)})


@app.get("/logs/stream")
async def logs_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events: skickar varje ny loggrad till klienten."""
    loop = asyncio.get_event_loop()
    queue = subscribe(loop)

    # Skicka senaste 100 raderna direkt så användaren ser kontext.
    backlog = get_buffer(limit=100)

    async def event_generator():
        try:
            for entry in backlog:
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            # Heartbeat var 15:e sekund så proxies inte stänger anslutningen.
            while True:
                if await request.is_disconnected():
                    break
                try:
                    entry = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
