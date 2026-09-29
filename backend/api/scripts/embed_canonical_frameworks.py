"""Embed all canonical framework controls into the local Chroma store.

Builds the per-framework collections that Ask CyberAI queries
(`<framework-code>_v1`, see AiService._collection_name) from the canonical
JSON files, so vector retrieval works fully offline.

Run from the repo root:
    python -m backend.api.scripts.embed_canonical_frameworks
"""
from __future__ import annotations

import json
from pathlib import Path

from backend.api.config import settings
from backend.api.embeddings.chroma_loader import _get_client, upload_chunks

# canonical file stem -> framework code registered in the app DB
FILE_TO_CODE = {
    "cis_controls_v8_1_2": "CIS_CONTROLS_V8",
    "framework_pci_dss_4_0": "PCI_DSS",
    "iso_27001_2022": "ISO_27001_2022",
    "market_assessment_canonical": "MARKET_ASSESSMENT",
    "nist_csf_2_0": "NIST_CSF_2_0",
}


def _collection_name(framework_code: str) -> str:
    """Shared with ingestion/retrieval so all three agree on collection names."""
    from backend.api.embeddings.chroma_loader import collection_name_for_framework
    return collection_name_for_framework(framework_code)


def _iter_controls(domain: dict):
    for ctrl in domain.get("controls") or []:
        yield ctrl, ""
    for cat in domain.get("categories") or []:
        for ctrl in cat.get("controls") or []:
            yield ctrl, cat.get("category_name") or ""
    for sub in domain.get("sub_domains") or []:
        for ctrl in sub.get("controls") or []:
            yield ctrl, sub.get("sub_domain_name") or sub.get("name") or ""


def build_chunks(data: dict, framework_code: str) -> list[dict]:
    chunks: list[dict] = []
    for dom in data.get("domains") or []:
        domain_label = " ".join(
            p for p in (dom.get("domain_id"), dom.get("domain_name")) if p
        )
        for ctrl, category in _iter_controls(dom):
            control_id = str(ctrl.get("control_id") or "")
            statement = str(ctrl.get("control_statement") or "").strip()
            if not control_id or not statement:
                continue
            evidence = ctrl.get("expected_evidence_types") or []
            content = f"{domain_label} — {control_id}: {statement}"
            if evidence:
                content += " Expected evidence: " + "; ".join(map(str, evidence))
            chunks.append({
                "chunk_id": f"{framework_code}:{control_id}",
                "content": content,
                "framework_code": framework_code,
                "control_code": control_id,
                "domain": domain_label,
                "category": category,
            })
    return chunks


def main() -> None:
    client = _get_client()
    total = 0
    for path in sorted(Path(settings.CANONICAL_FOLDER).glob("*.json")):
        code = FILE_TO_CODE.get(path.stem)
        if not code:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        chunks = build_chunks(data, code)
        name = _collection_name(code)
        try:
            client.delete_collection(name)
        except Exception:
            pass
        count = upload_chunks(chunks, name)
        total += count
        print(f"{path.name} -> {name}: {count} controls")
    print(f"Done. {total} controls embedded into {settings.CHROMA_PATH}")


if __name__ == "__main__":
    main()
