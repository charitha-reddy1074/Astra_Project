"""Top-k control retrieval, scoped to the frameworks the platform supports.

Retrieval happens *before* any model call for two reasons: it is what keeps the
prompt small enough to be affordable, and it is what makes the model checkable
— every control ID it is allowed to mention is one this module returned, so a
hallucinated control ID is detectable by set membership rather than by
eyeballing prose.

The existing Chroma stack (`backend.api.retrieval.retriever.retrieve_chunks`) is
used when it is usable. It needs a working sentence-transformer, which is an
optional runtime dependency, so every use is guarded and a deterministic lexical
scorer takes over when the vector stack cannot embed. Both paths return the same
`RetrievedControl` shape, so nothing downstream knows which one ran.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from backend.api.compliance.adapters.registry import get_adapter, supported_framework_keys
from backend.api.compliance.types import ControlSpec

logger = logging.getLogger(__name__)

#: Set once the vector stack proves unusable, so a broken embedding dependency
#: costs one attempt per process instead of one per control. A sentence-
#: transformer import can crash the interpreter outright on some numpy builds,
#: which is why the stack is off by default (see `SEMANTIC_VECTOR_RETRIEVAL`).
_VECTOR_DISABLED_REASON = ""

#: Cap on how much of a control's text is used for scoring/prompting. The
#: requirement statement is what matters; a control's full narrative is not.
_MAX_CONTROL_TEXT = 600

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Deliberately small: removing these raises the score of terms that actually
# discriminate between controls.
_STOPWORDS = frozenset("""
a an and are as at be been by for from has have in into is it its of on or
that the their there these this to was were will with which who whom whose
not no nor but if then than so such can could may might must shall should
""".split())


def _vector_retrieval_enabled() -> bool:
    """True only when the operator has opted into the vector stack."""
    global _VECTOR_DISABLED_REASON
    if _VECTOR_DISABLED_REASON:
        return False
    try:
        from backend.api.config import settings

        return str(getattr(settings, "SEMANTIC_VECTOR_RETRIEVAL", "false")).lower() in (
            "1", "true", "yes", "on",
        )
    except Exception:  # pragma: no cover - config import guard
        return False


def _disable_vector_stack(reason: str) -> None:
    global _VECTOR_DISABLED_REASON
    if not _VECTOR_DISABLED_REASON:
        _VECTOR_DISABLED_REASON = reason
        logger.warning(
            "Semantic control retrieval will use deterministic ranking for the "
            "rest of this process: vector retrieval disabled (%s).", reason,
        )


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS and len(t) > 2]


@dataclass(frozen=True)
class RetrievedControl:
    """One control offered to the model, with the provenance it needs.

    `source` records where the control text came from so a reviewer can trace a
    model claim back to the dataset rather than to the model.
    """

    control_id: str
    statement: str
    framework: str
    framework_name: str
    requirement_id: str = ""
    requirement: str = ""
    clauses: tuple[str, ...] = ()
    domain_code: str = ""
    domain_name: str = ""
    criticality: str = "standard"
    expected_evidence_types: tuple[str, ...] = ()
    source: str = "framework_dataset"
    source_path: str = ""
    score: float = 0.0
    retrieval_method: str = "lexical"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "statement": self.statement,
            "framework": self.framework,
            "framework_name": self.framework_name,
            "requirement_id": self.requirement_id,
            "requirement": self.requirement,
            "clauses": list(self.clauses),
            "domain_code": self.domain_code,
            "domain_name": self.domain_name,
            "criticality": self.criticality,
            "expected_evidence_types": list(self.expected_evidence_types),
            "source": self.source,
            "source_path": self.source_path,
            "score": round(self.score, 4),
            "retrieval_method": self.retrieval_method,
        }


def _searchable_text(control: ControlSpec) -> str:
    parts = [
        control.control_id,
        control.domain_name,
        control.domain_code,
        control.statement,
        control.requirement.requirement_id,
        control.requirement.text,
        *control.requirement.clauses,
    ]
    return " ".join(p for p in parts if p)


class ControlRetriever:
    """Framework-scoped control retrieval with a deterministic fallback.

    `controls` is the in-scope `ControlSpec` list the deterministic evaluator is
    already working over, so retrieval can never widen an assessment's scope:
    a control excluded from the run cannot be introduced by the model.
    """

    def __init__(
        self,
        controls: Sequence[ControlSpec] = (),
        *,
        use_vector_store: bool | None = None,
        collection_prefix: str = "framework_",
    ) -> None:
        self._controls: tuple[ControlSpec, ...] = tuple(controls)
        self._use_vector_store = (
            _vector_retrieval_enabled() if use_vector_store is None else use_vector_store
        )
        self._collection_prefix = collection_prefix
        self._idf: dict[str, float] = self._build_idf(self._controls)
        self._token_cache: dict[str, frozenset[str]] = {
            c.control_id: frozenset(_tokenize(_searchable_text(c)))
            for c in self._controls
        }

    # ── construction ───────────────────────────────────────────────────────

    @staticmethod
    def _build_idf(controls: Sequence[ControlSpec]) -> dict[str, float]:
        """Inverse document frequency over the in-scope controls.

        With 106 NIST and 61 SOC 2 controls, a term appearing in most of them
        ("risk", "access") carries almost no retrieval signal; without the idf
        weighting those terms dominate the overlap score and every control
        looks equally relevant.
        """
        if not controls:
            return {}
        total = len(controls)
        doc_freq: dict[str, int] = {}
        for control in controls:
            for term in {t for t in _tokenize(_searchable_text(control))}:
                doc_freq[term] = doc_freq.get(term, 0) + 1
        return {term: math.log((total + 1) / (freq + 0.5)) for term, freq in doc_freq.items()}

    @property
    def controls(self) -> tuple[ControlSpec, ...]:
        return self._controls

    def with_controls(self, controls: Sequence[ControlSpec]) -> "ControlRetriever":
        """A retriever over a different control set, keeping the same policy."""
        return ControlRetriever(
            controls,
            use_vector_store=self._use_vector_store,
            collection_prefix=self._collection_prefix,
        )

    # ── filtering ──────────────────────────────────────────────────────────

    def _scoped(
        self,
        *,
        framework: str | None,
        control_ids: Sequence[str] | None,
    ) -> list[ControlSpec]:
        """Apply the framework and control-ID filters before any ranking.

        Applied first, not after: filtering post-hoc would let an out-of-scope
        control occupy a top-k slot and then be dropped, silently returning
        fewer controls than asked for.
        """
        scoped = list(self._controls)

        if framework:
            key = framework.strip()
            adapter = get_adapter(key)
            if adapter is not None:
                scoped = [c for c in scoped if c.framework == adapter.key]
            else:
                wanted = key.lower()
                scoped = [
                    c for c in scoped
                    if wanted in {c.framework.lower(), c.framework_name.lower()}
                ]

        if control_ids:
            wanted = {cid.strip().upper() for cid in control_ids if cid and cid.strip()}
            if wanted:
                scoped = [c for c in scoped if c.control_id.strip().upper() in wanted]

        return scoped

    # ── ranking ────────────────────────────────────────────────────────────

    def _lexical_rank(
        self,
        candidates: Sequence[ControlSpec],
        query: str,
    ) -> list[tuple[ControlSpec, float]]:
        """Deterministic idf-weighted overlap ranking.

        This is a fallback, not a reimplementation of the vector store: it only
        has to be good enough to hand the model a short, on-topic control list
        when embeddings are unavailable, and it has to be reproducible, which a
        network call is not.
        """
        query_terms = set(_tokenize(query))
        if not query_terms:
            # No usable query text: fall back to dataset order, which is the
            # authoritative reading order the report uses.
            return [(c, 0.0) for c in candidates]

        upper_query = query.strip().upper()
        scored: list[tuple[ControlSpec, float]] = []
        for control in candidates:
            doc_terms = self._token_cache.get(control.control_id) or frozenset(
                _tokenize(_searchable_text(control))
            )
            shared = query_terms & doc_terms
            score = sum(self._idf.get(term, 1.0) for term in shared)

            # An explicit control ID in the query is an unambiguous instruction
            # and should win over any amount of topical overlap.
            if control.control_id.strip().upper() in upper_query:
                score += 10.0
            # A requirement id (GV.OC, CC6.1) is nearly as explicit.
            if control.requirement.requirement_id.strip().upper() in upper_query:
                score += 5.0

            if score > 0:
                scored.append((control, score))

        scored.sort(key=lambda pair: (-pair[1], pair[0].control_id))
        return scored

    def _vector_rank(
        self,
        framework: str | None,
        query: str,
        top_k: int,
    ) -> list[tuple[str, float]] | None:
        """Best-effort use of the existing Chroma retrieval.

        Returns None when the vector stack is unavailable, unpopulated for this
        framework, or raises for any reason — the caller then uses the lexical
        path. Deliberately swallows its errors: a semantic-ranking nicety must
        never be able to fail an assessment.
        """
        if not self._use_vector_store or _VECTOR_DISABLED_REASON:
            return None
        try:
            from backend.api.retrieval.retriever import retrieve_chunks
        except Exception as exc:
            _disable_vector_stack(f"retriever import failed: {exc}")
            return None

        adapter = get_adapter(framework) if framework else None
        collection = f"{self._collection_prefix}{(adapter.key if adapter else framework or '')}"
        try:
            raw = retrieve_chunks(query, collection, top_k=top_k)
        except Exception as exc:
            _disable_vector_stack(f"collection '{collection}' unusable: {exc}")
            return None

        try:
            ids = (raw.get("ids") or [[]])[0]
            distances = (raw.get("distances") or [[]])[0]
        except Exception as exc:  # pragma: no cover - shape guard
            logger.debug("Unexpected vector result shape: %s", exc)
            return None
        if not ids:
            return None

        ranked: list[tuple[str, float]] = []
        for position, raw_id in enumerate(ids):
            control_id = str(raw_id).split("::")[-1].strip().upper()
            distance = distances[position] if position < len(distances) else 1.0
            try:
                similarity = 1.0 / (1.0 + float(distance))
            except (TypeError, ValueError):
                similarity = 0.0
            ranked.append((control_id, similarity))
        return ranked or None

    # ── public API ─────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        *,
        framework: str | None = None,
        top_k: int = 5,
        control_ids: Sequence[str] | None = None,
    ) -> list[RetrievedControl]:
        """Return at most `top_k` in-scope controls, most relevant first.

        An explicit `control_ids` list is a hard request, not a ranking hint,
        so those controls are returned in the order asked for and are never
        dropped for failing to match the query text.
        """
        if top_k <= 0:
            return []
        candidates = self._scoped(framework=framework, control_ids=control_ids)
        if not candidates:
            return []

        by_id = {c.control_id.strip().upper(): c for c in candidates}
        results: list[RetrievedControl] = []

        for control in self._pinned(candidates, control_ids):
            if len(results) >= top_k:
                return results
            results.append(self._to_retrieved(control, 1000.0, "requested"))
        if len(results) >= top_k:
            return results

        taken = {r.control_id for r in results}
        vector_scored = self._vector_rank(framework, query, top_k)

        if vector_scored:
            # Vector hits are only accepted when they name a control that is
            # actually in scope; a stale collection must not reintroduce
            # controls the assessment excluded.
            for control_id, similarity in vector_scored:
                control = by_id.get(control_id)
                if control is None or control.control_id in taken:
                    continue
                taken.add(control.control_id)
                results.append(self._to_retrieved(control, similarity, "vector"))
                if len(results) >= top_k:
                    return results

        # Top up deterministically, so the caller always gets `top_k` when it
        # can, whichever ranking path ran.
        for control, score in self._lexical_rank(candidates, query):
            if control.control_id in taken:
                continue
            taken.add(control.control_id)
            results.append(self._to_retrieved(control, score, "lexical"))
            if len(results) >= top_k:
                break
        return results

    @staticmethod
    def _pinned(
        candidates: Sequence[ControlSpec],
        control_ids: Sequence[str] | None,
    ) -> list[ControlSpec]:
        """The explicitly requested controls, in the order they were asked for."""
        if not control_ids:
            return []
        by_id = {c.control_id.strip().upper(): c for c in candidates}
        pinned: list[ControlSpec] = []
        for raw in control_ids:
            control = by_id.get(str(raw).strip().upper())
            if control is not None and control not in pinned:
                pinned.append(control)
        return pinned

    def _to_retrieved(
        self,
        control: ControlSpec,
        score: float,
        method: str,
    ) -> RetrievedControl:
        return RetrievedControl(
            control_id=control.control_id,
            statement=(control.statement or control.requirement.text)[:_MAX_CONTROL_TEXT],
            framework=control.framework,
            framework_name=control.framework_name,
            requirement_id=control.requirement.requirement_id,
            requirement=control.requirement.text[:_MAX_CONTROL_TEXT],
            clauses=control.requirement.clauses,
            domain_code=control.domain_code,
            domain_name=control.domain_name,
            criticality=control.criticality,
            expected_evidence_types=control.expected_evidence_types,
            source="framework_dataset",
            score=float(score),
            retrieval_method=method,
            metadata=dict(control.metadata),
        )


def retrieve_relevant_controls(
    query: str,
    controls: Iterable[ControlSpec],
    *,
    framework: str | None = None,
    top_k: int = 5,
    control_ids: Sequence[str] | None = None,
) -> list[RetrievedControl]:
    """Convenience wrapper over `ControlRetriever` for one-shot callers."""
    retriever = ControlRetriever(list(controls))
    return retriever.retrieve(
        query, framework=framework, top_k=top_k, control_ids=control_ids,
    )


__all__ = [
    "ControlRetriever",
    "RetrievedControl",
    "retrieve_relevant_controls",
    "supported_framework_keys",
]
