"""Vector store abstraction.

Pinecone is the production store. Tests use InMemoryVectorStore. If Pinecone is
not configured the app degrades to a no-op logger so the site stays functional
(the SQL catalog remains the source of truth; resync_vectors recovers later).
"""
import logging
from dataclasses import dataclass
from typing import Protocol

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    id: str
    score: float
    metadata: dict


class VectorStore(Protocol):
    def upsert(self, vectors: list[tuple[str, list[float], dict]]) -> None: ...
    def delete(self, ids: list[str]) -> None: ...
    def query(
        self,
        query_vector: list[float],
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[SearchHit]: ...
    def count(self) -> int: ...


class InMemoryVectorStore:
    """Deterministic in-memory store used by tests."""

    def __init__(self) -> None:
        self._vectors: dict[str, list[float]] = {}
        self._metadata: dict[str, dict] = {}

    def upsert(self, vectors: list[tuple[str, list[float], dict]]) -> None:
        for vector_id, values, meta in vectors:
            self._vectors[vector_id] = values
            self._metadata[vector_id] = meta

    def delete(self, ids: list[str]) -> None:
        for vector_id in ids:
            self._vectors.pop(vector_id, None)
            self._metadata.pop(vector_id, None)

    def query(
        self,
        query_vector: list[float],
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[SearchHit]:
        import math

        def norm(v: list[float]) -> float:
            return math.sqrt(sum(x * x for x in v)) or 1.0

        qn = norm(query_vector)
        scored = []
        for vector_id, values in self._vectors.items():
            meta = self._metadata.get(vector_id, {})
            if filters:
                matched = True
                for key, value in filters.items():
                    if key in ("category", "level") and meta.get(key) != value:
                        matched = False
                        break
                    if key == "tags" and isinstance(value, list):
                        if not any(tag in meta.get("tags", []) for tag in value):
                            matched = False
                            break
                    if key == "is_active" and meta.get("is_active") is not value:
                        matched = False
                        break
                if not matched:
                    continue
            dot = sum(a * b for a, b in zip(query_vector, values))
            score = dot / (qn * norm(values))
            scored.append(SearchHit(id=vector_id, score=score, metadata=meta))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def count(self) -> int:
        return len(self._vectors)


class NullVectorStore:
    """No-op store used when Pinecone is not configured. Logs once per call."""

    def upsert(self, vectors: list[tuple[str, list[float], dict]]) -> None:
        logger.warning("Vector store not configured; skipping upsert of %d vectors", len(vectors))

    def delete(self, ids: list[str]) -> None:
        logger.warning("Vector store not configured; skipping delete of %d ids", len(ids))

    def query(
        self,
        query_vector: list[float],
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[SearchHit]:
        return []

    def count(self) -> int:
        return 0


class PineconeVectorStore:
    def __init__(self) -> None:
        from pinecone import Pinecone, ServerlessSpec

        self._pc = Pinecone(api_key=settings.pinecone_api_key)
        self._index_name = settings.pinecone_index
        if settings.pinecone_index not in self._pc.list_indexes().names():
            self._pc.create_index(
                name=settings.pinecone_index,
                dimension=settings.pinecone_dimension,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=settings.pinecone_cloud,
                    region=settings.pinecone_region,
                ),
            )
        self._index = self._pc.Index(self._index_name)

    def upsert(self, vectors: list[tuple[str, list[float], dict]]) -> None:
        if not vectors:
            return
        self._index.upsert(
            vectors=[(vid, values, meta) for vid, values, meta in vectors]
        )

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._index.delete(ids=ids)

    def query(
        self,
        query_vector: list[float],
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[SearchHit]:
        resp = self._index.query(
            vector=query_vector,
            top_k=top_k,
            filter=filters,
            include_metadata=True,
        )
        return [
            SearchHit(
                id=match.id,
                score=match.score,
                metadata=match.metadata or {},
            )
            for match in resp.matches
        ]

    def count(self) -> int:
        stats = self._index.describe_index_stats()
        return int(stats.total_vector_count)


_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        if settings.pinecone_api_key:
            _store = PineconeVectorStore()
        else:
            _store = NullVectorStore()
    return _store


def reset_vector_store() -> None:
    global _store
    _store = None
