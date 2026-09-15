

import os
from pathlib import Path
from typing import List

import chromadb
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

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
    # ── Bilder & media ──────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.1.1",
        "text": "Success Criterion 1.1.1 Non-text Content (Level A): "
                "Allt icke-textinnehåll som presenteras för användaren har "
                "ett textalternativ som tjänar samma syfte. Dekorativa bilder "
                "ska markeras med alt=\"\" så hjälpmedel kan ignorera dem. "
                "Bilder med informationsinnehåll ska ha beskrivande alt-text. "
                "Axe-core regel: image-alt, role-img-alt, svg-img-alt.",
    },
    # ── Struktur & semantik ─────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.3.1",
        "text": "Success Criterion 1.3.1 Info and Relationships (Level A): "
                "Information, struktur och relationer som förmedlas via presentation "
                "kan bestämmas programmatiskt. Rubriker ska märkas h1–h6, listor "
                "som ul/ol/li, tabeller med th/caption. Formulärelement ska ha "
                "associerade label-element. Axe-core: label, list, listitem, "
                "definition-list, table-duplicate-name.",
    },
    {
        "source": "WCAG 2.2", "reference": "1.3.5",
        "text": "Success Criterion 1.3.5 Identify Input Purpose (Level AA): "
                "Syftet med formulärfält som samlar in information om användaren "
                "kan bestämmas programmatiskt. Använd autocomplete-attribut med "
                "korrekt värde, t.ex. autocomplete=\"name\", autocomplete=\"email\". "
                "Axe-core regel: autocomplete-valid.",
    },
    # ── Landmarks & sidstruktur ─────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.3.6",
        "text": "Success Criterion 1.3.6 Identify Purpose (Level AAA): "
                "I innehåll som implementeras med märkspråk kan syftet med "
                "användargränssnittskomponenter, ikoner och regioner bestämmas "
                "programmatiskt. Landmarks som <main>, <nav>, <header>, <footer>, "
                "<aside>, <section> med aria-label hjälper skärmläsare att förstå "
                "sidans struktur.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.1",
        "text": "Success Criterion 2.4.1 Bypass Blocks (Level A): Det finns en "
                "mekanism för att hoppa förbi block av innehåll som upprepas på "
                "flera sidor. Implementeras med skip-links eller korrekt användning "
                "av landmarks: <main> för huvudinnehåll, <nav> för navigering, "
                "<header> och <footer> för sidhuvud/sidfot. Alla regioner av "
                "innehåll bör omges av landmark-element. Axe-core: bypass, "
                "landmark-one-main, region.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.1 – landmark-one-main",
        "text": "Axe-core regel 'landmark-one-main': Sidan saknar ett <main>-landmärke. "
                "Varje sida ska ha exakt ett <main>-element som omsluter sidans "
                "primära innehåll. Skärmläsaranvändare navigerar med landmarks för "
                "att snabbt hoppa till rätt del av sidan. Utan <main> tvingas "
                "användaren lyssna igenom hela sidan. WCAG 2.4.1 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.1 – region",
        "text": "Axe-core regel 'region': Allt sidinnehåll bör finnas inom ett "
                "landmark-element. Landmark-element är: <main>, <nav>, <header> "
                "(som barn till body), <footer> (som barn till body), <aside>, "
                "<section aria-label=\"...\">, <form aria-label=\"...\">, eller "
                "element med ARIA-roller main, navigation, banner, contentinfo, "
                "complementary, region, form, search. Innehåll utanför landmarks "
                "är svårt att hitta för skärmläsaranvändare. WCAG 2.4.1 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.1 – landmark-unique",
        "text": "Axe-core regel 'landmark-unique': Landmark-element av samma typ "
                "måste ha unika tillgängliga namn om de förekommer flera gånger. "
                "Exempel: om sidan har två <nav>-element ska de särskiljas med "
                "aria-label, t.ex. <nav aria-label=\"Huvudnavigering\"> och "
                "<nav aria-label=\"Sidfot-navigering\">. WCAG 2.4.1 (Level A).",
    },
    # ── Rubriker ────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.4.6",
        "text": "Success Criterion 2.4.6 Headings and Labels (Level AA): "
                "Rubriker och etiketter beskriver ämne eller syfte. Rubriknivåer "
                "ska vara logiskt ordnade: h1 för sidans huvudtitel, h2 för "
                "sektioner, h3 för undersektioner. Rubriknivåer ska inte hoppas "
                "över (t.ex. h1 direkt till h3). Axe-core: heading-order, "
                "page-has-heading-one.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.2",
        "text": "Success Criterion 2.4.2 Page Titled (Level A): Webbsidor har "
                "titlar som beskriver ämne eller syfte. Sätt en beskrivande "
                "<title> i HTML-headern. Axe-core: document-title.",
    },
    # ── Färg & kontrast ─────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.4.3",
        "text": "Success Criterion 1.4.3 Contrast (Minimum) (Level AA): "
                "Text och bilder av text ska ha kontrastförhållande minst 4.5:1. "
                "Stor text (18pt/14pt bold) kräver minst 3:1. Undantag: dekorativa "
                "element, logotyper, inaktiva komponenter. Axe-core: color-contrast.",
    },
    {
        "source": "WCAG 2.2", "reference": "1.4.11",
        "text": "Success Criterion 1.4.11 Non-text Contrast (Level AA): "
                "Visuella komponenter i användargränssnittet och grafiska objekt "
                "ska ha kontrastförhållande minst 3:1 mot angränsande färger. "
                "Gäller knappar, fält, fokusindikatorer, ikoner med informations­innehåll.",
    },
    # ── Tangentbord & fokus ─────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.1.1",
        "text": "Success Criterion 2.1.1 Keyboard (Level A): Alla funktioner "
                "är tillgängliga via tangentbord. Interaktiva element måste kunna "
                "nås och aktiveras med Tab, Shift+Tab, Enter och Mellanslag. "
                "Axe-core: keyboard, scrollable-region-focusable.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.3",
        "text": "Success Criterion 2.4.3 Focus Order (Level A): Om en webbsida "
                "kan navigeras sekventiellt och navigeringssekvensen påverkar "
                "mening eller funktion, ska fokuserbara komponenter ta emot fokus "
                "i en ordning som bevarar mening och funktion. Undvik positiva "
                "tabindex-värden. Axe-core: focus-order-semantics, tabindex.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.7",
        "text": "Success Criterion 2.4.7 Focus Visible (Level AA): Alla "
                "tangentbordsfokusbara användargränssnittskomponenter har ett "
                "synligt fokusläge. Ta inte bort outline med outline:none eller "
                "outline:0 utan att ersätta med en tydlig alternativ fokusindikator.",
    },
    # ── Formulär ────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.3.1 – formuläretikett",
        "text": "Axe-core regler 'label', 'select-name': Formulärfält måste ha "
                "ett programmatiskt kopplat etikettnamn. Metoder: "
                "1) <label for=\"id\">Text</label> kopplat till fältets id. "
                "2) aria-label=\"beskrivning\" direkt på fältet. "
                "3) aria-labelledby=\"id-på-etikett\". "
                "Utan etikett vet inte skärmläsare vad fältet handlar om. "
                "WCAG 1.3.1 och 4.1.2 (Level A).",
    },
    # ── ARIA & kod ──────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "4.1.2",
        "text": "Success Criterion 4.1.2 Name, Role, Value (Level A): För alla "
                "användargränssnittskomponenter kan namn och roll bestämmas "
                "programmatiskt. Använd semantisk HTML (button, a, input, select) "
                "eller korrekt ARIA (role, aria-label, aria-expanded, aria-hidden). "
                "Axe-core: button-name, aria-required-attr, aria-roles, "
                "aria-valid-attr, aria-valid-attr-value, aria-hidden-focus.",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.1",
        "text": "Success Criterion 4.1.1 Parsing (Level A): I innehåll som "
                "implementerats med märkspråk har element kompletta start- och "
                "sluttagar, är korrekt nästlade, har inga dubblerade attribut, "
                "och alla id-värden är unika. Axe-core: duplicate-id, "
                "duplicate-id-active, duplicate-id-aria.",
    },
    # ── Språk ────────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "3.1.1",
        "text": "Success Criterion 3.1.1 Language of Page (Level A): Det "
                "mänskliga språket på varje webbsida kan bestämmas programmatiskt. "
                "Sätt lang-attribut på html-elementet, t.ex. <html lang=\"sv\">. "
                "Axe-core: html-has-lang, html-lang-valid.",
    },
    # ── Storlek & zoom ───────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.4.4",
        "text": "Success Criterion 1.4.4 Resize text (Level AA): Text kan "
                "storleksändras upp till 200 procent utan hjälpmedelsteknik utan "
                "att innehåll eller funktionalitet förloras. Undvik att blockera "
                "zoom med <meta name=\"viewport\" content=\"user-scalable=no\">. "
                "Axe-core: meta-viewport.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.5.8",
        "text": "Success Criterion 2.5.8 Target Size (Minimum) (Level AA): "
                "Klickytan för pekarinmatning är minst 24x24 CSS-pixlar. "
                "Undantag: inline-länkar i löptext, element där storleken bestäms "
                "av webbläsaren, och element med tillräckligt avstånd. "
                "Axe-core: target-size.",
    },
    # ── Länkar ───────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.4.4",
        "text": "Success Criterion 2.4.4 Link Purpose (Level A): Syftet med "
                "varje länk kan bestämmas från länktexten ensam eller länktexten "
                "tillsammans med dess programmatiska kontext. Undvik 'klicka här', "
                "'läs mer', 'mer info' utan kontext. Använd aria-label eller "
                "aria-labelledby för att ge länken en unik beskrivning. "
                "Axe-core: link-name.",
    },
    # ── EAA ─────────────────────────────────────────────────────────────────
    {
        "source": "EAA / LPTT", "reference": "Lag 2023:254 §4",
        "text": "Lagen gäller för ekonomiska aktörer som tillhandahåller "
                "produkter eller tjänster på marknaden i Sverige. Tjänster "
                "omfattar: elektronisk kommunikation, audiovisuella medietjänster, "
                "passagerartransporttjänster, banktjänster för konsumenter, "
                "e-böcker samt e-handel.",
    },
    {
        "source": "EAA / LPTT", "reference": "Lag 2023:254 §9",
        "text": "Produkter och tjänster som omfattas av lagen ska uppfylla "
                "tillgänglighetskraven i bilaga I till tillgänglighetsdirektivet "
                "(EU) 2019/882. I praktiken innebär det att webbplatser och "
                "mobila appar ska följa WCAG 2.1 nivå AA.",
    },
    {
        "source": "EAA / LPTT", "reference": "Lag 2023:254 §29",
        "text": "Tillsynsmyndigheten får meddela förelägganden och förbud som "
                "behövs för att lagen ska följas. Beslut får förenas med vite. "
                "Tillsynsmyndighet för e-handel är Post- och telestyrelsen (PTS).",
    },
    {
        "source": "EAA / LPTT", "reference": "Undantag: mikroföretag",
        "text": "Mikroföretag som tillhandahåller tjänster är undantagna. "
                "Mikroföretag: färre än 10 anställda OCH årsomsättning eller "
                "balansomslutning högst 2 miljoner euro.",
    },

    # ── Bilder: fler regler ─────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.1.1 – input-image-alt",
        "text": "Axe-core regel input-image-alt: En <input type='image'> används "
                "som knapp och saknar alt-text. Bilden fungerar som en knapp och "
                "måste ha ett alt-attribut som beskriver knappens syfte, t.ex. "
                "alt='Skicka formulär'. WCAG 1.1.1 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "1.1.1 – svg-img-alt",
        "text": "Axe-core regel svg-img-alt: Ett SVG-element med role='img' saknar "
                "tillgängligt namn. Lägg till <title>Beskrivning</title> som första "
                "barn i SVG-elementet, eller använd aria-label='Beskrivning' på "
                "SVG-elementet. Om SVG:n är dekorativ: aria-hidden='true'. "
                "WCAG 1.1.1 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "1.1.1 – image-redundant-alt",
        "text": "Axe-core regel image-redundant-alt: En bild har alt-text som "
                "exakt upprepar omgivande länk- eller knapptext. Det orsakar att "
                "skärmläsare läser samma information två gånger. Lösning: sätt "
                "alt='' på bilden om länktexten redan beskriver syftet. "
                "WCAG 1.1.1 (Level A).",
    },

    # ── Video & ljud ────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.2.2",
        "text": "Success Criterion 1.2.2 Captions (Prerecorded) (Level A): "
                "Textning tillhandahålls för allt förinspelat ljudinnehåll i "
                "synkroniserade medier. Textning ska inkludera allt tal och "
                "viktiga ljudeffekter. Axe-core: video-caption.",
    },
    {
        "source": "WCAG 2.2", "reference": "1.2.1",
        "text": "Success Criterion 1.2.1 Audio-only and Video-only (Prerecorded) "
                "(Level A): För förinspelat ljud-bara-innehåll tillhandahålls ett "
                "textalternativ. För förinspelat video-bara-innehåll tillhandahålls "
                "ett textalternativ eller en ljudspår. Axe-core: audio-caption.",
    },
    {
        "source": "WCAG 2.2", "reference": "1.2.5",
        "text": "Success Criterion 1.2.5 Audio Description (Prerecorded) (Level AA): "
                "Ljudbeskrivning tillhandahålls för allt förinspelat videoinnehåll "
                "i synkroniserade medier. Axe-core: video-description.",
    },
    {
        "source": "WCAG 2.2", "reference": "1.4.2",
        "text": "Success Criterion 1.4.2 Audio Control (Level A): Om ljud spelas "
                "upp automatiskt i mer än 3 sekunder ska det finnas en mekanism "
                "för att pausa, stoppa eller justera volymen oberoende av systemets "
                "volym. Axe-core: no-autoplay-audio.",
    },

    # ── Kontrast förhöjd ────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.4.6",
        "text": "Success Criterion 1.4.6 Contrast (Enhanced) (Level AAA): "
                "Text och bilder av text ska ha kontrastförhållande minst 7:1. "
                "Stor text (18pt/14pt bold) kräver minst 4.5:1. "
                "Axe-core: color-contrast-enhanced.",
    },

    # ── Timing & animationer ────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.2.1",
        "text": "Success Criterion 2.2.1 Timing Adjustable (Level A): För varje "
                "tidsgräns som sätts av innehållet kan användaren stänga av, "
                "justera eller förlänga tidsgränsen. Gäller sessionstimeouts och "
                "automatiska omdirigeringar. Axe-core: meta-refresh, "
                "timing-adjustable.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.2.2",
        "text": "Success Criterion 2.2.2 Pause, Stop, Hide (Level A): För rörligt, "
                "blinkande, rullande eller automatiskt uppdaterat innehåll finns "
                "en mekanism för att pausa, stoppa eller dölja det. Gäller "
                "karuseller, animationer och automatiskt uppdaterande nyhetsflöden. "
                "Axe-core: pause-stop-hide.",
    },

    # ── Tangentbord: fler regler ────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.1.2",
        "text": "Success Criterion 2.1.2 No Keyboard Trap (Level A): Om "
                "tangentbordsfokus kan flyttas till en komponent med tangentbordet "
                "kan fokus också flyttas bort enbart med tangentbordet. Om det "
                "krävs mer än standard piltangenter/Tab/Escape ska användaren "
                "informeras. Axe-core: no-keyboard-trap.",
    },
    {
        "source": "WCAG 2.2", "reference": "2.4.1 – accesskeys",
        "text": "Axe-core regel accesskeys: Accesskey-värden måste vara unika. "
                "Dubblerade accesskeys kan skapa konflikter i webbläsaren och "
                "göra kortkommandon oförutsägbara för tangentbordsanvändare. "
                "WCAG 2.1.1, 4.1.1 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "2.1.1 – scrollable-region-focusable",
        "text": "Axe-core regler scrollable-region-focusable och "
                "scrolling-region-focusable: Ett element som kan scrollas "
                "(overflow: auto/scroll) är inte nåbart med tangentbordet. "
                "Lägg till tabindex='0' på det scrollbara elementet så att "
                "tangentbordsanvändare kan nå och scrolla innehållet. "
                "WCAG 2.1.1 (Level A).",
    },

    # ── Skip-links ──────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.4.1 – skip-link",
        "text": "Axe-core regel skip-link: En skip-länk hittades men dess mål "
                "finns inte eller är inte fokusbart. Skip-links används för att "
                "hoppa förbi repetitivt navigeringsinnehåll. Länkmålet (t.ex. "
                "#main-content) måste existera och vara fokusbart (ha tabindex='-1' "
                "om det inte är ett naturligt fokusbart element). WCAG 2.4.1 (Level A).",
    },

    # ── Etikett kontra synlig text ──────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.5.3",
        "text": "Success Criterion 2.5.3 Label in Name (Level A): För "
                "användargränssnittskomponenter med etiketter som innehåller text "
                "eller bilder av text ska det tillgängliga namnet innehålla den "
                "synliga texten. En knapp med synlig text 'Skicka' ska ha "
                "aria-label som innehåller 'Skicka', inte ett helt annat ord. "
                "Axe-core: label-content-name-mismatch.",
    },

    # ── Länkändamål ─────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "2.4.9",
        "text": "Success Criterion 2.4.9 Link Purpose (Link Only) (Level AAA): "
                "En mekanism finns tillgänglig som gör det möjligt att identifiera "
                "syftet med varje länk enbart från länktexten. Undvik identiska "
                "länktexter som pekar på olika destinationer. "
                "Axe-core: identical-links-same-purpose.",
    },

    # ── Ramtitlar ───────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – frame-title",
        "text": "Axe-core regel frame-title: Ett <iframe>- eller <frame>-element "
                "saknar ett tillgängligt namnattribut. Lägg till title-attribut "
                "med en beskrivning av ramens innehåll, t.ex. "
                "<iframe title='Inbäddad karta'></iframe>. "
                "WCAG 4.1.2 (Level A).",
    },

    # ── Nästlade interaktiva element ────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – nested-interactive",
        "text": "Axe-core regel nested-interactive: Interaktiva element är "
                "nästlade inuti varandra, t.ex. en knapp inuti en länk eller "
                "en länk inuti en knapp. Detta är ogiltigt HTML och skapar "
                "oförutsägbart beteende för hjälpmedel. Flytta ut det inre "
                "interaktiva elementet. WCAG 4.1.2 (Level A).",
    },

    # ── ARIA-dold body ──────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-hidden-body",
        "text": "Axe-core regel aria-hidden-body: Attributet aria-hidden='true' "
                "har satts på <body>-elementet, vilket döljer hela sidan för "
                "skärmläsare. Ta bort aria-hidden från body-elementet. Om en "
                "modal är öppen, sätt aria-hidden='true' på allt UTOM modalen "
                "med hjälp av en aria-modal-hanterare. WCAG 4.1.2 (Level A).",
    },

    # ── ARIA-roller och attribut ────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-required-children",
        "text": "Axe-core regel aria-required-children: Ett element med en "
                "ARIA-roll saknar obligatoriska barn-element med korrekt roll. "
                "T.ex. role='list' kräver barn med role='listitem', "
                "role='tablist' kräver barn med role='tab'. "
                "WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-required-parent",
        "text": "Axe-core regel aria-required-parent: Ett element med en "
                "ARIA-roll saknar obligatoriskt förälder-element med korrekt roll. "
                "T.ex. role='listitem' kräver förälder med role='list' eller "
                "role='group', role='option' kräver förälder med role='listbox'. "
                "WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-roles",
        "text": "Axe-core regel aria-roles: Ett element har en ARIA-roll som "
                "är ogiltig, abstrakt eller inte tillåten på elementet. Använd "
                "endast giltiga WAI-ARIA-roller (button, link, navigation, main, "
                "dialog, alert, etc.) och se till att rollen passar elementtypen. "
                "WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-valid-attr",
        "text": "Axe-core regel aria-valid-attr: Ett ARIA-attribut används som "
                "inte finns i WAI-ARIA-specifikationen. Kontrollera stavningen "
                "och använd enbart giltiga attribut som aria-label, "
                "aria-labelledby, aria-describedby, aria-expanded, aria-hidden, "
                "aria-live, aria-role, etc. WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-valid-attr-value",
        "text": "Axe-core regel aria-valid-attr-value: Ett ARIA-attribut har "
                "ett ogiltigt värde. T.ex. aria-expanded='yes' är fel "
                "(ska vara 'true'/'false'), aria-labelledby='id-som-inte-finns' "
                "refererar till ett element som inte existerar. "
                "WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-required-attr",
        "text": "Axe-core regel aria-required-attr: Ett element med en ARIA-roll "
                "saknar ett obligatoriskt ARIA-attribut. T.ex. kräver role='checkbox' "
                "attributet aria-checked, role='combobox' kräver aria-expanded. "
                "Lägg till det saknade attributet med ett lämpligt värde. "
                "WCAG 4.1.2 (Level A).",
    },
    {
        "source": "WCAG 2.2", "reference": "4.1.2 – aria-hidden-focus",
        "text": "Axe-core regel aria-hidden-focus: Ett fokusbart element finns "
                "inuti ett element med aria-hidden='true'. Skärmläsare döljer "
                "elementet men tangentbordet kan fortfarande nå det, vilket skapar "
                "förvirring. Antingen: ta bort aria-hidden, lägg till tabindex='-1' "
                "på det fokuserbara elementet, eller flytta det fokuserbara "
                "elementet utanför aria-hidden-området. WCAG 4.1.2 (Level A).",
    },

    # ── Listor ──────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "1.3.1 – definition-list",
        "text": "Axe-core regler definition-list och dlitem: "
                "En <dl>-lista (definitionslista) har ogiltiga barn-element, "
                "eller ett <dt>/<dd>-element används utanför en <dl>. "
                "Strukturen ska vara: <dl><dt>Term</dt><dd>Definition</dd></dl>. "
                "Enbart <dt> och <dd> (och <div> som wrapper) är tillåtna barn. "
                "WCAG 1.3.1 (Level A).",
    },

    # ── Formulär: fler regler ───────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "3.3.2 – form-field-multiple-labels",
        "text": "Axe-core regel form-field-multiple-labels: Ett formulärfält "
                "har flera <label>-element kopplade till sig. Detta kan ge "
                "förvirrande information till skärmläsare. Säkerställ att varje "
                "fält har exakt ett kopplat label-element. "
                "WCAG 3.3.2 (Level A).",
    },

    # ── Språk ────────────────────────────────────────────────────────────────
    {
        "source": "WCAG 2.2", "reference": "3.1.2",
        "text": "Success Criterion 3.1.2 Language of Parts (Level AA): Det "
                "mänskliga språket för varje passage eller fras i innehållet kan "
                "bestämmas programmatiskt, utom för egennamn, tekniska termer och "
                "ord av obestämt språk. Markera textavsnitt på annat språk med "
                "lang-attribut, t.ex. <span lang='en'>Hello</span>. "
                "Axe-core: valid-lang.",
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
