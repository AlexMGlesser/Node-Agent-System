"""
storage.py  —  Local vector database for the Linux node.

Manages four ChromaDB collections:
  - documents : personal notes and text files
  - code      : source code files
  - web       : scraped web content
  - memory    : conversation history between sessions

Embeddings are generated locally via Ollama (nomic-embed-text).
ChromaDB stores everything on disk under ./chroma_db/.

Requirements:
  pip install chromadb requests
  Ollama must be running:  ollama serve
  Embedding model pulled:  ollama pull nomic-embed-text
"""

import hashlib
import json
import os
from datetime import datetime
from typing import Optional

import chromadb
import requests

# ── Configuration ─────────────────────────────────────────────────────────────

OLLAMA_URL      = "http://localhost:11434"
EMBED_MODEL     = "nomic-embed-text"
CHROMA_DIR      = os.path.join(os.path.dirname(__file__), "chroma_db")
TOP_K           = 5     # number of results returned by default for each query
MAX_CHUNK_CHARS = 1500  # characters per text chunk when indexing long files

# Collection names
COL_DOCUMENTS = "documents"
COL_CODE      = "code"
COL_WEB       = "web"
COL_MEMORY    = "memory"

# ── Initialise ChromaDB ───────────────────────────────────────────────────────

_client: Optional[chromadb.PersistentClient] = None


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        os.makedirs(CHROMA_DIR, exist_ok=True)
        _client = chromadb.PersistentClient(path=CHROMA_DIR)
    return _client


def _get_collection(name: str) -> chromadb.Collection:
    return _get_client().get_or_create_collection(name=name)


# ── Embedding ─────────────────────────────────────────────────────────────────

def embed(text: str) -> list[float]:
    """
    Generate an embedding vector for `text` using the local Ollama model.
    Raises RuntimeError if Ollama is unreachable.
    """
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            "Cannot reach local Ollama for embeddings. "
            "Is 'ollama serve' running and 'nomic-embed-text' pulled?"
        ) from exc


# ── Text chunking ─────────────────────────────────────────────────────────────

