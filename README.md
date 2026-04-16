# Tillgänglighetsrevisor (MVP + RAG)

Ett AI-assisterat verktyg som granskar webbsidor mot WCAG 2.2 och EAA,
med källhänvisningar direkt från lagtexten.

## Arkitektur

```
Användare  →  FastAPI  →  Playwright + axe-core (hittar fel)
                              ↓
                         retriever.py → ChromaDB (hittar relevant lagtext)
                              ↓
                         Claude (förklarar på svenska, ENDAST från källan)
                              ↓
                         WeasyPrint → PDF-rapport med citations
```

- **axe-core** hittar tekniska fel (fakta, inte AI)
- **ChromaDB + OpenAI embeddings** = RAG-pipeline mot WCAG 2.2 och EAA
- **Claude** förklarar felen på svenska, baserat på hämtad lagtext
- **WeasyPrint** genererar PDF med källhänvisningar
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

Kopiera `.env.example` till `.env` och fyll i två nycklar:

- **ANTHROPIC_API_KEY** från https://console.anthropic.com
- **OPENAI_API_KEY** från https://platform.openai.com/api-keys

```bash
cp .env.example .env
# Öppna .env och fyll i dina nycklar
```

### 4. Bygg upp kunskapsbasen (görs EN gång)

Detta laddar ner WCAG och EAA-texterna och bygger upp vektordatabasen.
Det tar ett par minuter och kostar några öre i OpenAI-avgifter.

```bash
python indexer.py
```

Du bör se output som:
```
📥 Försöker ladda ner WCAG 2.2...
   OK: 127 chunks från WCAG
📥 Försöker ladda ner EAA (Lag 2023:254)...
   OK: 45 chunks från EAA
📚 Lägger till 11 seed-chunks
🧮 Skapar embeddings...
✅ Klart! 183 chunks indexerade
```

Även om nedladdningarna krånglar fungerar det ändå tack vare fallback-seed,
men då blir täckningen mindre.

### 5. Starta servern

```bash
uvicorn main:app --reload
```

Servern kör nu på http://localhost:8000

### 6. Testa

Öppna http://localhost:8000/docs i din webbläsare.
Skicka in en URL via det interaktiva gränssnittet.

Eller via curl:

```bash
curl -X POST http://localhost:8000/scan \
     -H "Content-Type: application/json" \
     -d '{"url": "https://www.delorean.se"}' \
     --output rapport.pdf
```

## Vad RAG tillför

Innan RAG: Claude förklarar tillgänglighetsfel från sitt minne. Risk för
att blanda ihop WCAG-regler eller hitta på paragrafer.

Med RAG: Claude får officiell text från WCAG och EAA tillsammans med
varje fel, och får instruktion att ENDAST använda den texten som källa.
Varje förklaring kommer med exakta källhänvisningar.

Det är skillnaden mellan en konsult som pratar ur minnet och en konsult
som slår upp i lagboken.

## Filstruktur

- `scanner.py` — Browsar sidan och kör axe-core (fakta-motor)
- `indexer.py` — Bygger upp vektordatabasen från WCAG + EAA (kör en gång)
- `retriever.py` — Slår upp relevant lagtext i databasen
- `explainer.py` — Skickar fel + lagtext till Claude (RAG-pipeline)
- `reporter.py` — Genererar PDF-rapport
- `main.py` — FastAPI-server som binder ihop allt
- `templates/report.html` — Mallen för PDF-rapporten
- `chroma_db/` — Vektor-databasen (skapas av indexer.py)

## Vad är nästa steg?

- [ ] Crawla flera sidor per sajt, inte bara startsidan
- [ ] Bild-alt-förslag baserat på faktiskt bildinnehåll (vision-modell)
- [ ] Ett frontend-gränssnitt istället för bara API
- [ ] Databas för historik och trendanalys
- [ ] Kontinuerlig övervakning (schemalagd scanning)

## Webbaserat användargränssnitt (UI)

Du kan nu använda verktyget direkt i webbläsaren:

1. Starta servern:

```bash
uvicorn main:app --reload
```

2. Öppna http://localhost:8000/ i din webbläsare.

### Funktioner i UI:t
- **Skriv in valfri URL** och starta granskning direkt från sidan.
- **Se PDF-rapporten** direkt i webbläsaren.
- **Ladda ner PDF** eller **visa i helskärm** med ett klick.
- **Spara rapporter** lokalt i webbläsaren och återöppna dem under fliken "Sparade rapporter".

> **OBS!** Varje granskning använder OpenAI:s API och kan innebära en liten kostnad per körning.

### API och CLI
Du kan fortfarande använda `/scan`-endpointen direkt eller via t.ex. curl om du vill automatisera tester.
