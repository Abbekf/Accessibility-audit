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

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, HTMLResponse
from pydantic import BaseModel, HttpUrl
from pathlib import Path

from scanner import scan_url
from explainer import explain_issue
from reporter import generate_html
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
    provider: str = "openai"
    model: str = "gpt-4o"




@app.get("/", response_class=HTMLResponse)
def serve_ui():
    """Serverar det enkla UI:t för att skriva in URL."""
    ui_path = Path(__file__).parent / "ui.html"
    return HTMLResponse(ui_path.read_text(encoding="utf-8"))


@app.post("/scan")
async def scan(request: ScanRequest) -> Response:
    """
    Granskar en URL och returnerar en PDF-rapport.
    """
    url_str = str(request.url)

    try:
        # Steg 1: Hitta felen (fakta, ingen AI inblandad här)
        issues = await scan_url(url_str)

        if not issues:
            # Returnera 200 med ett meddelande istället för en tom PDF
            # Det är sällsynt men möjligt att en sida är helt OK
            return Response(
                content=b"Inga problem hittades av axe-core. "
                        b"OBS! Manuell granskning rekommenderas fortfarande.",
                media_type="text/plain; charset=utf-8",
            )

        # Steg 2: Förklara varje unik rule_id med AI (en förklaring per regeltyp)
        # Många sidor har t.ex. 30 bilder utan alt-text — samma förklaring gäller alla.
        seen: dict = {}
        unique_issues = []
        for issue in issues:
            if issue.rule_id not in seen:
                seen[issue.rule_id] = None
                unique_issues.append(issue)

        explanations_list = await asyncio.gather(*[
            explain_issue(issue, provider=request.provider, model=request.model)
            for issue in unique_issues[:50]
        ])
        explanation_by_rule = {e.issue.rule_id: e for e in explanations_list}

        # Bygg en ExplainedIssue per originalinstans men återanvänd AI-förklaringen
        from explainer import ExplainedIssue
        explained = []
        for issue in issues:
            template = explanation_by_rule[issue.rule_id]
            explained.append(ExplainedIssue(
                issue=issue,
                plain_swedish=template.plain_swedish,
                suggested_fix=template.suggested_fix,
                confidence=template.confidence,
                citations=template.citations,
                retrieved_chunks=template.retrieved_chunks,
            ))

        # Steg 3: Generera HTML-rapport
        html_content = generate_html(url_str, list(explained))

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
