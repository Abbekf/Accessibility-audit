# Tillgänglighetsrevisor

Ett AI-assisterat verktyg som granskar webbsidor mot WCAG 2.2 och EAA,
med källhänvisningar direkt från lagtexten.

## Arkitektur

```
Användare  →  FastAPI  →  Playwright + axe-core (hittar fel)
                              ↓
                         retriever.py → ChromaDB (hittar relevant lagtext)
                              ↓
                         OpenAI / Claude / Gemini (förklarar på svenska, ENDAST från källan)
                              ↓
                         Interaktiv HTML-rapport med statushantering och PDF-export
```

- **axe-core** hittar tekniska fel (fakta, inte AI)
- **ChromaDB + auto-vald embedding (OpenAI / Gemini / lokal)** = RAG-pipeline mot WCAG 2.2 och EAA
- **Valfri LLM** (OpenAI, Claude eller Gemini) förklarar felen på svenska, baserat på hämtad lagtext
- **HTML-rapport** med källhänvisningar, kontextskärmdumpar och utvecklarverktyg
- **FastAPI** exponerar allt som ett enkelt webb-API

## Kom igång

### 1. Förutsättningar

- Python 3.11+
- Node.js (krävs av Playwright)

### 2. Installera paket

```bash
python3 -m venv venv
source venv/bin/activate    # På Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 3. Sätt upp API-nycklar

Kopiera `.env.example` till `.env` och fyll i minst **en** av dessa nycklar:

- **OPENAI_API_KEY** från https://platform.openai.com/api-keys
- **ANTHROPIC_API_KEY** från https://console.anthropic.com
- **GEMINI_API_KEY** från https://aistudio.google.com

```bash
cp .env.example .env
# Öppna .env och fyll i dina nycklar
```

> **OBS!** Du behöver bara **en** nyckel för att komma igång. Systemet väljer
> automatiskt embedding-provider i prioritetsordning: OpenAI → Gemini → lokal (ONNX, ingen nyckel krävs om du har `ANTHROPIC_API_KEY`).
> Om du byter vilken nyckel du använder måste du köra om `python indexer.py`
> så att vektordatabasen byggs om med rätt embedding-modell.

### 4. Bygg upp kunskapsbasen (görs EN gång, eller om du byter nyckel)

Detta laddar ner WCAG- och EAA-texterna och bygger upp vektordatabasen.

```bash
python indexer.py
```

Du bör se output som:

```
📥 Försöker ladda ner WCAG 2.2...
   OK: 127 chunks från WCAG
📥 Försöker ladda ner EAA (Lag 2023:254)...
   OK: 45 chunks från EAA
📚 Lägger till 55 seed-chunks
🧮 Skapar embeddings...
✅ Klart! 227 chunks indexerade
```

Även om nedladdningarna krånglar fungerar det ändå tack vare de 55 seed-chunksarna
som täcker alla axe-core-regler, men då blir täckningen av hela lagtexten mindre.

### 5. Starta servern

```bash
uvicorn main:app --reload
```

Servern kör nu på http://localhost:8000

### 6. Testa

Öppna http://localhost:8000/ i din webbläsare, skriv in en URL och välj LLM.

Eller via curl:

```bash
curl -X POST http://localhost:8000/scan \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.delorean.se", "provider": "claude", "model": "claude-haiku-4-5-20251001"}' \
     --output rapport.html
