
# Fil- och mappöversikt för a11y-audit

Detaljerad beskrivning av alla viktiga filer och mappar, deras syfte, beroenden och hur de samverkar.

---

## Pythonmoduler

### main.py
**Roll:** Huvudapplikation och startpunkt. Skapar ett FastAPI-objekt och exponerar API:et.

**Viktiga endpoints:**
- `/` – Serverar ui.html (webbgränssnittet)
- `/scan` – Tar emot URL, provider och modell, kör hela granskningsflödet för en enskild sida och returnerar en HTML-rapport
- `/scan-site` – Crawlar hela sajten, skannar alla sidor och returnerar en samlad rapport
- `/presentation` – Returnerar den pre-genererade presentationen från senaste skanningen

**Flöde:**
1. Tar emot `url`, `provider` och `model` från klienten
2. Kör `scan_url()` för att hitta fel med axe-core
3. Deduplicerar issues per `rule_id` — förklarar varje regeltyp EN gång och återanvänder förklaringen för alla instanser (sparar API-kostnader)
4. Kör `explain_issue()` parallellt för unika regeltyper
5. Genererar HTML-rapport via `generate_html()`

**Beroenden:** scanner.py, explainer.py, reporter.py, crawler.py, favicon_route.py

---

### scanner.py
**Roll:** "Faktamotor". Kör Playwright (Chromium) för att ladda webbsidan, injicerar axe-core och samlar in tillgänglighetsfel.

**Steg:**
1. Startar Chromium i headless-läge
2. Navigerar till användarens URL (väntar på `networkidle`)
3. Injicerar axe-core från CDN
4. Kör `axe.run()` och samlar in alla "violations"
5. För varje violation: scrollar till elementet, lägger på röd outline, tar en viewport-skärmdump (kontextbild), tar bort outline
6. Returnerar en lista av `A11yIssue`-objekt

**Viktigt:**
- All data är FAKTA från axe-core — ingen AI eller gissning
- Skärmdumpar visar elementet markerat i sin omgivning på sidan (inte isolerat)
- Playwright körs i separat tråd på Windows (ProactorEventLoop) för kompatibilitet med uvicorn

**Beroenden:** playwright, axe-core (via CDN), pydantic

---

### explainer.py
**Roll:** "Språkmotor". Tar varje fel, hämtar relevant lagtext via RAG och skickar till vald LLM för förklaring på svenska.

**Steg:**
1. Tar emot ett `A11yIssue` + `provider` + `model`
2. Anropar `retriever.py` för att hämta 2–3 relevanta textpassager ur WCAG/EAA
3. Skickar felet + lagtexten till vald LLM
4. Returnerar `ExplainedIssue` med förklaring, förslag, källhänvisningar och hämtade lagtexter

**Stödda providers:**
- `openai` — via `AsyncOpenAI` (kräver `OPENAI_API_KEY`)
- `claude` — via `anthropic.AsyncAnthropic` (kräver `ANTHROPIC_API_KEY`)
- `gemini` — via `google.generativeai` i executor (kräver `GEMINI_API_KEY`)

**Svarsparsning:**
- `_parse_response()` använder regex med `re.DOTALL` för att hantera både enrads- och flerrads-svar från olika modeller
- Extraherar `FÖRKLARING`, `FÖRSLAG`, `KÄLLOR` och `SÄKERHET`

**Viktigt:**
- Prompten är strikt: LLM:en får ENDAST använda tillhandahållen lagtext
- RAG används alltid — systemet väljer automatiskt embedding-provider baserat på tillgängliga API-nycklar

**Beroenden:** openai, anthropic, google-generativeai, pydantic, retriever.py, scanner.py

---

### reporter.py
**Roll:** Sätter ihop allt till en interaktiv HTML-rapport.

**Steg:**
1. Tar emot en lista av `ExplainedIssue`-objekt
2. Grupperar issues per `rule_id` (en kortare förklaring per regeltyp, alla element visas)
3. Kategoriserar per DIGG-kategori (Bilder, Formulär, Tangentbord, etc.)
4. Renderar rapporten med Jinja2 och `report.html`-mallen
5. Returnerar HTML som sträng

**Detaljer:**
- `_describe_element()` genererar mänskligt läsbar etikett per element (filnamn för bilder, länktext, knapptext, etc.)
- `_truncate_html()` kortar ner långa HTML-strängar och tar bort base64-data
- `_md_to_html()` konverterar markdown-kodblock från LLM-svar till HTML
- Varje element i rapporten har: "Kopiera selektor", "Kopiera konsolkommando", "Visa selektor"
- CSS-selektorn lagras i `data-selector`-attribut för säker hantering av specialtecken

**Beroenden:** jinja2, markupsafe, explainer.py

---

### retriever.py
**Roll:** "Sökmotor" i RAG-pipelinen. Söker i ChromaDB efter lagtexter relevanta för ett givet fel.

**Steg:**
1. Tar emot ett `A11yIssue`
2. Bygger en sökfråga av felbeskrivningen
3. Väljer embedding-provider automatiskt baserat på tillgängliga API-nycklar (OpenAI → Gemini → lokal ONNX)
4. Söker i ChromaDB i den provider-specifika collectionen
5. Returnerar `RetrievedChunk`-objekt till explainer.py

