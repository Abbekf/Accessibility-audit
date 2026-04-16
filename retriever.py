"""
retriever.py

Detta är "sökmotorn" i vår RAG-pipeline.

Vad den gör:
1. Tar emot ett tillgänglighetsfel
2. Konverterar felet till en embedding (sifferlista)
3. Söker i ChromaDB efter de chunks som ligger närmast
4. Returnerar de mest relevanta textpassagerna

Resultatet används sedan av explainer.py för att bygga en prompt
med korrekt lagtext som kontext.
"""

import os
from pathlib import Path
from typing import List

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from scanner import A11yIssue


load_dotenv()

openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

CHROMA_PATH = str(Path(__file__).parent / "chroma_db")
COLLECTION_NAME = "a11y_laws"
EMBEDDING_MODEL = "text-embedding-3-small"


class RetrievedChunk(BaseModel):
    """En hämtad textbit från kunskapsbasen."""
    source: str        # T.ex. "WCAG 2.2" eller "EAA / LPTT"
    reference: str     # T.ex. "1.1.1" eller "Lag 2023:254 §9"
    text: str          # Själva texten
    distance: float    # Hur nära (0 = identiskt, större = mindre relevant)


# Vi återanvänder samma client mellan anrop, det är snabbare
_client = None
_collection = None


def _get_collection():
    """Öppnar databasen första gången, återanvänder den sedan."""
    global _client, _collection
    if _collection is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH)
        try:
            _collection = _client.get_collection(COLLECTION_NAME)
        except Exception:
            raise RuntimeError(
                "Hittar ingen indexerad kunskapsbas. "
                "Kör 'python indexer.py' först för att bygga upp den."
            )
    return _collection


def _make_query(issue: A11yIssue) -> str:
    """
    Bygger en sökfråga från felet. Vi kombinerar flera fält för
    att få en rikare semantisk signal. Bara rule_id räcker inte.
    """
    return (
        f"{issue.wcag_reference} {issue.rule_id} "
        f"{issue.description} {issue.help_text}"
    )


def retrieve_relevant_laws(issue: A11yIssue, top_k: int = 3) -> List[RetrievedChunk]:
    """
    Hittar de top_k mest relevanta textpassagerna för ett givet fel.
    Default är 3, vilket brukar vara en bra balans mellan täckning
    och promptstorlek.
    """
    collection = _get_collection()

    # Bygg sökfrågan och konvertera till embedding
    query_text = _make_query(issue)
    query_embedding = openai_client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=query_text,
    ).data[0].embedding

    # Sök i databasen. Chroma returnerar de närmaste enligt cosinus-likhet
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
    )

    # Packa ihop resultaten till en snygg lista av RetrievedChunk
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
