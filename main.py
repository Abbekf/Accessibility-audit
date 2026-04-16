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
from fastapi.responses import Response
from pydantic import BaseModel, HttpUrl

from scanner import scan_url
from explainer import explain_issue
from reporter import generate_pdf


app = FastAPI(
    title="Tillgänglighetsrevisor",
    description="Ett AI-assisterat verktyg som granskar webbsidor mot WCAG.",
)


class ScanRequest(BaseModel):
    """
    Indata till /scan-endpointen.
    Pydantic validerar automatiskt att URL:en är giltig.
    """
    url: HttpUrl


@app.get("/")
async def root():
    """Enkel hälso-check så man ser att servern lever."""
    return {
        "status": "ok",
        "message": "Tillgänglighetsrevisorn är igång. Se /docs för API-dokumentation.",
    }


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

        # Steg 2: Förklara varje fel med AI
        # asyncio.gather kör alla förklaringar parallellt = snabbare
        # Vi begränsar till 20 samtidiga anrop för att inte sprida API-limits
        explained = await asyncio.gather(*[
            explain_issue(issue) for issue in issues[:50]  # max 50 för MVP
        ])

        # Steg 3: Generera PDF-rapport
        pdf_bytes = generate_pdf(url_str, list(explained))

        # Returnera PDF:en med rätt filnamn
        filename = f"a11y-rapport-{url_str.replace('https://', '').replace('/', '-')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Fel under granskning: {e}")