```

## Välja LLM i gränssnittet

I headern finns två dropdowns: **leverantör** och **modell**.
Standardvalet är **Claude Haiku 4.5** — snabb och kostnadseffektiv för de flesta granskningar.

| Leverantör | Tillgängliga modeller |
|------------|----------------------|
| OpenAI | GPT-4o, GPT-4o mini, GPT-4.1, GPT-4.1 mini |
| Claude | Claude Sonnet 4.6, Claude Opus 4.7, Claude Haiku 4.5 |
| Gemini | Gemini 2.5 Flash, Gemini 2.5 Pro, Gemini 2.0 Flash |

**Rekommendation:** Claude Haiku 4.5 eller GPT-4o mini är snabba och billiga och fungerar bra för de flesta fel.
Claude Sonnet 4.6 eller GPT-4o ger djupare förklaringar och bättre kodexempel.

## Vad RAG tillför

Utan RAG: LLM:en förklarar tillgänglighetsfel från sitt minne — risk för
att blanda ihop WCAG-regler eller hitta på paragrafer.

Med RAG: LLM:en får officiell text från WCAG 2.2 och EAA tillsammans med
varje fel, och instrueras att ENDAST använda den texten som källa.
Varje förklaring kommer med exakta källhänvisningar.

Det är skillnaden mellan en konsult som pratar ur minnet och en konsult
som slår upp i lagboken.

## Vad rapporten innehåller

- **Dashboard** — sammanfattning med antal fel per allvarlighetsnivå och en framstegsindikator
- **Kategorisering** enligt DIGG:s kategorier (Bilder, Formulär, Tangentbord, etc.)
- **Allvarlighetsnivå** (Kritisk, Allvarlig, Måttlig, Liten)
- **AI-förklaring** på svenska med källhänvisning till WCAG/EAA
- **Konkret åtgärdsförslag** med kodexempel
- **Kontextskärmdump** — visar elementet markerat med röd ram i sin omgivning på sidan
- **Vision-analys för bilder** — AI:n bedömer om bilden är dekorativ eller innehållsbärande och föreslår en konkret alt-text
- **Kopiera selektor** — CSS-selektor för att hitta elementet i DevTools
- **Kopiera konsolkommando** — ett JS-kommando att köra i F12-konsolen som scrollar till och markerar elementet direkt på sidan

### Statushantering

Varje fel och varje enskilt element kan markeras med en av tre statusar:

| Status | Meaning |
|--------|---------|
| *(ingen)* | Ännu inte åtgärdat |
| ✅ Åtgärdad | Problemet är fixat |
| 🚫 Ej relevant | Medvetet val att inte åtgärda |

Statusarna sparas automatiskt i webbläsaren (localStorage) per granskad URL,
och kvarstår även om sidan laddas om.

Dashboarden och sidomenyn uppdateras i realtid när du markerar fel — t.ex.
"29 / 30" visar hur många som är kvar av det ursprungliga antalet.

### PDF-export

Klicka **Skapa rapport** i headern för att öppna webbläsarens utskriftsdialog.
Välj "Spara som PDF" för att exportera hela rapporten.

Rapporten i PDF-format visar alla fel inklusive de markerade som åtgärdade
och ej relevanta, med tydliga statusbadgar för varje.

### Hitta ett fel direkt på sidan

Varje element i rapporten har knappar som hjälper dig lokalisera felet:

#### Kopiera selektor
Kopierar en CSS-selektor som unikt identifierar elementet, t.ex. `div.hero > img:nth-child(2)`.

**Så här använder du den:**
1. Klicka "Kopiera selektor" på elementet i rapporten
2. Öppna den granskade sidan i webbläsaren
3. Tryck F12 → fliken **Elements**
4. Tryck Ctrl+F (sök i HTML-trädet)
5. Klistra in selektorn — webbläsaren hoppar direkt till rätt HTML-element

#### Kopiera konsolkommando
Kopierar ett JavaScript-kommando som scrollar till elementet och ritar en röd ram runt det direkt på sidan.

**Så här använder du det:**
1. Klicka "Kopiera konsolkommando" på elementet i rapporten
2. Öppna den granskade sidan i webbläsaren
3. Tryck F12 → fliken **Console**
4. Om webbläsaren visar en varning: skriv `allow pasting` och tryck Enter (görs en gång per session i Edge/Chrome)
5. Klistra in kommandot och tryck Enter
6. Sidan scrollar automatiskt till elementet och markerar det med röd ram

> **Tips:** Konsolkommandot är snabbast när du vill se elementet visuellt på sidan.
> Selektorn är bäst när du vill hitta och redigera HTML-koden direkt i DevTools.

## Filstruktur

- `scanner.py` — Browsar sidan och kör axe-core, tar kontextskärmdumpar
- `indexer.py` — Bygger upp vektordatabasen från WCAG + EAA (kör en gång, eller om du byter API-nyckel)
- `retriever.py` — Slår upp relevant lagtext i ChromaDB (auto-väljer embedding-provider)
- `explainer.py` — Skickar fel + lagtext till vald LLM (OpenAI / Claude / Gemini)
- `reporter.py` — Genererar HTML-rapport med kategorisering och utvecklarverktyg
- `crawler.py` — Crawlar en hel sajt och samlar in alla sidor för granskning
- `main.py` — FastAPI-server som binder ihop allt
- `ui.html` — Webbaserat gränssnitt med LLM-väljare
- `templates/report.html` — Mallen för den interaktiva HTML-rapporten
- `chroma_db/` — Vektordatabasen (skapas av indexer.py)

## Vad är nästa steg?

- [x] Crawla flera sidor per sajt, inte bara startsidan
- [ ] Databas för historik och trendanalys
- [ ] Kontinuerlig övervakning (schemalagd scanning)
