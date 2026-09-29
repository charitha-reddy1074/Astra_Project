import re

import chromadb

from backend.api.config import settings
from backend.api.embeddings.embedder import generate_embeddings

_client = None


def collection_name_for_framework(framework_code: str) -> str:
    """Derive a Chroma collection name from a framework code. Framework-agnostic.

    Single source of truth — writers (ingestion) and readers (retrieval) MUST
    agree, or embedded controls become unreachable.

    Chroma requires 3-512 chars from [a-zA-Z0-9._-], starting and ending
    alphanumeric. Framework codes may contain '/' or ':' (e.g. "ISO/IEC_27001:2022"),
    so anything outside the allowed set is collapsed to '-'.
    """
    slug = framework_code.lower().replace("_", "-")
    slug = re.sub(r"[^a-z0-9._-]+", "-", slug)       # drop '/', ':', spaces, …
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")  # tidy separators
    if not slug:
        slug = "framework"
    name = f"{slug}_v1"
    if not name[0].isalnum():
        name = f"f{name}"
    return name[:512]


def _get_client():
    """Lazily create the local Chroma client (settings.CHROMA_PATH) so importing
    this module has no side effects — the API boots without touching the store."""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=settings.CHROMA_PATH)
    return _client


def get_or_create_collection(name: str):
    return _get_client().get_or_create_collection(name=name)


def upload_chunks(chunks: list[dict], collection_name: str) -> int:
    """Upload chunks to a named Chroma collection. Returns count uploaded."""
    collection = get_or_create_collection(collection_name)

    seen_ids: set[str] = set()
    clean_chunks: list[dict] = []
    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or chunk.get("control_code") or "")
        if not chunk_id or chunk_id in seen_ids:
            continue
        seen_ids.add(chunk_id)
        clean_chunks.append(chunk)

    if not clean_chunks:
        return 0

    texts = [
        chunk.get("content") or chunk.get("text") or chunk.get("statement") or ""
        for chunk in clean_chunks
    ]
    embeddings = generate_embeddings(texts)

    ids, documents, metadatas = [], [], []
    for chunk in clean_chunks:
        chunk_id = str(chunk.get("chunk_id") or chunk.get("id") or chunk.get("control_code") or "")
        content = chunk.get("content") or chunk.get("text") or chunk.get("statement") or ""

        meta = dict(chunk.get("metadata", {}))
        meta["framework_code"] = str(chunk.get("framework_code") or meta.get("framework_code") or "")
        meta["framework"] = str(chunk.get("framework_code") or meta.get("framework") or "")
        meta["control_code"] = str(chunk.get("control_code") or meta.get("control_id") or meta.get("control_code") or "")
        meta["control_name"] = str(chunk.get("control_name") or meta.get("control_name") or "")
        meta["domain"] = str(chunk.get("domain") or meta.get("domain") or "")
        meta["category"] = str(chunk.get("category") or meta.get("category") or "")
        meta["version"] = str(chunk.get("version") or meta.get("version") or "")

        ids.append(chunk_id)
        documents.append(content)
        metadatas.append(meta)

    collection.add(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings.tolist())
    print(f"Uploaded {len(ids)} chunks to '{collection_name}'")
    return len(ids)
