
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path

from scanner import scan_url
from explainer import explain_issue
from reporter import generate_html, generate_presentation_html
from crawler import crawl
from favicon_route import router as favicon_router


app = FastAPI(
    title="Tillgänglighetsrevisor",
    description="Ett AI-assisterat verktyg som granskar webbsidor mot WCAG.",
)
app.include_router(favicon_router)


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

    try:
        issues, site_logo_b64 = await scan_url(url_str)

        if not issues:
            return Response(
                content=b"Inga problem hittades av axe-core. "
                        b"OBS! Manuell granskning rekommenderas fortfarande.",
                media_type="text/plain; charset=utf-8",
            )

        explained_list = await _explain_issues(issues, request.provider, request.model)

        _presentation_cache[url_str] = generate_presentation_html(url_str, explained_list)
        html_content = generate_html(url_str, explained_list, site_logo_b64=site_logo_b64)

        filename = f"a11y-rapport-{url_str.replace('https://', '').replace('/', '-')}.html"
        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Fel under granskning: {e}")


@app.post("/scan-site")
async def scan_site(request: ScanRequest) -> Response:
    """Crawlar sajten, skannar alla sidor och returnerar en samlad rapport."""
    url_str = str(request.url)

    try:
        # Steg 1: Crawla och hitta alla sidor
        pages = await crawl(url_str)

        # Steg 2: Skanna varje sida (max 3 parallellt)
        semaphore = asyncio.Semaphore(3)

        async def _scan_page(page_url: str):
            async with semaphore:
                try:
                    issues, logo = await scan_url(page_url)
                    return issues, logo
                except Exception:
                    return [], ""

        results = await asyncio.gather(*[_scan_page(p) for p in pages])

        all_issues = []
        site_logo_b64 = ""
        for issues, logo in results:
            all_issues.extend(issues)
            if logo and not site_logo_b64:
                site_logo_b64 = logo

        if not all_issues:
            return Response(
                content=f"Inga problem hittades på {len(pages)} sidor. OBS! Manuell granskning rekommenderas fortfarande.",
                media_type="text/plain; charset=utf-8",
            )

        # Steg 3: AI-förklaring med global deduplicering över alla sidor
        seen_rule: dict = {}
        image_count: list = [0]
        explained_list = await _explain_issues(
            all_issues, request.provider, request.model,
            seen_rule=seen_rule, image_count=image_count,
        )

        # Steg 4: Generera rapport (med sidantalet i URL-fältet)
        report_label = f"{url_str} ({len(pages)} sidor)"
        _presentation_cache[url_str] = generate_presentation_html(report_label, explained_list)
        html_content = generate_html(report_label, explained_list, site_logo_b64=site_logo_b64)

        filename = f"a11y-sajt-{url_str.replace('https://', '').replace('/', '-')}.html"
        return Response(
            content=html_content.encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(tb)
        raise HTTPException(status_code=500, detail=f"Fel under sajt-granskning: {e}\n\n{tb}")


@app.post("/presentation")
async def presentation(request: ScanRequest) -> Response:
    """Returnerar den pre-genererade presentationen från senaste skanningen."""
    url_str = str(request.url)
    html_content = _presentation_cache.get(url_str)
    if not html_content:
        raise HTTPException(
            status_code=404,
            detail="Ingen presentation hittades. Kör en granskning först."
        )
    filename = f"presentation-{url_str.replace('https://', '').replace('/', '-')}.html"
    return Response(
        content=html_content.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
