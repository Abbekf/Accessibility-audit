"""
explainer.py

Språkmotorn, nu RAG-förstärkt.

Ny arkitektur:
1. Ta in ett fel från axe-core (fakta)
2. Slå upp relevant WCAG/EAA-text i vektordatabasen (retriever)
3. Skicka BÅDE felet OCH lagtexten till Claude
4. Claude får instruktioner att ENDAST använda den tillhandahållna texten
5. Returnera förklaring, förslag, och källhänvisningar

Skillnaden mot tidigare: Claude hittar aldrig på WCAG-regler från minnet.
Allt den säger om regler är baserat på den text vi hämtat från databasen.
Detta är garantin mot hallucinationer.
"""

import os
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv

from scanner import A11yIssue
from retriever import retrieve_relevant_laws, RetrievedChunk


load_dotenv()

_api_key = os.getenv("OPENAI_API_KEY")
if not _api_key or _api_key.startswith("sk-proj-EXAMPLE"):
    raise RuntimeError(
        "OPENAI_API_KEY saknas. Skapa en .env-fil (kopiera .env.example) "
        "och klistra in din nyckel från https://platform.openai.com/api-keys"
    )

client = AsyncOpenAI(api_key=_api_key)


class Citation(BaseModel):
    """En källhänvisning som LLM:en använt i sitt svar."""
    source: str        # "WCAG 2.2" eller "EAA / LPTT"
    reference: str     # T.ex. "1.1.1"


class ExplainedIssue(BaseModel):
    """Ett fel med AI-genererad förklaring plus källhänvisningar."""
    issue: A11yIssue
    plain_swedish: str
    suggested_fix: str
    confidence: str          # "high", "medium", "low"
    citations: List[Citation]  # Vilka källor som användes
    retrieved_chunks: List[RetrievedChunk]  # För transparens i rapporten


# Den här prompten är NOGGRANT skriven för att tvinga fram källbaserade svar.
# Varje regel här är med av en anledning.
SYSTEM_PROMPT = """Du är en assistent som förklarar tillgänglighetsproblem \
för webbutvecklare, baserat på officiella WCAG- och EAA-dokument.

Du kommer få:
1. Ett tekniskt fel hittat av axe-core (detta är FAKTA)
2. 2-3 utdrag från WCAG 2.2 och/eller EAA som är relevanta för felet (din enda KUNSKAPSKÄLLA)

STRIKTA REGLER du MÅSTE följa:
1. Använd ENDAST information från de utdrag som tillhandahålls. Hitta INTE på \
något utanför dessa texter.
2. Om informationen i utdragen inte räcker för att förklara felet ordentligt, \
säg det tydligt istället för att gissa.
3. Citera alltid vilken källa och referens du stödjer dig på.
4. Skriv förklaringen på enkel, pedagogisk svenska.
5. Förslag ska vara KONKRETA med kodexempel där det är relevant.
6. Om förslaget är en gissning (t.ex. en alt-text du hittat på), markera det tydligt.

Svara ALLTID i exakt detta format, inget annat:

FÖRKLARING: <2-3 meningar på svenska>
FÖRSLAG: <konkret förslag, gärna med kodexempel>
KÄLLOR: <komma-separerad lista med exakta referenser du använt, t.ex. "WCAG 2.2 §1.1.1, EAA §9">
SÄKERHET: <high, medium, eller low>"""


async def explain_issue(issue: A11yIssue) -> ExplainedIssue:
    """
    Förklarar ett fel med hjälp av RAG-pipelinen.
    """
    # Steg 1: Hämta relevanta lagparagrafer från vektordatabasen
    retrieved = retrieve_relevant_laws(issue, top_k=3)

    # Steg 2: Bygg kontext-blocket med de hämtade texterna
    if retrieved:
        context_block = "\n\n".join([
            f"[KÄLLA {i + 1}: {chunk.source} — {chunk.reference}]\n{chunk.text}"
            for i, chunk in enumerate(retrieved)
        ])
    else:
        context_block = "(Ingen relevant lagtext hittades i kunskapsbasen.)"

    # Steg 3: Bygg användarprompten
    user_prompt = f"""FEL HITTAT AV AXE-CORE:

Regel-ID: {issue.rule_id}
WCAG-referens enligt axe: {issue.wcag_reference}
Allvarlighetsgrad: {issue.impact}
Beskrivning (engelska): {issue.description}
Hjälptext (engelska): {issue.help_text}
Berörd HTML: {issue.affected_html[:500]}
CSS-selektor: {issue.selector}

RELEVANTA UTDRAG FRÅN WCAG / EAA:

{context_block}

Förklara problemet och föreslå en fix. Baserat ENDAST på utdragen ovan."""

    # Steg 4: Anropa OpenAI
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=700,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    response_text = response.choices[0].message.content
    explanation, fix, citations, confidence = _parse_response(response_text)

    return ExplainedIssue(
        issue=issue,
        plain_swedish=explanation,
        suggested_fix=fix,
        confidence=confidence,
        citations=citations,
        retrieved_chunks=retrieved,
    )


def _parse_response(text: str) -> tuple[str, str, List[Citation], str]:
    """Plockar isär LLM:ens strukturerade svar."""
    explanation = ""
    fix = ""
    citations: List[Citation] = []
    confidence = "medium"

    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("FÖRKLARING:"):
            explanation = line.replace("FÖRKLARING:", "").strip()
        elif line.startswith("FÖRSLAG:"):
            fix = line.replace("FÖRSLAG:", "").strip()
        elif line.startswith("KÄLLOR:"):
            raw = line.replace("KÄLLOR:", "").strip()
            # Parsear t.ex. "WCAG 2.2 §1.1.1, EAA §9"
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                # Enkel heuristik: allt före första siffran är källan,
                # resten är referensen
                if "WCAG" in part.upper():
                    citations.append(Citation(source="WCAG 2.2", reference=part))
                elif "EAA" in part.upper() or "LPTT" in part.upper() or "2023:254" in part:
                    citations.append(Citation(source="EAA / LPTT", reference=part))
                else:
                    citations.append(Citation(source="Övrig", reference=part))
        elif line.startswith("SÄKERHET:"):
            value = line.replace("SÄKERHET:", "").strip().lower()
            if value in ("high", "medium", "low"):
                confidence = value

    if not explanation:
        explanation = "Kunde inte generera förklaring."
    if not fix:
        fix = "Manuell granskning rekommenderas."

    return explanation, fix, citations, confidence
