"""Rebuild the NIST CSF 2.0 canonical JSON from the authoritative NIST CSWP 29 PDF.

The previously shipped canonical file was produced by the LLM extraction pipeline
and was truncated to 35 of the framework's 106 subcategories (whole categories
such as GV.OV and GV.SC-02..10 were missing entirely). Appendix A of NIST CSWP 29
lists the complete CSF Core as a clean hierarchical outline:

    GOVERN (GV): <function description>
    • Organizational Context (GV.OC): <category description>
    o GV.OC-01: <subcategory statement>

That structure is parsed deterministically here — no LLM — so every control ID and
statement is verbatim from the source, and the result is reproducible.

Produces the "root canonical" shape (framework_id / domain_id / control_id) that
backend/api/services/framework_service.py normalises on import, and emits the real
22 categories so the framework nests function → category → subcategory.

Usage:
    python scripts/build_nist_csf_canonical.py [--questions-per-control {1,2}]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = PROJECT_ROOT / "data" / "frameworks" / "pdf" / "NIST.CSWP.29 (1).pdf"
OUT_CANONICAL = PROJECT_ROOT / "backend" / "api" / "data" / "canonical" / "nist_csf_2_0.json"
OUT_MIRROR = PROJECT_ROOT / "data" / "frameworks" / "json" / "nist_csf_2_0.json"

FUNCTIONS = ("GV", "ID", "PR", "DE", "RS", "RC")
EXPECTED_TOTAL = 106
# Official NIST CSF 2.0 subcategory counts per function — a hard check that the
# parse captured the whole Core and nothing extra.
EXPECTED_PER_FUNCTION = {"GV": 31, "ID": 21, "PR": 22, "DE": 11, "RS": 13, "RC": 8}

FUNCTION_RE = re.compile(r"^([A-Z][A-Z ]+?)\s*\((" + "|".join(FUNCTIONS) + r")\):\s*(.*)$")
CATEGORY_RE = re.compile(r"^[•·]\s*(.+?)\s*\((" + "|".join(FUNCTIONS) + r")\.([A-Z]{2})\):\s*(.*)$")
SUBCAT_RE = re.compile(r"^o?\s*((?:" + "|".join(FUNCTIONS) + r")\.[A-Z]{2}-\d{2}):\s*(.*)$")
# Page furniture to drop before parsing.
NOISE_RE = re.compile(
    r"^(NIST CSWP 29|The NIST Cybersecurity Framework \(CSF\) 2\.0|February 26, 2024|\d{1,3})\s*$"
)


def normalise(text: str) -> str:
    """Collapse whitespace and normalise the PDF's typographic punctuation."""
    text = unicodedata.normalize("NFKC", text)
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace("—", "—").replace(" ", " "))
    return re.sub(r"\s+", " ", text).strip()


def extract_outline_lines(pdf_path: Path) -> list[str]:
    """Return Appendix A's outline as logical lines (wrapped lines re-joined)."""
    doc = fitz.open(pdf_path)
    raw: list[str] = []
    for page in doc:
        raw.extend(page.get_text().splitlines())
    doc.close()

    # Keep only from the first function heading of the Core table onward.
    start = next((i for i, ln in enumerate(raw)
                  if FUNCTION_RE.match(normalise(ln)) and "GOVERN" in ln.upper()), None)
    if start is None:
        sys.exit("Could not locate the CSF Core table (GOVERN (GV):) in the PDF.")

    lines: list[str] = []
    for ln in raw[start:]:
        s = normalise(ln)
        if not s or NOISE_RE.match(s):
            continue
        starts_item = (s.startswith(("•", "·", "o ")) or FUNCTION_RE.match(s)
                       or SUBCAT_RE.match(s))
        if starts_item or not lines:
            lines.append(s)
        else:
            lines[-1] += " " + s      # continuation of a wrapped line
    return lines


def parse_core(lines: list[str]) -> list[dict]:
    """Parse the outline into [{function, categories:[{controls:[...]}]}]."""
    functions: list[dict] = []
    cur_fn: dict | None = None
    cur_cat: dict | None = None

    for line in lines:
        m = FUNCTION_RE.match(line)
        if m and m.group(2) in FUNCTIONS:
            name, code, desc = m.group(1).title().strip(), m.group(2), m.group(3)
            if any(f["code"] == code for f in functions):
                cur_fn = next(f for f in functions if f["code"] == code)
                cur_cat = None
                continue
            cur_fn = {"code": code, "name": name, "description": desc, "categories": []}
            functions.append(cur_fn)
            cur_cat = None
            continue

        m = CATEGORY_RE.match(line)
        if m and cur_fn is not None:
            cat_name, fn_code, cat_suffix, desc = m.group(1), m.group(2), m.group(3), m.group(4)
            if fn_code != cur_fn["code"]:
                cur_fn = next((f for f in functions if f["code"] == fn_code), cur_fn)
            cat_code = f"{fn_code}.{cat_suffix}"
            existing = next((c for c in cur_fn["categories"] if c["code"] == cat_code), None)
            if existing:
                cur_cat = existing
                continue
            cur_cat = {"code": cat_code, "name": cat_name, "description": desc, "controls": []}
            cur_fn["categories"].append(cur_cat)
            continue

        m = SUBCAT_RE.match(line)
        if m:
            ctrl_id, statement = m.group(1), m.group(2)
            fn_code, cat_code = ctrl_id.split(".")[0], ctrl_id.split("-")[0]
            fn = next((f for f in functions if f["code"] == fn_code), None)
            if fn is None:
                continue
            cat = next((c for c in fn["categories"] if c["code"] == cat_code), None)
            if cat is None:
                # Subcategory seen before its category header — synthesise it.
                cat = {"code": cat_code, "name": cat_code, "description": "", "controls": []}
                fn["categories"].append(cat)
            if any(c["control_id"] == ctrl_id for c in cat["controls"]):
                continue
            if not statement:
                continue
            # The last subcategory absorbs the following appendix heading, since
            # that heading is not itself an outline item.
            statement = re.split(r"\s+Appendix\s+[A-Z]\b", statement)[0].strip()
            cat["controls"].append({"control_id": ctrl_id, "statement": statement})
    return functions


