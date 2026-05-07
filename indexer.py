"""
indexer.py

Bygger upp vektordatabasen med WCAG 2.2 och EAA-texter.
Körs EN GÅNG (eller om du byter embedding-provider).

    python indexer.py

Embedding-provider väljs automatiskt:
  OPENAI_API_KEY    → OpenAI text-embedding-3-small   (collection: a11y_laws_openai)
  GEMINI_API_KEY    → Google text-embedding-004        (collection: a11y_laws_gemini)
  ANTHROPIC_API_KEY → lokal ChromaDB-modell (ONNX)    (collection: a11y_laws_local)
"""

import os
from pathlib import Path
from typing import List

import chromadb
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
for env_path in [PROJECT_ROOT / ".env",
                 PROJECT_ROOT / "a11y-audit-rag" / "a11y-audit" / ".env"]:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

_openai_key    = os.getenv("OPENAI_API_KEY", "")
_gemini_key    = os.getenv("GEMINI_API_KEY", "")
_anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

CHROMA_PATH = str(Path(__file__).parent / "chroma_db")


def get_embedding_provider() -> tuple[str, str]:
    """
    Returnerar (provider, collection_name).
    Prioritet: OpenAI → Gemini → Claude (lokal modell).
    """
    if _openai_key:
        return "openai", "a11y_laws_openai"
    if _gemini_key:
        return "gemini", "a11y_laws_gemini"
    if _anthropic_key:
        return "local", "a11y_laws_local"
    raise RuntimeError(
        "Ingen API-nyckel hittades. Lägg till minst en av\n"
        "OPENAI_API_KEY, GEMINI_API_KEY eller ANTHROPIC_API_KEY i .env."
    )


def make_embedding(text: str, provider: str) -> list:
    """Genererar en embedding med vald provider."""
    if provider == "openai":
        from openai import OpenAI
        return OpenAI(api_key=_openai_key).embeddings.create(
            model="text-embedding-3-small", input=text,
        ).data[0].embedding

    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=_gemini_key)
        return genai.embed_content(
            model="models/text-embedding-004",
            content=text,
            task_type="retrieval_document",
        )["embedding"]

    if provider == "local":
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        return DefaultEmbeddingFunction()([text])[0]

    raise RuntimeError(f"Okänd provider: {provider}")

# Hur stora bitar ska vi dela upp texten i?
# För stort = svårare att hitta exakt rätt del
# För litet = förlorar sammanhang
# 800 tecken är en bra medelväg för lagtexter.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100  # Överlapp mellan chunks så vi inte tappar sammanhang


# ---------------------------------------------------------------------------
# Steg 1: Ladda ner dokumenten
# ---------------------------------------------------------------------------

WCAG_URL = "https://www.w3.org/TR/WCAG22/"
# EAA i svensk version finns hos Riksdagen
EAA_URL = "https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/lag-2023254-om-vissa-produkters-och_sfs-2023-254/"