def _chunk(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """
    Split `text` into overlapping chunks of at most `max_chars` characters.
    Tries to break on paragraph boundaries first, then sentence boundaries.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    paragraphs = text.split("\n\n")
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= max_chars:
            current = current + "\n\n" + para if current else para
        else:
            if current:
                chunks.append(current.strip())
            # Paragraph itself is longer than limit — split on sentences
            if len(para) > max_chars:
                for i in range(0, len(para), max_chars):
                    chunks.append(para[i : i + max_chars])
                current = ""
            else:
                current = para
    if current:
        chunks.append(current.strip())
    return [c for c in chunks if c]


def _doc_id(source: str, chunk_index: int) -> str:
    base = hashlib.sha256(source.encode()).hexdigest()[:16]
    return f"{base}_{chunk_index}"


# ── Add / remove documents ────────────────────────────────────────────────────

def add_document(
    text: str,
    source: str,
    metadata: Optional[dict] = None,
) -> int:
    """
    Index a text document (notes, plain-text files, PDFs converted to text).
    Returns the number of chunks stored.
    """
    col = _get_collection(COL_DOCUMENTS)
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        doc_id = _doc_id(source, i)
        meta = {
            "source": source,
            "chunk": i,
            "total_chunks": len(chunks),
            "indexed_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        col.upsert(
            ids=[doc_id],
            embeddings=[embed(chunk)],
            documents=[chunk],
            metadatas=[meta],
        )
    return len(chunks)


def add_code_file(
    text: str,
    filepath: str,
    language: str = "",
    metadata: Optional[dict] = None,
) -> int:
    """
    Index a source code file.
    Returns the number of chunks stored.
    """
    col = _get_collection(COL_CODE)
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        doc_id = _doc_id(filepath, i)
        meta = {
            "filepath": filepath,
            "language": language,
            "chunk": i,
            "total_chunks": len(chunks),
            "indexed_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        col.upsert(
            ids=[doc_id],
            embeddings=[embed(chunk)],
            documents=[chunk],
            metadatas=[meta],
        )
    return len(chunks)


def add_web_page(
    text: str,
    url: str,
    title: str = "",
    metadata: Optional[dict] = None,
) -> int:
    """
    Index scraped web content.
    Returns the number of chunks stored.
    """
    col = _get_collection(COL_WEB)
    chunks = _chunk(text)
    for i, chunk in enumerate(chunks):
        doc_id = _doc_id(url, i)
        meta = {
            "url": url,
            "title": title,
            "chunk": i,
            "total_chunks": len(chunks),
            "indexed_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }
        col.upsert(
            ids=[doc_id],
            embeddings=[embed(chunk)],
            documents=[chunk],
            metadatas=[meta],
        )
    return len(chunks)


def save_memory(
    user_message: str,
    assistant_message: str,
    session_id: str = "default",
) -> None:
    """
    Persist a conversation turn to the memory collection so past context can
    be retrieved in future sessions.
    """
    col = _get_collection(COL_MEMORY)
    ts = datetime.utcnow().isoformat()
    turn_text = f"User: {user_message}\nAssistant: {assistant_message}"
    turn_id = hashlib.sha256(f"{session_id}_{ts}".encode()).hexdigest()[:24]
    col.upsert(
        ids=[turn_id],
        embeddings=[embed(turn_text)],
        documents=[turn_text],
        metadatas=[{"session_id": session_id, "timestamp": ts}],
    )


def delete_document(source: str, collection: str = COL_DOCUMENTS) -> int:
    """
    Remove all chunks for a given source/filepath/url from a collection.
    Returns the number of chunks deleted.
    """
    col = _get_collection(collection)
    source_key = "url" if collection == COL_WEB else ("filepath" if collection == COL_CODE else "source")
    results = col.get(where={source_key: source})
    ids = results.get("ids", [])
    if ids:
        col.delete(ids=ids)
    return len(ids)


# ── Query ─────────────────────────────────────────────────────────────────────

def _format_results(results: dict) -> list[dict]:
    """Convert a ChromaDB result dict into a list of plain dicts."""
    out = []
    docs      = results.get("documents", [[]])[0]
    metas     = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]
    for doc, meta, dist in zip(docs, metas, distances):
        out.append({
            "text":     doc,
            "metadata": meta,
            "distance": round(dist, 4),
        })
    return out


def query_documents(query: str, k: int = TOP_K) -> list[dict]:
    """Semantic search over personal notes and text documents."""
    col = _get_collection(COL_DOCUMENTS)
    if col.count() == 0:
        return []
    results = col.query(query_embeddings=[embed(query)], n_results=min(k, col.count()))
    return _format_results(results)


def query_code(query: str, k: int = TOP_K) -> list[dict]:
    """Semantic search over indexed code files."""
    col = _get_collection(COL_CODE)
    if col.count() == 0:
        return []
    results = col.query(query_embeddings=[embed(query)], n_results=min(k, col.count()))
    return _format_results(results)


def query_web(query: str, k: int = TOP_K) -> list[dict]:
    """Semantic search over scraped web content."""
    col = _get_collection(COL_WEB)
    if col.count() == 0:
        return []
    results = col.query(query_embeddings=[embed(query)], n_results=min(k, col.count()))
    return _format_results(results)


def query_memory(query: str, k: int = TOP_K) -> list[dict]:
    """Retrieve relevant past conversation turns."""
    col = _get_collection(COL_MEMORY)
    if col.count() == 0:
        return []
    results = col.query(query_embeddings=[embed(query)], n_results=min(k, col.count()))
    return _format_results(results)


def query_all(query: str, k: int = TOP_K) -> dict[str, list[dict]]:
    """
    Search all four collections and return a combined dict.
    Only returns results whose distance is below a reasonable threshold.
    """
    threshold = 1.0  # cosine distance — lower is more similar
    results: dict[str, list[dict]] = {}
    for label, fn in [
        ("documents", query_documents),
        ("code",      query_code),
        ("web",       query_web),
        ("memory",    query_memory),
    ]:
        hits = [r for r in fn(query, k) if r["distance"] < threshold]
        if hits:
            results[label] = hits
    return results


# ── Context builder for the AI pipeline ──────────────────────────────────────

def build_context(query: str, k: int = 3) -> str:
    """
    Search all collections and format the most relevant results into a
    plain-text context block suitable for injection into an AI prompt.

    Returns an empty string if nothing relevant is found.
    """
    hits = query_all(query, k=k)
    if not hits:
        return ""

    sections: list[str] = []
    for collection, results in hits.items():
        label_map = {
            "documents": "Relevant documents",
            "code":      "Relevant code",
            "web":       "Relevant web content",
            "memory":    "Relevant past conversations",
        }
        header = label_map.get(collection, collection.capitalize())
        section_lines = [f"=== {header} ==="]
        for r in results:
            meta = r["metadata"]
            source = (
                meta.get("source")
                or meta.get("filepath")
                or meta.get("url")
                or ""
            )
            if source:
                section_lines.append(f"[Source: {source}]")
            section_lines.append(r["text"])
            section_lines.append("")
        sections.append("\n".join(section_lines))

    return "\n".join(sections).strip()


# ── Collection stats ──────────────────────────────────────────────────────────

def stats() -> dict[str, int]:
    """Return the number of stored chunks per collection."""
    return {
        COL_DOCUMENTS: _get_collection(COL_DOCUMENTS).count(),
        COL_CODE:      _get_collection(COL_CODE).count(),
        COL_WEB:       _get_collection(COL_WEB).count(),
        COL_MEMORY:    _get_collection(COL_MEMORY).count(),
    }
