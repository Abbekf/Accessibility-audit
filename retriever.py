"""
retriever.py

RAG-sökmotorn. Väljer embedding-provider automatiskt:
  1. OPENAI_API_KEY   → OpenAI text-embedding-3-small
  2. GEMINI_API_KEY   → Google text-embedding-004
  3. ANTHROPIC_API_KEY → lokal ChromaDB-modell (ONNX MiniLM-L6-v2)

Separata collections per provider så att vektorrymden alltid stämmer.
Kör 'python indexer.py' efter att du ändrat vilken nyckel du använder.
"""

import os
from pathlib import Path
from typing import List

import chromadb
from dotenv import load_dotenv
from pydantic import BaseModel

from scanner import A11yIssue


PROJECT_ROOT = Path(__file__).resolve().parent
for env_path in [PROJECT_ROOT / ".env",
                 PROJECT_ROOT / "a11y-audit-rag" / "a11y-audit" / ".env"]:
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)

_openai_key    = os.getenv("OPENAI_API_KEY", "")
_gemini_key    = os.getenv("GEMINI_API_KEY", "")
_anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

CHROMA_PATH = str(Path(__file__).parent / "chroma_db")


class RetrievedChunk(BaseModel):
    source: str
    reference: str
    text: str
    distance: float


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
        "OPENAI_API_KEY, GEMINI_API_KEY eller ANTHROPIC_API_KEY i .env\n"
        "och kör sedan 'python indexer.py'."
    )


def make_embedding(text: str, provider: str) -> List[float]:
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
            task_type="retrieval_query",
        )["embedding"]

    if provider == "local":
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
        return DefaultEmbeddingFunction()([text])[0]

    raise RuntimeError(f"Okänd embedding-provider: {provider}")


_chroma_client = None
_collection = None
_active_provider: str | None = None
_retrieval_warning_printed = False


def _get_collection():
    global _chroma_client, _collection, _active_provider

    provider, collection_name = get_embedding_provider()

    if _collection is None or _active_provider != provider:
        _active_provider = provider
        _chroma_client = chromadb.PersistentClient(path=CHROMA_PATH)

        # Försök hämta provider-specifik collection, fall tillbaka på legacy-namn
        for name in (collection_name, "a11y_laws"):
            try:
                _collection = _chroma_client.get_collection(name)
                return _collection
            except Exception:
                continue

        raise RuntimeError(
            f"Hittar ingen indexerad kunskapsbas för '{provider}'.\n"
            "Kör 'python indexer.py' för att bygga upp den."
        )

    return _collection


def retrieve_relevant_laws(issue: A11yIssue, top_k: int = 3) -> List[RetrievedChunk]:
    global _retrieval_warning_printed

    try:
        provider, _ = get_embedding_provider()
        collection = _get_collection()
    except RuntimeError as exc:
        if not _retrieval_warning_printed:
            print(f"[retriever] Varning: {exc}")
            _retrieval_warning_printed = True
        return []

    try:
        query_embedding = make_embedding(
            f"{issue.wcag_reference} {issue.rule_id} "
            f"{issue.description} {issue.help_text}",
            provider,
        )
    except Exception as exc:
        if not _retrieval_warning_printed:
            print(f"[retriever] Kunde inte skapa embedding ({provider}): {exc}")
            _retrieval_warning_printed = True
        return []

    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)

    chunks = []
    if results["documents"] and results["documents"][0]:
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            chunks.append(RetrievedChunk(
                source=meta.get("source", "Okänd"),
                reference=meta.get("reference", ""),
                text=doc,
                distance=dist,
            ))
    return chunks
