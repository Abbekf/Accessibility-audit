
# Fil- och mappöversikt för a11y-audit

Detaljerad beskrivning av alla viktiga filer och mappar, deras syfte, beroenden och hur de samverkar.

---

## Pythonmoduler

### main.py
**Roll:** Huvudapplikation och startpunkt. Skapar ett FastAPI-objekt och exponerar API:et.

**Viktiga endpoints:**
- `/` – Serverar ui.html (webbgränssnittet)
- `/scan` – Tar emot URL, provider och modell, kör hela granskningsflödet och returnerar en HTML-rapport

**Flöde:**
1. Tar emot `url`, `provider` och `model` från klienten
2. Kör `scan_url()` för att hitta fel med axe-core
3. Deduplicerar issues per `rule_id` — förklarar varje regeltyp EN gång och återanvänder förklaringen för alla instanser (sparar API-kostnader)
4. Kör `explain_issue()` parallellt för unika regeltyper
5. Genererar HTML-rapport via `generate_html()`

**Beroenden:** scanner.py, explainer.py, reporter.py, favicon_route.py

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
- `OPENAI_API_KEY` krävs alltid för RAG-embeddings, oavsett vald LLM

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
3. Skapar en embedding via OpenAI
4. Söker i ChromaDB efter närmaste textpassager
5. Returnerar `RetrievedChunk`-objekt till explainer.py

**Beroenden:** chromadb, openai, pydantic, scanner.py

---

### indexer.py
**Roll:** Bygger upp vektordatabasen (ChromaDB) med WCAG 2.2 och EAA-texter. Körs EN gång vid setup.

**Steg:**
1. Laddar ner WCAG 2.2 från W3C
2. Laddar ner EAA (Lag 2023:254) från riksdagen.se
3. Delar upp texterna i chunks (~800 tecken med 100 teckens överlapp)
4. Skickar varje chunk till OpenAI för embedding
5. Sparar allt i ChromaDB
6. Har fallback med seed-chunks om nätet krånglar

**Beroenden:** chromadb, openai, httpx, beautifulsoup4

---

### favicon_route.py
**Roll:** Hanterar `/favicon.ico` och returnerar en 1×1 transparent PNG för att undvika 404-fel i webbläsaren.

**Beroenden:** fastapi

---

## Övriga filer

### requirements.txt
Lista på alla Python-paket som behövs. Viktiga paket:
- `playwright` — headless browser för axe-core
- `openai` — embeddings och GPT-modeller
- `anthropic` — Claude-modeller
- `google-generativeai` — Gemini-modeller
- `chromadb` — vektordatabas för RAG
- `fastapi` + `uvicorn` — webb-API
- `jinja2` — HTML-mallrendering

### .env
API-nycklar. Ska **aldrig** checkas in i versionshantering.
- `OPENAI_API_KEY` — krävs alltid (embeddings + valfritt GPT)
- `ANTHROPIC_API_KEY` — krävs för Claude
- `GEMINI_API_KEY` — krävs för Gemini

### ui.html
Webbaserat gränssnitt med:
- URL-inmatningsfält
- Dropdown för LLM-provider (OpenAI / Claude / Gemini)
- Dropdown för specifik modell (uppdateras dynamiskt per provider)
- Rapport visas inbäddad i sidan
- Knappar för helskärm, ladda ner och spara

---

## Mappar

### templates/
- **report.html** — Interaktiv HTML-rapportmall. Innehåller sidebar, filter, sökning, skärmdumpar och utvecklarverktyg (kopiera selektor/konsolkommando).

### chroma_db/
Vektordatabasen skapad av `indexer.py`. Innehåller embeddings och textpassager från WCAG 2.2 och EAA.

### __pycache__/
Python-cachefiler. Ignoreras av git.

---

## Hur allt hänger ihop

```
ui.html (användaren väljer URL + LLM)
    ↓ POST /scan {url, provider, model}
main.py
    ↓
scanner.py → axe-core → A11yIssue[] (med kontextskärmdumpar)
    ↓ deduplicering per rule_id
explainer.py (en förklaring per regeltyp)
    ├── retriever.py → chroma_db/ (hämtar WCAG/EAA-text)
    └── openai / claude / gemini (förklarar på svenska)
    ↓
reporter.py → templates/report.html (HTML med utvecklarverktyg)
    ↓
HTML-rapport tillbaka till ui.html
```
