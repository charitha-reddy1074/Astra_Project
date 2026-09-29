"""
ChromaDB vector store manager.

Uses local PersistentClient with BAAI/bge-base-en-v1.5 sentence-transformer
embeddings (768-dim). Framework controls are stored in per-framework
collections; cross-framework mappings and evidence have dedicated collections.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_huggingface import HuggingFaceEmbeddings

from backend.core.utils import logger


CHROMA_PATH = Path(os.getenv("CHROMA_PATH", "./chroma_db"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5")
COLLECTION_NAME = "framework_controls"
FRAMEWORK_COLLECTIONS = {
    "nist": "framework_nist_csf_2_0",
    "nist_cis": "framework_nist_cis_merged",
    "nist-cis": "framework_nist_cis_merged",
    "iso": "framework_iso_27001_2022",
    "cis": "framework_cis_controls_v8_1_2",
    "market_assessment": "framework_market_assessment_51",
    "market-assessment": "framework_market_assessment_51",
}

CROSS_MAPPING_COLLECTION = "framework_cross_mappings"
EVIDENCE_COLLECTION = "evidence_store"


def framework_collection_name(framework_key: str) -> str:
    """Return the dedicated ChromaDB collection for a framework key."""
    key = str(framework_key or "").strip().lower()
    if key in FRAMEWORK_COLLECTIONS:
        return FRAMEWORK_COLLECTIONS[key]
    safe_key = "".join(ch if ch.isalnum() else "_" for ch in key).strip("_")
    return f"framework_{safe_key or 'unknown'}"


class VectorDBManager:
    """Manage local ChromaDB collections for frameworks, mappings, and evidence."""

    def __init__(self, chroma_path: Path = CHROMA_PATH) -> None:
        self._path = chroma_path
        self._path.mkdir(parents=True, exist_ok=True)

        self._embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        self._client = chromadb.PersistentClient(path=str(self._path))
        self._stores: Dict[str, Chroma] = {}
        self._framework_store: Optional[Chroma] = None
        self._evidence_store: Optional[Chroma] = None

        logger.info(
            "VectorDBManager ready — local Chroma at %s, model: %s",
            self._path, EMBEDDING_MODEL,
        )

    def get_collection_store(self, collection_name: str) -> Chroma:
        """Return a Chroma wrapper for a named local collection."""
        if collection_name not in self._stores:
            self._stores[collection_name] = Chroma(
                client=self._client,
                collection_name=collection_name,
                embedding_function=self._embeddings,
                collection_metadata={"hnsw:space": "cosine"},
            )
        return self._stores[collection_name]

    def clear_collection(self, collection_name: str) -> None:
        """Drop a collection and recreate it lazily on next write/search."""
        try:
            self._client.delete_collection(collection_name)
            logger.info("Collection '%s' cleared.", collection_name)
        except Exception:
            pass
        self._stores.pop(collection_name, None)
        if collection_name == COLLECTION_NAME:
            self._framework_store = None
        if collection_name == EVIDENCE_COLLECTION:
            self._evidence_store = None

    def store_documents(
        self,
        docs: List[Document],
        collection_name: str,
        ids: Optional[Sequence[str]] = None,
    ) -> None:
        """Embed and upsert documents into the named collection."""
        if not docs:
            logger.warning("No documents to store in '%s'.", collection_name)
            return

        document_ids = list(ids) if ids is not None else [
            d.metadata.get("chunk_id")
            or d.metadata.get("control_id")
            or f"doc_{i}"
            for i, d in enumerate(docs)
        ]

        logger.info("Upserting %d documents into '%s'.", len(docs), collection_name)
        store = self.get_collection_store(collection_name)
        batch_size = 100
        for i in range(0, len(docs), batch_size):
            end = min(i + batch_size, len(docs))
            store.add_documents(documents=docs[i:end], ids=document_ids[i:end])
            logger.debug("Ingested batch %d–%d", i, end)

        logger.info("Stored %d embeddings in '%s'.", len(docs), collection_name)

    @property
    def framework_store(self) -> Chroma:
        if self._framework_store is None:
            self._framework_store = self.get_collection_store(framework_collection_name("nist"))
        return self._framework_store

    def store_framework_docs(self, docs: List[Document]) -> None:
        ids = [
            d.metadata.get("chunk_id")
            or d.metadata.get("control_id")
            or f"doc_{i}"
            for i, d in enumerate(docs)
        ]
        self.store_documents(docs, collection_name=framework_collection_name("nist"), ids=ids)

    def clear_framework_store(self) -> None:
        self.clear_collection(COLLECTION_NAME)

    def search_framework(
        self,
        query: str,
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        kwargs: Dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        results = self.framework_store.similarity_search(query, **kwargs)
        logger.info("Framework search returned %d results for: '%s'", len(results), query[:80])
        return results

    def get_framework_docs(
        self,
        collection_name: Optional[str] = None,
        ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
        limit: int = 300,
        offset: int = 0,
    ) -> List[Document]:
        """Fetch documents directly from the framework store by ID or metadata filter."""
        store = self.get_collection_store(collection_name) if collection_name else self.framework_store
        res = store.get(ids=ids, where=where, limit=limit, offset=offset)
        return [
            Document(page_content=content, metadata=metadata)
            for content, metadata in zip(res["documents"], res["metadatas"])
        ]

    def search_collection(
        self,
        collection_name: str,
        query: str,
        k: int = 5,
        filter: Optional[Dict[str, Any]] = None,
    ) -> List[Document]:
        """Similarity search over a named local collection."""
        kwargs: Dict[str, Any] = {"k": k}
        if filter:
            kwargs["filter"] = filter
        return self.get_collection_store(collection_name).similarity_search(query, **kwargs)

    def search_framework_with_scores(
        self,
        query: str,
        k: int = 5,
    ) -> List[tuple[Document, float]]:
        return self.framework_store.similarity_search_with_relevance_scores(query, k=k)

    @property
    def evidence_store(self) -> Chroma:
        if self._evidence_store is None:
            self._evidence_store = self.get_collection_store(EVIDENCE_COLLECTION)
        return self._evidence_store

    def store_evidence_docs(self, docs: List[Document]) -> None:
        if not docs:
            return
        import uuid
        ids = [str(uuid.uuid4()) for _ in docs]
        self.store_documents(docs, collection_name=EVIDENCE_COLLECTION, ids=ids)

    def search_evidence(self, query: str, k: int = 4) -> List[Document]:
        return self.evidence_store.similarity_search(query, k=k)

    def clear_evidence_store(self) -> None:
        self.clear_collection(EVIDENCE_COLLECTION)

    def as_retriever(self, k: int = 5):
        return self.framework_store.as_retriever(
            search_type="similarity",
            search_kwargs={"k": k},
        )

    def _collection_count(self, collection_name: str) -> int:
        try:
            return self._client.get_collection(collection_name).count()
        except Exception:
            return 0

    def collection_stats(self) -> Dict[str, Any]:
        framework_counts = {
            name: self._collection_count(name)
            for name in FRAMEWORK_COLLECTIONS.values()
        }
        return {
            "framework_controls_count": self._collection_count(COLLECTION_NAME),
            "framework_collection_counts": framework_counts,
            "cross_mapping_count": self._collection_count(CROSS_MAPPING_COLLECTION),
            "evidence_chunks_count": self._collection_count(EVIDENCE_COLLECTION),
            "chroma_path": str(self._path.resolve()),
        }
