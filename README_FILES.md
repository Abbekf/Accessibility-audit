
# Utförlig fil- och mappöversikt för a11y-audit

Den här filen ger en detaljerad beskrivning av alla viktiga filer och mappar i projektet, inklusive deras syfte, beroenden och hur de samverkar i tillgänglighetsrevisorn.

---

## Pythonmoduler

### main.py
**Roll:** Huvudapplikation och startpunkt. Skapar ett FastAPI-objekt och exponerar API:et.

**Viktiga endpoints:**
- `/` – Serverar ui.html (webbgränssnittet)
- `/scan` – Tar emot en URL, kör hela granskningsflödet och returnerar en PDF-rapport

**Beroenden:**
- scanner.py (för att hitta fel)
- explainer.py (för att förklara fel med AI och lagtext)
- reporter.py (för att generera HTML/PDF-rapport)
- favicon_route.py (för favicon)

**Kommentar:** Binder ihop hela flödet. Hanterar fel och ser till att alla steg körs i rätt ordning.

---

### scanner.py
**Roll:** "Faktamotor". Kör Playwright (Chromium) för att ladda webbsidan, injicerar axe-core och samlar in tillgänglighetsfel.

**Steg:**
1. Startar Chromium i headless-läge
2. Navigerar till användarens URL
3. Laddar ner och injicerar axe-core från CDN
4. Kör axe.run() och samlar in alla "violations"
5. Returnerar en lista av A11yIssue-objekt (med bl.a. WCAG-referens, beskrivning, CSS-selektor, HTML-snutt, ev. skärmdump)

**Viktigt:**
- All data här är FAKTA från axe-core, ingen AI eller gissning.
- Playwright körs i separat tråd på Windows för kompatibilitet.

**Beroenden:**
- playwright, axe-core (via CDN), pydantic

---

### explainer.py
**Roll:** "Språkmotor". Tar varje fel från scanner.py, hämtar relevant lagtext från vektordatabasen (via retriever.py) och skickar både felet och lagtexten till Claude (OpenAI/Anthropic) för förklaring på svenska.

**Steg:**
1. Tar emot ett A11yIssue
2. Anropar retriever.py för att hämta 2–3 relevanta textpassager ur WCAG/EAA
3. Skickar både felet och lagtexten till Claude (via OpenAI API)
4. Returnerar en ExplainedIssue med förklaring, förslag, källhänvisningar och transparens kring vilka lagtexter som användes

**Viktigt:**
- Prompten är strikt: Claude får ENDAST använda tillhandahållen lagtext, aldrig hitta på egna regler
- Förslagen är alltid konkreta och på enkel svenska

**Beroenden:**
- openai, pydantic, retriever.py, scanner.py

---

### reporter.py
**Roll:** Sätter ihop allt till en läsbar rapport (HTML och PDF).

**Steg:**
1. Tar emot en lista av ExplainedIssue-objekt
2. Renderar rapporten med Jinja2 och report.html-mallen
3. Konverterar HTML till PDF med WeasyPrint
4. Returnerar PDF:en som bytes

**Detaljer:**
- Hanterar även kategorisering av fel, generering av etiketter, och säker HTML-escaping

**Beroenden:**
- jinja2, weasyprint, markupsafe, explainer.py

---

### retriever.py
**Roll:** "Sökmotor" i RAG-pipelinen. Tar emot ett fel, bygger en embedding och söker i ChromaDB efter de mest relevanta lagtexterna.

**Steg:**
1. Tar emot ett A11yIssue
2. Bygger en sökfråga av felbeskrivningen
3. Skickar till OpenAI för att skapa en embedding
4. Söker i ChromaDB efter närmaste textpassager
5. Returnerar dessa till explainer.py

**Viktigt:**
- Om ingen databas finns, får man ett tydligt felmeddelande

**Beroenden:**
- chromadb, openai, pydantic, scanner.py

---

### indexer.py
**Roll:** Bygger upp vektordatabasen (ChromaDB) med WCAG 2.2 och EAA-texter. Körs EN gång vid setup.

**Steg:**
1. Laddar ner WCAG 2.2 från W3C
2. Laddar ner EAA (Lag 2023:254) från riksdagen
3. Delar upp texterna i "chunks" om ca 800 tecken
4. Skickar varje chunk till OpenAI för embedding
5. Sparar allt i ChromaDB
6. Har fallback med seed-chunks om nätet krånglar

**Beroenden:**
- chromadb, openai, httpx, beautifulsoup4

---

### favicon_route.py
**Roll:** Hanterar /favicon.ico och returnerar en 1x1 transparent PNG för att slippa 404-fel i browsern.

**Beroenden:**
- fastapi

---

## Övriga filer

### requirements.txt
Lista på alla Pythonpaket som behövs för att köra projektet.

### .env
API-nycklar för OpenAI och Anthropic (Claude). Ska **aldrig** delas publikt.

### README.md
Huvuddokumentation, installationsguide och arkitekturbeskrivning.

### ui.html
Webbaserat användargränssnitt. Gör det möjligt att köra granskningar direkt i webbläsaren, visa rapporter, ladda ner och spara dem lokalt.

---

## Mappar

### templates/
Innehåller HTML-mallar för rapportgenerering.
- **report.html** – Själva rapportmallen. Används av reporter.py och renderas med Jinja2.

### chroma_db/
Innehåller vektordatabasen (ChromaDB) och metadata.
- **chroma.sqlite3** – Själva databasen med embeddings och textpassager.
- (UUID-mappar) – Metadata och indexfiler för ChromaDB.

### __pycache__/
Python-cachefiler. Kan ignoreras och är med i .gitignore.

---

## Övrigt

### test-rapport.pdf
Exempel på genererad rapport. Kan tas bort.

### .gitignore
Ignorerar cache, miljöfiler, databaser och API-nycklar i versionshantering.

---

## Hur allt hänger ihop

1. **main.py** tar emot en URL via API eller UI
2. **scanner.py** kör axe-core på sidan och hittar fel
3. **explainer.py** förklarar varje fel med hjälp av **retriever.py** (hämtar lagtext ur **chroma_db/**)
4. **reporter.py** bygger en rapport (HTML/PDF) med hjälp av **templates/report.html**
5. Resultatet visas i webbläsaren (**ui.html**) eller returneras via API

Alla beroenden och steg är dokumenterade i respektive modul.

---

För mer detaljer, se huvud-README.md och källkoden för varje modul.
