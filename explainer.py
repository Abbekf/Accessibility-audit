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
from pathlib import Path
from openai import AsyncOpenAI
from pydantic import BaseModel
from typing import List
from dotenv import load_dotenv

from scanner import A11yIssue
from retriever import retrieve_relevant_laws, RetrievedChunk


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_PATHS = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "a11y-audit-rag" / "a11y-audit" / ".env",
]
for env_path in ENV_PATHS:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

_openai_key  = os.getenv("OPENAI_API_KEY", "")
_claude_key  = os.getenv("ANTHROPIC_API_KEY", "")
_gemini_key  = os.getenv("GEMINI_API_KEY", "")

_openai_client = AsyncOpenAI(api_key=_openai_key) if _openai_key else None

DEFAULT_MODEL_BY_PROVIDER = {
    "openai": "gpt-4o",
    "claude": "claude-haiku-4-5-20251001",
    "gemini": "gemini-2.5-flash",
}


def _available_providers() -> list[str]:
    providers: list[str] = []
    if _openai_key:
        providers.append("openai")
    if _claude_key:
        providers.append("claude")
    if _gemini_key:
        providers.append("gemini")
    return providers


def _resolve_provider_and_model(provider: str, model: str) -> tuple[str, str]:
    """Väljer en fungerande provider och en kompatibel modell."""
    requested = (provider or "").strip().lower() or "auto"
    available = _available_providers()

    if not available:
        raise RuntimeError(
            "Ingen AI-nyckel hittades. Sätt minst en av OPENAI_API_KEY, "
            "ANTHROPIC_API_KEY eller GEMINI_API_KEY i .env."
        )

    if requested in ("openai", "claude", "gemini") and requested in available:
        resolved_provider = requested
        resolved_model = model or DEFAULT_MODEL_BY_PROVIDER[resolved_provider]
        return resolved_provider, resolved_model

    # Fallback-prioritet: Claude, Gemini, OpenAI.
    for candidate in ("claude", "gemini", "openai"):
        if candidate in available:
            if requested in ("openai", "claude", "gemini") and requested != candidate:
                print(
                    f"[explainer] Varning: provider '{requested}' saknar nyckel. "
                    f"Byter till '{candidate}'."
                )
            return candidate, DEFAULT_MODEL_BY_PROVIDER[candidate]

    # Teoretiskt onåbar eftersom vi redan hanterat tom lista.
    raise RuntimeError("Kunde inte välja en AI-provider.")


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


# Bildregler där vision-analys ger konkret nytta
IMAGE_RULES = {
    "image-alt", "input-image-alt", "role-img-alt",
    "svg-img-alt", "image-redundant-alt",
}

# Den här prompten är NOGGRANT skriven för att tvinga fram källbaserade svar.
SYSTEM_PROMPT = """Du är en assistent som förklarar tillgänglighetsproblem \
för webbutvecklare, baserat på officiella WCAG- och EAA-dokument.

Du kommer få:
1. Ett tekniskt fel hittat av axe-core (detta är FAKTA)
2. 2-3 utdrag från WCAG 2.2 och/eller EAA som är relevanta för felet (din enda KUNSKAPSKÄLLA)
3. Ibland: en skärmdump av det berörda elementet — använd den för att ge ett KONKRET förslag

STRIKTA REGLER du MÅSTE följa:
1. Använd ENDAST information från de utdrag som tillhandahålls. Hitta INTE på \
något utanför dessa texter.
2. Om informationen i utdragen inte räcker för att förklara felet ordentligt, \
säg det tydligt istället för att gissa.
3. Citera alltid vilken källa och referens du stödjer dig på.
4. Skriv förklaringen på enkel, pedagogisk svenska.
5. Förslag ska vara KONKRETA med kodexempel där det är relevant.
6. Om du ser en skärmdump av en bild: avgör om bilden är DEKORATIV eller INNEHÅLLSBÄRANDE \
och ge ett specifikt förslag. Markera tydligt om det är din bedömning.

Svara ALLTID i exakt detta format, inget annat:

FÖRKLARING: <2-3 meningar på svenska>
FÖRSLAG: <konkret förslag, gärna med kodexempel>
KÄLLOR: <komma-separerad lista med exakta referenser du använt, t.ex. "WCAG 2.2 §1.1.1, EAA §9">
SÄKERHET: <high, medium, eller low>"""


async def explain_issue(issue: A11yIssue, provider: str = "openai", model: str = "gpt-4o") -> ExplainedIssue:
    """
    Förklarar ett fel med hjälp av RAG-pipelinen.
    För bildregler med skärmdump används vision för konkret analys.
    """
    retrieved = retrieve_relevant_laws(issue, top_k=3)

    if retrieved:
        context_block = "\n\n".join([
            f"[KÄLLA {i + 1}: {chunk.source} — {chunk.reference}]\n{chunk.text}"
            for i, chunk in enumerate(retrieved)
        ])
    else:
        context_block = "(Ingen relevant lagtext hittades i kunskapsbasen.)"

    use_vision = issue.rule_id in IMAGE_RULES and bool(issue.screenshot_b64)

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