def build_canonical(functions: list[dict], questions_per_control: int) -> dict:
    domains = []
    for fn in functions:
        categories = []
        for cat in fn["categories"]:
            controls = []
            for ctrl in cat["controls"]:
                cid, stmt = ctrl["control_id"], ctrl["statement"]
                questions = [{
                    "question_id": f"{cid}-Q1",
                    "question_text": f"Is the organization implementing and operating the control: {stmt}",
                    "question_type": "yes_no",
                    "weight": 1.0,
                    "expected_evidence_types": [
                        "policy", "procedure", "configuration", "records", "report",
                    ],
                }]
                if questions_per_control == 2:
                    questions.append({
                        "question_id": f"{cid}-Q2",
                        "question_text": (
                            f"Provide evidence demonstrating implementation of {cid}: {stmt}"
                        ),
                        "question_type": "evidence_upload",
                        "weight": 1.0,
                        "expected_evidence_types": [
                            "policy", "procedure", "configuration", "records", "report",
                        ],
                    })
                controls.append({
                    "control_id": cid,
                    "control_statement": stmt,
                    "criticality": "standard",
                    "cross_references": [],
                    "expected_evidence_types": [
                        "policy", "procedure", "configuration", "records", "report",
                    ],
                    "questions": questions,
                })
            categories.append({
                "category_id": cat["code"],
                "category_name": f"{cat['code']} — {cat['name']}",
                "criteria_statement": cat["description"] or None,
                "description": cat["description"] or None,
                "controls": controls,
            })
        domains.append({
            "domain_id": fn["code"],
            "domain_name": fn["name"],
            "description": fn["description"],
            "categories": categories,
        })

    return {
        "framework_id": "NIST_CSF_2.0",
        "name": "NIST CSF 2.0",
        "version": "2.0",
        "source_format": "pdf",
        "source_document": SOURCE_PDF.name,
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "domains": domains,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions-per-control", type=int, choices=(1, 2), default=1,
                    help="1 → 106 questions (yes/no only); 2 → adds an evidence-upload question")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SOURCE_PDF.exists():
        sys.exit(f"Source PDF not found: {SOURCE_PDF}")

    lines = extract_outline_lines(SOURCE_PDF)
    functions = parse_core(lines)
    canonical = build_canonical(functions, args.questions_per_control)

    # ── validation: the parse must reproduce the official Core exactly ──
    per_fn, ids, n_cats, n_q = {}, [], 0, 0
    for d in canonical["domains"]:
        n = 0
        for cat in d["categories"]:
            n_cats += 1
            for c in cat["controls"]:
                ids.append(c["control_id"])
                n_q += len(c["questions"])
                n += 1
        per_fn[d["domain_id"]] = n

    print(f"functions:  {len(canonical['domains'])}")
    print(f"categories: {n_cats}")
    print(f"controls:   {len(ids)}  (unique {len(set(ids))})")
    print(f"questions:  {n_q}")
    print(f"per function: {per_fn}")

    problems = []
    if len(ids) != EXPECTED_TOTAL:
        problems.append(f"expected {EXPECTED_TOTAL} subcategories, parsed {len(ids)}")
    if len(set(ids)) != len(ids):
        problems.append("duplicate control IDs parsed")
    if per_fn != EXPECTED_PER_FUNCTION:
        problems.append(f"per-function counts {per_fn} != official {EXPECTED_PER_FUNCTION}")
    for cid in ids:
        if not re.fullmatch(r"(?:GV|ID|PR|DE|RS|RC)\.[A-Z]{2}-\d{2}", cid):
            problems.append(f"malformed control id: {cid}")
    if problems:
        for p in problems:
            print("  ERROR:", p)
        sys.exit("Parse failed validation — canonical file NOT written.")
    print("validation: OK — matches the official NIST CSF 2.0 Core exactly")

    if args.dry_run:
        print("(dry run — nothing written)")
        return

    for out in (OUT_CANONICAL, OUT_MIRROR):
        if out.exists():
            backup = out.with_suffix(out.suffix + ".sparse35.bak")
            if not backup.exists():
                backup.write_bytes(out.read_bytes())
                print(f"backed up previous file -> {backup.name}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(canonical, fh, indent=2, ensure_ascii=False)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