def fetch_wcag() -> str:
    """Hämtar WCAG 2.2 och extraherar texten."""
    try:
        response = httpx.get(WCAG_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # W3C:s huvudinnehåll ligger i <main>
        main = soup.find("main") or soup.find("body")
        return main.get_text(separator="\n", strip=True) if main else ""
    except Exception as e:
        print(f"⚠️  Kunde inte hämta WCAG: {e}")
        return ""


def fetch_eaa() -> str:
    """Hämtar svenska tillgänglighetslagen (Lag 2023:254)."""
    try:
        response = httpx.get(EAA_URL, timeout=30, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        # Riksdagens lagtext ligger oftast i en div med klass "content"
        main = soup.find("div", class_="content") or soup.find("main") or soup.find("body")
        return main.get_text(separator="\n", strip=True) if main else ""
    except Exception as e:
        print(f"⚠️  Kunde inte hämta EAA: {e}")
        return ""


# ---------------------------------------------------------------------------
# Fallback: hårdkodad seed med kärnregler (om nedladdning misslyckas)
# ---------------------------------------------------------------------------

SEED_CHUNKS = [
    {
        "source": "WCAG 2.2",
        "reference": "1.1.1",
        "text": "Success Criterion 1.1.1 Non-text Content (Level A): "
                "Allt icke-textinnehåll som presenteras för användaren har "
                "ett textalternativ som tjänar samma syfte. Undantag: "
                "dekorativa bilder, formatering, osynliga element ska markeras "
                "så hjälpmedel kan ignorera dem (t.ex. tom alt-text alt=\"\").",
    },
    {
        "source": "WCAG 2.2",
        "reference": "1.3.1",
        "text": "Success Criterion 1.3.1 Info and Relationships (Level A): "
                "Information, struktur och relationer som förmedlas via "
                "presentation kan bestämmas programmatiskt eller finns "
                "tillgängliga som text. Rubriker ska märkas som rubriker "
                "(h1-h6), listor som listor (ul/ol/li), tabeller som tabeller.",
    },
    {
        "source": "WCAG 2.2",
        "reference": "1.4.3",
        "text": "Success Criterion 1.4.3 Contrast (Minimum) (Level AA): "
                "Den visuella presentationen av text och bilder av text har "
                "ett kontrastförhållande på minst 4.5:1, förutom för: stor text "
                "(3:1), dekorativa element, och logotyper.",
    },
    {
        "source": "WCAG 2.2",
        "reference": "2.1.1",
        "text": "Success Criterion 2.1.1 Keyboard (Level A): Alla funktioner "
                "är tillgängliga via tangentbord, utan att kräva specifik timing "
                "för enskilda tangenttryckningar. Undantag: när funktionen "
                "kräver input som beror på rörelsens bana.",
    },
    {
        "source": "WCAG 2.2",
        "reference": "2.4.4",
        "text": "Success Criterion 2.4.4 Link Purpose (Level A): Syftet med "
                "varje länk kan bestämmas från länktexten ensam, eller från "
                "länktexten tillsammans med dess programmatiskt bestämda kontext. "
                "Undvik 'klicka här' och 'läs mer' utan kontext.",
    },
    {
        "source": "WCAG 2.2",
        "reference": "3.1.1",
        "text": "Success Criterion 3.1.1 Language of Page (Level A): Det "
                "mänskliga språket på varje webbsida kan bestämmas programmatiskt. "
                "Sätt lang-attribut på html-elementet, t.ex. <html lang=\"sv\">.",
    },
    {
        "source": "WCAG 2.2",
        "reference": "4.1.2",
        "text": "Success Criterion 4.1.2 Name, Role, Value (Level A): För alla "
                "användargränssnittskomponenter, inklusive formulärelement, "
                "länkar och komponenter genererade av skript, kan namn och roll "
                "bestämmas programmatiskt. Använd korrekt HTML (button, a, input) "
                "eller korrekta ARIA-roller.",
    },
    {
        "source": "EAA / LPTT",
        "reference": "Lag 2023:254 §4",
        "text": "Lagen gäller för ekonomiska aktörer som tillhandahåller "
                "produkter eller tjänster som omfattas av lagen på marknaden i "
                "Sverige. Tjänster omfattar bland annat: elektronisk kommunikation, "
                "audiovisuella medietjänster, passagerartransporttjänster, "
                "banktjänster för konsumenter, e-böcker, samt e-handel.",
    },
    {
        "source": "EAA / LPTT",
        "reference": "Lag 2023:254 §9",
        "text": "Produkter och tjänster som omfattas av lagen ska uppfylla "
                "tillgänglighetskraven. Kraven preciseras i bilaga I till "
                "tillgänglighetsdirektivet (EU) 2019/882, och i praktiken "
                "innebär det att webbplatser och mobila appar ska följa "
                "WCAG 2.1 nivå AA.",
    },
    {
        "source": "EAA / LPTT",
        "reference": "Lag 2023:254 §29",
        "text": "Tillsynsmyndigheten får meddela de förelägganden och förbud "
                "som behövs för att lagen ska följas. Ett beslut om föreläggande "
                "eller förbud får förenas med vite. Tillsynsmyndighet för "
                "e-handel är Post- och telestyrelsen (PTS).",
    },
    {
        "source": "EAA / LPTT",
        "reference": "Undantag: mikroföretag",
        "text": "Mikroföretag som tillhandahåller tjänster är undantagna från "
                "kraven. Ett mikroföretag definieras som ett företag som "
                "sysselsätter färre än 10 personer OCH vars årsomsättning eller "
                "balansomslutning inte överstiger 2 miljoner euro.",
    },
]


# ---------------------------------------------------------------------------
# Steg 2: Dela upp text i chunks
# ---------------------------------------------------------------------------

def chunk_text(text: str, source: str) -> List[dict]:
    """
    Delar upp en längre text i små bitar. Vi använder enkel
    tecken-baserad chunking här. För produktion skulle man vilja
    vara smartare (bryta vid meningar/stycken) men detta räcker för MVP.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if len(chunk) > 100:  # Hoppa över för korta chunks
            chunks.append({
                "source": source,
                "reference": f"{source} (del {len(chunks) + 1})",
                "text": chunk,
            })
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


# ---------------------------------------------------------------------------
# Steg 3: Bygg upp databasen
# ---------------------------------------------------------------------------

def build_index():
    """Huvudfunktionen: hämtar, chunkar, embeddar och sparar."""
    provider, collection_name = get_embedding_provider()
    print(f"🔧 Bygger upp tillgänglighetskunskapsbas (embedding-provider: {provider})...")

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    # Ta bort gammal collection om den finns, så vi börjar från noll
    try:
        client.delete_collection(collection_name)
        print("  Gammal databas rensad.")
    except Exception:
        pass

    collection = client.create_collection(
        name=collection_name,
        metadata={"description": "WCAG 2.2 och EAA / LPTT", "provider": provider},
    )

    # Hämta dokumenten
    all_chunks = []

    print("📥 Försöker ladda ner WCAG 2.2...")
    wcag_text = fetch_wcag()
    if wcag_text:
        wcag_chunks = chunk_text(wcag_text, "WCAG 2.2")
        all_chunks.extend(wcag_chunks)
        print(f"   OK: {len(wcag_chunks)} chunks från WCAG")
    else:
        print("   Misslyckades, fortsätter utan full WCAG-text")

    print("📥 Försöker ladda ner EAA (Lag 2023:254)...")
    eaa_text = fetch_eaa()
    if eaa_text:
        eaa_chunks = chunk_text(eaa_text, "EAA / LPTT")
        all_chunks.extend(eaa_chunks)
        print(f"   OK: {len(eaa_chunks)} chunks från EAA")
    else:
        print("   Misslyckades, fortsätter utan full EAA-text")

    # Lägg alltid till seed-chunks som baseline
    print(f"📚 Lägger till {len(SEED_CHUNKS)} seed-chunks som fallback-kunskap")
    all_chunks.extend(SEED_CHUNKS)

    if not all_chunks:
        raise RuntimeError("Inga chunks att indexera!")

    # Skicka till OpenAI för embeddings, en i taget
    # (för enkelhets skull - i produktion skulle vi batcha)
    print(f"🧮 Skapar embeddings för {len(all_chunks)} chunks ({provider})...")
    ids = []
    documents = []
    metadatas = []
    embeddings = []

    for idx, chunk in enumerate(all_chunks):
        print(f"   [{idx + 1}/{len(all_chunks)}] {chunk['reference']}")
        embedding = make_embedding(chunk["text"], provider)
        ids.append(f"chunk-{idx}")
        documents.append(chunk["text"])
        metadatas.append({
            "source": chunk["source"],
            "reference": chunk["reference"],
        })
        embeddings.append(embedding)

    # Spara allt i Chroma
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings,
    )

    print(f"✅ Klart! {len(all_chunks)} chunks indexerade i {CHROMA_PATH} ({collection_name})")
    print(f"   Du kan nu köra: uvicorn main:app --reload")


if __name__ == "__main__":
    build_index()