**Embedding-providers:**
- `openai` → `text-embedding-3-small`, collection `a11y_laws_openai`
- `gemini` → `text-embedding-004`, collection `a11y_laws_gemini`
- `local` → ONNX MiniLM-L6-v2 (inbyggd i chromadb, kräver ingen extra nyckel), collection `a11y_laws_local`

**Viktigt:** Om du byter vilken API-nyckel du använder måste du köra om `python indexer.py`
så att vektordatabasen byggs om med samma embedding-modell som retriever använder.

**Beroenden:** chromadb, openai (valfritt), google-generativeai (valfritt), pydantic, scanner.py

---

### indexer.py
**Roll:** Bygger upp vektordatabasen (ChromaDB) med WCAG 2.2 och EAA-texter. Körs EN gång vid setup, eller när du byter API-nyckel.

**Steg:**
1. Laddar ner WCAG 2.2 från W3C
2. Laddar ner EAA (Lag 2023:254) från riksdagen.se
3. Delar upp texterna i chunks (~800 tecken med 100 teckens överlapp)
4. Väljer embedding-provider automatiskt (OpenAI → Gemini → lokal ONNX)
5. Skapar embeddings och sparar i provider-specifik ChromaDB-collection
6. Har fallback med 55 seed-chunks om nätet krånglar — täcker alla axe-core-regler

**Embedding-providers:** samma prioritetsordning och collections som retriever.py.

**Beroenden:** chromadb, openai (valfritt), google-generativeai (valfritt), httpx, beautifulsoup4

---

### crawler.py
**Roll:** Crawlar en hel sajt och samlar in alla unika interna sidor för granskning.

**Steg:**
1. Startar från en rot-URL
2. Följer interna länkar rekursivt (samma domän)
3. Undviker dubbletter och externa URL:er
4. Returnerar en lista med alla hittade sidor

Används av `/scan-site`-endpointen i main.py för att granska hela sajter på en gång.

**Beroenden:** playwright (eller httpx), pydantic

---

### favicon_route.py
**Roll:** Hanterar `/favicon.ico` och returnerar en 1×1 transparent PNG för att undvika 404-fel i webbläsaren.

**Beroenden:** fastapi

---

## Övriga filer

### requirements.txt
Lista på alla Python-paket som behövs. Viktiga paket:
- `playwright` — headless browser för axe-core
- `openai` — embeddings (valfritt) och GPT-modeller
- `anthropic` — Claude-modeller
- `google-generativeai` — Gemini-modeller och embeddings (valfritt)
- `chromadb` — vektordatabas för RAG (inkluderar lokal ONNX-embeddingmodell)
- `fastapi` + `uvicorn` — webb-API
- `jinja2` — HTML-mallrendering

### .env
API-nycklar. Ska **aldrig** checkas in i versionshantering.
- `OPENAI_API_KEY` — för OpenAI GPT-modeller och OpenAI-embeddings (valfritt)
- `ANTHROPIC_API_KEY` — för Claude-modeller; lokal ONNX-embedding används automatiskt om bara denna nyckel finns
- `GEMINI_API_KEY` — för Gemini-modeller och Gemini-embeddings (valfritt)

> Du behöver bara **en** nyckel. Systemet väljer rätt embedding-provider automatiskt.
> Om du byter nyckel: kör om `python indexer.py`.

### ui.html
Webbaserat gränssnitt med:
- URL-inmatningsfält
- Knappar för att granska en sida eller crawla hela sajten
- Dropdown för LLM-provider (OpenAI / Claude / Gemini)
- Dropdown för specifik modell (uppdateras dynamiskt per provider)
- Rapport visas inbäddad i sidan
- Knappar för att skapa rapport (PDF) och presentation

---

## Mappar

### templates/
- **report.html** — Interaktiv HTML-rapportmall. Innehåller sidebar, filter, sökning, skärmdumpar och utvecklarverktyg (kopiera selektor/konsolkommando).
- **presentation.html** — Presentationsmall för slidshow-vy av granskningsresultaten.

### chroma_db/
Vektordatabasen skapad av `indexer.py`. Innehåller embeddings och textpassager från WCAG 2.2 och EAA.
Innehåller separata collections per embedding-provider (`a11y_laws_openai`, `a11y_laws_gemini`, `a11y_laws_local`).

### __pycache__/
Python-cachefiler. Ignoreras av git.

---

## Hur allt hänger ihop

```
ui.html (användaren väljer URL + LLM)
    ↓ POST /scan eller /scan-site {url, provider, model}
main.py
    ├── crawler.py (vid /scan-site: crawlar alla sidor)
    ↓
scanner.py → axe-core → A11yIssue[] (med kontextskärmdumpar)
    ↓ deduplicering per rule_id
explainer.py (en förklaring per regeltyp)
    ├── retriever.py → chroma_db/ (hämtar WCAG/EAA-text via auto-vald embedding)
    └── openai / claude / gemini (förklarar på svenska)
    ↓
reporter.py → templates/report.html (HTML med utvecklarverktyg)
    ↓
HTML-rapport tillbaka till ui.html
```
