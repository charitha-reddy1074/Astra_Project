"""Reader for the drop-in framework dataset format.

This is the *only* contract a new framework JSON file has to satisfy. Drop a
conforming file into `backend/api/data/canonical/` and press
*Load existing* (`POST /frameworks/load-existing`), or upload it to
`POST /frameworks/import-json` — no code change required.

    {
      "framework": {
        "framework_id": "SOC2_TSC_2017",          // optional (falls back to name)
        "framework_name": "AICPA SOC 2",          // or "name"
        "name": "AICPA SOC 2",
        "version": "2017",
        "description": "…",                       // optional
        "…": "any other top-level metadata is preserved on the framework row",
        "domains": [
          {
            "domain_id": "SECURITY",
            "domain_name": "Security (Common Criteria)",
            "description": "…",
            "sub_domains": [
              {
                "sub_domain_id": "CC1",
                "sub_domain_name": "Control Environment",
                "description": "…",                // doubles as the criteria statement
                "controls": [
                  {
                    "control_id": "CC1.1",
                    "control_objective": "…",       // see STATEMENT_KEYS
                    "expected_evidence_types": [],  // optional
                    "metadata": { }                 // optional, preserved
                  }
                ]
              }
            ]
          }
        ]
      }
    }

Notes
-----
* ``framework`` may be omitted — a bare ``{"name": …, "domains": [...]}`` is read
  identically. The wrapper exists so a dataset can carry sibling top-level
  blocks (``knowledge_base_document``, ``supporting_reference_content``) without
  colliding with framework metadata.
* The statement text may be published under any of ``STATEMENT_KEYS``; that is
  the single axis of variation the reader absorbs, which is what lets a NIST
  (``outcome_statement``) and a SOC 2 (``control_objective``) dataset share one
  format.
* A ``domain`` may hold ``controls`` directly instead of under ``sub_domains``.
  The reader then synthesises one pass-through sub-domain so the resulting
  hierarchy stays uniform (this mirrors what the DB importer already does for
  frameworks with no category layer).

Everything here is pure and framework-agnostic. Framework *semantics* live in
``backend.api.compliance.adapters``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

# Field names accepted for a control's requirement/statement text, in priority
# order. NIST CSF publishes subcategory outcome statements; SOC 2 publishes
# control objectives; the root ingestion pipeline publishes control statements.
STATEMENT_KEYS: tuple[str, ...] = (
    "outcome_statement",
    "control_objective",
    "control_statement",
    "requirement",
    "requirement_text",
    "statement",
    "text",
    "title",
    "name",
)

_ID_KEYS = ("control_id", "code", "id")
_NAME_KEYS = ("control_name", "name", "title", "label")

#: Self-describing summary of the drop-in format, served by
#: `GET /compliance/frameworks` so an operator adding a framework does not have to
#: read the module docstring. Deliberately mirrors the contract above.
DATASET_FORMAT: dict[str, Any] = {
    "shape": "framework -> domains -> sub_domains -> controls",
    "root_key": "framework",
    "framework": ["framework_id?", "framework_name|name", "version?", "description?", "domains"],
    "domain": ["domain_id", "domain_name", "description?", "sub_domains"],
    "sub_domain": ["sub_domain_id", "sub_domain_name", "description?", "category_id?", "category_name?"],
    "control": ["control_id|code", "description|statement|control_text", "metadata?"],
    "notes": [
        "Both the 'framework -> domains -> categories -> controls' and the "
        "deeper 'framework -> domains -> sub_domains -> categories -> controls' "
        "shapes are accepted; the reader flattens the latter.",
        "Aliases for control text ('description', 'statement', 'control_text', "
        "'requirement', ...) are resolved automatically, so a new framework's "
        "dataset does not need to guess the platform's preferred key.",
        "Drop the file in backend/api/data/canonical/ and call "
        "POST /frameworks/load-existing, or upload it to POST /frameworks/import-json. "
        "No code change is required to import it.",
        "An adapter is required before the framework can be *evaluated*: see "
        "backend/api/compliance/adapters/registry.py.",
    ],
}


class DatasetFormatError(ValueError):
    """Raised when a file cannot be read as a framework dataset at all."""


def statement_text(control: Mapping[str, Any]) -> str:
    """Return the control's requirement/statement text, or "" if it has none."""
    for key in STATEMENT_KEYS:
        value = control.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick(mapping: Mapping[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def is_dataset(doc: Any) -> bool:
    """True if `doc` looks like a drop-in framework dataset.

    Deliberately cheap and conservative: it must not claim the API canonical
    shape (``code`` + ``categories``), which is already handled elsewhere, and it
    must not claim the legacy root pipeline shape, which has no
    ``sub_domain``/``outcome_statement``/``control_objective`` markers.
    """
    if not isinstance(doc, Mapping) or "code" in doc:
        return False
    fw = doc.get("framework")
    if not isinstance(fw, Mapping):
        return False
    domains = fw.get("domains")
    if not isinstance(domains, list) or not domains:
        return False
    if not isinstance(domains[0], Mapping):
        return False
    # The hierarchy marker: every dataset reaches its leaves through sub_domains,
    # or declares a domain that carries controls directly.
    if "domain_id" not in domains[0] and "domain_name" not in domains[0]:
        return False
    return _has_sub_domain_layer(domains)


def _has_sub_domain_layer(domains: list[Any]) -> bool:
    for domain in domains:
        if not isinstance(domain, Mapping):
            continue
        sub_domains = domain.get("sub_domains")
        if isinstance(sub_domains, list) and sub_domains:
            return True
        if domain.get("controls"):
            return True
    return False


def _control_to_api(control: Mapping[str, Any], domain_code: str,
                    sub_domain_code: str) -> dict[str, Any] | None:
    """Map one dataset control into the internal API control shape.

    Returns None only for a control with no text at all. A control that *has*
    text but no identifier is an authoring mistake (usually a typo'd
    `control_id`), and it raises instead: dropping it would silently under-report
    the framework's coverage, which is the one thing a compliance tool must not do.
    """
    statement = statement_text(control)
    code = _pick(control, _ID_KEYS)
    if not code:
        if not statement:
            return None
        raise DatasetFormatError(
            f"Control in domain '{domain_code}' / sub-domain '{sub_domain_code}' has "
            f"text but no identifier. Add one of {list(_ID_KEYS)}; the keys present "
            f"were {sorted(control)}. A control with no id cannot be evidenced, "
            f"evaluated or reported against, so it is rejected rather than dropped."
        )
    if not statement:
        raise DatasetFormatError(
            f"Control '{code}' in domain '{domain_code}' / sub-domain "
            f"'{sub_domain_code}' has no statement text. Add one of "
            f"{list(STATEMENT_KEYS)}."
        )

    metadata = control.get("metadata")
    expected = control.get("expected_evidence_types")
    if not isinstance(expected, list) or not expected:
        expected = []

    return {
        "code": code,
        "name": _pick(control, _NAME_KEYS) or None,
        "statement": statement,
        "category_code": sub_domain_code,
        "category_name": None,  # filled in by the caller from the sub-domain
        "criticality": control.get("criticality") or "standard",
        "expected_evidence_types": [str(e) for e in expected],
        "cross_refs": _cross_refs(control, metadata, domain_code, sub_domain_code),
        # Carried through the existing `maturity_criteria` JSON column so no new
        # column is needed. The adapters read this back to resolve requirements.
        "maturity_criteria": {
            "dataset_metadata": metadata if isinstance(metadata, Mapping) else None,
        } if isinstance(metadata, Mapping) else None,
        # The canonical questions list is intentionally left empty: the existing
        # importer already auto-generates exactly one question per control from
        # its statement, which is the questionnaire behaviour the platform has
        # always had. The compliance layer evaluates controls, not questions.
        "questions": [],
    }


def _cross_refs(control: Mapping[str, Any], metadata: Any,
                domain_code: str, sub_domain_code: str) -> list[str]:
    refs: list[str] = [sub_domain_code, domain_code]
    raw = control.get("cross_references")
    if isinstance(raw, list):
        refs.extend(str(r) for r in raw if str(r).strip())
    if isinstance(metadata, Mapping):
        for key in ("cross_references", "informative_references", "related_controls"):
            value = metadata.get(key)
            if isinstance(value, list):
                refs.extend(
                    str(v) for v in value
                    if isinstance(v, str) and v.strip()
                )
    seen: set[str] = set()
    return [r for r in refs if r and not (r in seen or seen.add(r))]


def dataset_to_api_canonical(doc: Mapping[str, Any], *,
                             name_hint: str = "",
                             version_hint: str = "") -> dict[str, Any]:
    """Read a drop-in dataset and return the internal API canonical shape.

    The returned dict is exactly what ``FrameworkService._store_canonical_json``
    and ``generic_chunker.build_chunks`` already consume
    (``code`` / ``domains[].categories[].controls[].statement``), so importing a
    dataset needs no change to either.

    Raises :class:`DatasetFormatError` when the file is not a dataset.
    """
    if not is_dataset(doc):
        raise DatasetFormatError(
            "Not a framework dataset: expected a top-level 'framework' object "
            "with a 'domains' list of {domain_id, domain_name, sub_domains[]}."
        )

    fw = doc["framework"]
    if not isinstance(fw, Mapping):  # pragma: no cover - guarded by is_dataset
        raise DatasetFormatError("'framework' must be an object.")

    name = _pick(fw, ("framework_name", "name")) or name_hint or "Framework"
    version = str(fw.get("version") or version_hint or "1.0")

    domains: list[dict[str, Any]] = []
    for order, raw_domain in enumerate(fw.get("domains") or []):
        if not isinstance(raw_domain, Mapping):
            continue
        domain_code = _pick(raw_domain, ("domain_id", "code", "id"))
        domain_name = _pick(raw_domain, ("domain_name", "name")) or domain_code
        if not domain_code:
            continue

        raw_sub = raw_domain.get("sub_domains")
        sub_domains = [s for s in raw_sub if isinstance(s, Mapping)] if isinstance(raw_sub, list) else []
        if not sub_domains:
            # Flat domain -> controls. Synthesise one pass-through sub-domain so
            # the DB nesting stays uniform (same rule the importer applies).
            sub_domains = [{
                "sub_domain_id": domain_code,
                "sub_domain_name": domain_name,
                "description": raw_domain.get("description"),
                "controls": raw_domain.get("controls") or [],
            }]

        categories: list[dict[str, Any]] = []
        for sub in sub_domains:
            sub_code = _pick(sub, ("sub_domain_id", "category_id", "code", "id")) or domain_code
            sub_name = _pick(sub, ("sub_domain_name", "category_name", "name")) or sub_code
            controls: list[dict[str, Any]] = []
            for raw_control in sub.get("controls") or []:
                if not isinstance(raw_control, Mapping):
                    continue
                mapped = _control_to_api(raw_control, domain_code, sub_code)
                if mapped:
                    mapped["category_name"] = sub_name
                    controls.append(mapped)
            if not controls:
                continue
            categories.append({
                "code": sub_code,
                "name": sub_name,
                # The sub-domain description IS the criteria statement for this
                # grouping (NIST Category objective / SOC 2 CC series scope).
                "criteria_statement": sub.get("description") or None,
                "description": sub.get("description") or None,
                "controls": controls,
            })

        if not categories:
            continue
        domains.append({
            "code": domain_code,
            "name": domain_name,
            "description": raw_domain.get("description") or None,
            "categories": categories,
        })

    return {
        "code": _derive_code(fw, name),
        "name": name,
        "version": version,
        "description": fw.get("description") or None,
        "maturity_levels": fw.get("maturity_levels"),
        "domains": domains,
    }


def _derive_code(fw: Mapping[str, Any], name: str) -> str:
    """Stable, URL-safe framework code from the dataset (or the name).

    Framework codes become `Framework.code`, a Chroma collection name and part of
    a URL path, so anything that is not alphanumeric or underscore is dropped:
    the NIST dataset's ``"NIST Cybersecurity Framework (CSF)"`` becomes
    ``NIST_CYBERSECURITY_FRAMEWORK_CSF`` rather than keeping its parentheses.
    """
    explicit = _pick(fw, ("framework_id", "code"))
    if explicit:
        return _slugify_code(explicit)
    return _slugify_code(name)


def _slugify_code(value: str) -> str:
    """Uppercase, alphanumeric-and-underscore only, non-empty."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "")).strip("_").upper()
    return re.sub(r"_+", "_", slug) or "FRAMEWORK"