{"Se skärmdumpen av det berörda elementet bifogad nedan. Avgör om bilden verkar vara DEKORATIV (använd alt='') eller INNEHÅLLSBÄRANDE (föreslå en konkret alt-text baserad på vad du ser). Markera att det är din visuella bedömning." if use_vision else ""}
Förklara problemet och föreslå en fix. Baserat ENDAST på utdragen ovan."""

    resolved_provider, resolved_model = _resolve_provider_and_model(provider, model)

    if resolved_provider == "claude":
        response_text = await _call_claude(resolved_model, user_prompt, screenshot_b64=issue.screenshot_b64 if use_vision else "")
    elif resolved_provider == "gemini":
        response_text = await _call_gemini(resolved_model, user_prompt, screenshot_b64=issue.screenshot_b64 if use_vision else "")
    else:
        response_text = await _call_openai(resolved_model, user_prompt, screenshot_b64=issue.screenshot_b64 if use_vision else "")

    explanation, fix, citations, confidence = _parse_response(response_text)

    return ExplainedIssue(
        issue=issue,
        plain_swedish=explanation,
        suggested_fix=fix,
        confidence=confidence,
        citations=citations,
        retrieved_chunks=retrieved,
    )


async def _call_openai(model: str, user_prompt: str, screenshot_b64: str = "") -> str:
    if not _openai_client:
        raise RuntimeError("OPENAI_API_KEY saknas i .env")
    content: list = [{"type": "text", "text": user_prompt}]
    if screenshot_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{screenshot_b64}", "detail": "low"},
        })
    response = await _openai_client.chat.completions.create(
        model=model,
        max_tokens=700,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
    )
    return response.choices[0].message.content


async def _call_claude(model: str, user_prompt: str, screenshot_b64: str = "") -> str:
    if not _claude_key:
        raise RuntimeError("ANTHROPIC_API_KEY saknas i .env")
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=_claude_key)
    content: list = [{"type": "text", "text": user_prompt}]
    if screenshot_b64:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": screenshot_b64},
        })
    response = await client.messages.create(
        model=model,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return response.content[0].text


async def _call_gemini(model: str, user_prompt: str, screenshot_b64: str = "") -> str:
    if not _gemini_key:
        raise RuntimeError("GEMINI_API_KEY saknas i .env")
    import google.generativeai as genai
    import base64 as _base64
    genai.configure(api_key=_gemini_key)
    gemini_model = genai.GenerativeModel(
        model_name=model,
        system_instruction=SYSTEM_PROMPT,
    )
    parts: list = [user_prompt]
    if screenshot_b64:
        parts.append({
            "mime_type": "image/png",
            "data": _base64.b64decode(screenshot_b64),
        })
    loop = __import__('asyncio').get_event_loop()
    response = await loop.run_in_executor(
        None,
        lambda: gemini_model.generate_content(parts),
    )
    return response.text


def _parse_response(text: str) -> tuple[str, str, List[Citation], str]:
    """
    Plockar isär LLM:ens strukturerade svar.
    Hanterar både enrads- och flerrads-svar — Haiku skriver ofta innehållet
    på nästa rad efter etiketten.
    """
    import re

    explanation = ""
    fix = ""
    citations: List[Citation] = []
    confidence = "medium"

    _LABELS = ["FÖRKLARING", "FÖRSLAG", "KÄLLOR", "SÄKERHET"]
    pattern = (
        r'(' + '|'.join(_LABELS) + r')\s*:\s*'
        r'(.*?)'
        r'(?=(?:' + '|'.join(_LABELS) + r')\s*:|$)'
    )
    matches = re.findall(pattern, text, flags=re.DOTALL | re.IGNORECASE)

    for label, content in matches:
        content = content.strip()
        label_upper = label.upper()

        if label_upper == "FÖRKLARING":
            explanation = content
        elif label_upper == "FÖRSLAG":
            fix = content
        elif label_upper == "KÄLLOR":
            for part in content.split(","):
                part = part.strip()
                if not part:
                    continue
                if "WCAG" in part.upper():
                    citations.append(Citation(source="WCAG 2.2", reference=part))
                elif "EAA" in part.upper() or "LPTT" in part.upper() or "2023:254" in part:
                    citations.append(Citation(source="EAA / LPTT", reference=part))
                else:
                    citations.append(Citation(source="Övrig", reference=part))
        elif label_upper == "SÄKERHET":
            value = content.lower().split()[0] if content else ""
            if value in ("high", "medium", "low"):
                confidence = value

    if not explanation:
        explanation = "Kunde inte generera förklaring."
    if not fix:
        fix = "Manuell granskning rekommenderas."

    return explanation, fix, citations, confidence
