"""Rebuild the ISO/IEC 27001:2022 canonical JSON from the authoritative PDF.

The previously shipped canonical file was produced by the LLM extraction pipeline
and was truncated to 91 of Annex A's 93 controls (7.3 "Securing offices, rooms
and facilities" and 7.5 "Protecting against physical and environmental threats"
were missing entirely), and its statements carried the PDF's soft-hyphen line
breaks as literal spaces ("information secu rity", "de fined").

Annex A / Table A.1 of ISO/IEC 27001:2022 extracts from PyMuPDF as a very regular
line stream — one cell per line, in reading order:

    5                                  <- theme number
    Organizational controls            <- theme name
    5.1                                <- control number
    Policies for information secu­     <- control title (soft-hyphen wrapped)
    rity
    Control                            <- the table's "Control" label
    Information security policy and …  <- the control statement (verbatim)

That structure is parsed deterministically here — no LLM — so every control ID,
title and statement is verbatim from the source, and the result is reproducible.

Produces the "root canonical" shape (framework_id / domain_id / control_id) that
backend/api/services/framework_service.py normalises on import, with one category
per Annex A theme so the framework nests theme → category → control → question.

Usage:
    python scripts/build_iso_27001_canonical.py [--questions-per-control {1,2}] [--dry-run]
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
SOURCE_PDF = PROJECT_ROOT / "data" / "frameworks" / "pdf" / "ISO_IEC-270012022-ed.3.pdf"
OUT_CANONICAL = PROJECT_ROOT / "backend" / "api" / "data" / "canonical" / "iso_27001_2022.json"
OUT_MIRROR = PROJECT_ROOT / "data" / "frameworks" / "json" / "iso_27001_2022.json"
BACKUP_SUFFIX = ".sparse91.bak"

# Official ISO/IEC 27001:2022 Annex A themes and their control counts — a hard
# check that the parse captured the whole of Table A.1 and nothing extra.
THEMES = {
    "5": "Organizational controls",
    "6": "People controls",
    "7": "Physical controls",
    "8": "Technological controls",
}
EXPECTED_TOTAL = 93
EXPECTED_PER_THEME = {"5": 37, "6": 8, "7": 14, "8": 34}

TABLE_START_RE = re.compile(r"^Table\s*A\.1\s*—\s*Information security controls$")
THEME_RE = re.compile(r"^([5-8])$")
CONTROL_RE = re.compile(r"^([5-8]\.\d{1,2})$")
CONTROL_LABEL = "Control"
# Page furniture / table headers to drop before parsing.
NOISE_RE = re.compile(
    r"^(?:ISO/IEC 27001:2022\(E\)"
    r"|Table A\.1 \(continued\)"
    r"|©\s*ISO/IEC 2022 – All rights reserved"
    r"|SNV / licensed to .*"
    # Page numbers — 2+ digits only, so the single-digit theme headers survive.
    r"|\d{2,3})$"
)


def normalise(text: str) -> str:
    """Normalise the PDF's typographic punctuation and invisible characters.

    Soft hyphens (U+00AD) are preserved: they mark hyphenation at a line break
    and are what lets wrapped words be rejoined without a spurious space.
    """
    text = unicodedata.normalize("NFKC", text.replace("­", "\x00"))
    text = text.replace("\x00", "­")
    for ch in ("﻿", "​", "\x08"):
        text = text.replace(ch, "")
    text = (text.replace("’", "'").replace("‘", "'")
                .replace("“", '"').replace("”", '"')
                .replace(" ", " ").replace(" ", " "))
    # Collapse runs of whitespace but keep a trailing soft hyphen attached.
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_table_lines(pdf_path: Path) -> list[str]:
    """Return the Table A.1 cell stream as cleaned lines, in reading order."""
    doc = fitz.open(pdf_path)
    raw: list[str] = []
    for page in doc:
        raw.extend(page.get_text().splitlines())
    doc.close()

    start = next((i for i, ln in enumerate(raw) if TABLE_START_RE.match(normalise(ln))), None)
    if start is None:
        sys.exit("Could not locate 'Table A.1 — Information security controls' in the PDF.")

    lines: list[str] = []
    for ln in raw[start + 1:]:
        s = normalise(ln)
        # The Bibliography heading ends Annex A; without this the last control's
        # statement would absorb the reference list that follows it.
        if s == "Bibliography":
            break
        if not s or NOISE_RE.match(s):
            continue
        lines.append(s)
    return lines


def join_cell(parts: list[str]) -> str:
    """Join wrapped lines of one table cell into a single string.

    A line ending in a soft hyphen was split mid-word by the typesetter, so it
    is rejoined with no separator; every other break was a plain wrap.
    """
    out = ""
    for part in parts:
        if not out:
            out = part
        elif out.endswith("­"):
            out = out[:-1] + part
        else:
            out += " " + part
    return re.sub(r"\s+", " ", out).replace("­", "").strip()


def parse_table(lines: list[str]) -> list[dict]:
    """Parse the cell stream into [{code, name, controls:[{id,title,statement}]}]."""
    themes: list[dict] = []
    by_code: dict[str, dict] = {}
    cur_theme: dict | None = None
    cur_ctrl: dict | None = None
    bucket: list[str] = []          # lines of the cell currently being read
    reading: str | None = None      # "title" | "statement"

    def flush() -> None:
        nonlocal bucket, reading
        if cur_ctrl is not None and reading and bucket:
            text = join_cell(bucket)
            if reading == "title":
                cur_ctrl["title"] = (cur_ctrl["title"] + " " + text).strip() if cur_ctrl["title"] else text
            else:
                cur_ctrl["statement"] = (
                    (cur_ctrl["statement"] + " " + text).strip() if cur_ctrl["statement"] else text
                )
        bucket = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Theme header: the number, immediately followed by its official name.
        m = THEME_RE.match(line)
        if m and i + 1 < len(lines) and lines[i + 1] == THEMES.get(m.group(1)):
            flush()
            reading = None
            cur_ctrl = None
            code = m.group(1)
            cur_theme = by_code.get(code)
            if cur_theme is None:
                cur_theme = {"code": code, "name": THEMES[code], "controls": []}
                themes.append(cur_theme)
                by_code[code] = cur_theme
            i += 2
            continue

        m = CONTROL_RE.match(line)
        if m and cur_theme is not None and m.group(1).split(".")[0] == cur_theme["code"]:
            flush()
            cid = m.group(1)
            existing = next((c for c in cur_theme["controls"] if c["control_id"] == cid), None)
            if existing is not None:
                # Continuation of a control split across pages (never seen in the
                # 2022 edition, but keep the parse idempotent rather than dupe).
                cur_ctrl = existing
            else:
                cur_ctrl = {"control_id": cid, "title": "", "statement": ""}
                cur_theme["controls"].append(cur_ctrl)
            reading = "title"
            i += 1
            continue

        if cur_ctrl is not None:
            # The "Control" label separates the title cell from the statement
            # cell. Narrow title cells put it on its own line; wide ones append
            # it to the last title line ("Equipment siting and protection Control").
            if line == CONTROL_LABEL:
                flush()
                reading = "statement"
                i += 1
                continue
            if line.endswith(" " + CONTROL_LABEL) and reading == "title":
                bucket.append(line[: -len(CONTROL_LABEL)].rstrip())
                flush()
                reading = "statement"
                i += 1
                continue
            if reading:
                bucket.append(line)
        i += 1

    flush()
    return themes


def build_canonical(themes: list[dict], questions_per_control: int) -> dict:
    evidence = ["policy", "procedure", "configuration", "records", "report"]
    domains = []
    for theme in themes:
        controls = []
        for ctrl in theme["controls"]:
            cid, title, stmt = ctrl["control_id"], ctrl["title"], ctrl["statement"]
            questions = [{
                "question_id": f"{cid}-Q1",
                "question_text": (
                    f"Is the organization implementing and operating the control "
                    f"{cid} {title}: {stmt}"
                ),
                "question_type": "yes_no",
                "weight": 1.0,
                "expected_evidence_types": evidence,
            }]
            if questions_per_control == 2:
                questions.append({
                    "question_id": f"{cid}-Q2",
                    "question_text": (
                        f"Provide evidence demonstrating implementation of {cid} {title}: {stmt}"
                    ),
                    "question_type": "evidence_upload",
                    "weight": 1.0,
                    "expected_evidence_types": evidence,
                })
            controls.append({
                "control_id": cid,
                "control_name": title,
                # framework_service maps `sub_topic` to the DB control name.
                "sub_topic": title,
                "control_statement": stmt,
                "criticality": "standard",
                "cross_references": [],
                "expected_evidence_types": evidence,
                "questions": questions,
            })
        category = {
            "category_id": f"A.{theme['code']}",
            "category_name": f"A.{theme['code']} — {theme['name']}",
            "criteria_statement": None,
            "description": None,
            "controls": controls,
        }
        domains.append({
            "domain_id": theme["code"],
            "domain_name": theme["name"],
            "description": (
                f"ISO/IEC 27001:2022 Annex A (normative) Table A.1 — {theme['name']} "
                f"({theme['code']}.1–{theme['code']}.{len(controls)})."
            ),
            "categories": [category],
        })

    return {
        "framework_id": "iso-iec-27001-2022",
        "name": "ISO/IEC 27001:2022",
        "version": "2022",
        "source_format": "pdf",
        "source_document": SOURCE_PDF.name,
        "ingested_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "domains": domains,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions-per-control", type=int, choices=(1, 2), default=1,
                    help="1 → 93 questions (yes/no only); 2 → adds an evidence-upload question")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not SOURCE_PDF.exists():
        sys.exit(f"Source PDF not found: {SOURCE_PDF}")

    lines = extract_table_lines(SOURCE_PDF)
    themes = parse_table(lines)
    canonical = build_canonical(themes, args.questions_per_control)

    # ── validation: the parse must reproduce Annex A Table A.1 exactly ──
    per_theme, ids, n_cats, n_q = {}, [], 0, 0
    empty: list[str] = []
    for d in canonical["domains"]:
        n = 0
        for cat in d["categories"]:
            n_cats += 1
            for c in cat["controls"]:
                ids.append(c["control_id"])
                n_q += len(c["questions"])
                n += 1
                if not c["control_statement"] or not c["control_name"]:
                    empty.append(c["control_id"])
        per_theme[d["domain_id"]] = n

    print(f"themes:     {len(canonical['domains'])}")
    print(f"categories: {n_cats}")
    print(f"controls:   {len(ids)}  (unique {len(set(ids))})")
    print(f"questions:  {n_q}")
    print(f"per theme:  {per_theme}")

    problems = []
    if len(ids) != EXPECTED_TOTAL:
        problems.append(f"expected {EXPECTED_TOTAL} controls, parsed {len(ids)}")
    if len(set(ids)) != len(ids):
        problems.append("duplicate control IDs parsed")
    if per_theme != EXPECTED_PER_THEME:
        problems.append(f"per-theme counts {per_theme} != official {EXPECTED_PER_THEME}")
    for cid in ids:
        if not re.fullmatch(r"[5-8]\.\d{1,2}", cid):
            problems.append(f"malformed control id: {cid}")
    # Every theme must be a gapless 1..n run (no silently dropped control).
    for theme_code, count in EXPECTED_PER_THEME.items():
        expected_ids = [f"{theme_code}.{i}" for i in range(1, count + 1)]
        got = [c for c in ids if c.startswith(theme_code + ".")]
        if got != expected_ids:
            missing = [c for c in expected_ids if c not in got]
            extra = [c for c in got if c not in expected_ids]
            problems.append(
                f"theme {theme_code} ids off-sequence (missing {missing}, extra {extra})"
            )
    if empty:
        problems.append(f"controls with an empty title or statement: {empty}")
    # Every Table A.1 control statement is a single sentence ending in a period;
    # anything longer than ~500 chars means a neighbouring cell bled in.
    for d in canonical["domains"]:
        for cat in d["categories"]:
            for c in cat["controls"]:
                stmt = c["control_statement"]
                if not stmt.endswith("."):
                    problems.append(f"{c['control_id']}: statement does not end in a period")
                if len(stmt) > 500:
                    problems.append(
                        f"{c['control_id']}: statement is {len(stmt)} chars — page furniture bled in"
                    )
    if n_q != len(ids) * args.questions_per_control:
        problems.append(f"question count {n_q} != controls × {args.questions_per_control}")
    if problems:
        for p in problems:
            print("  ERROR:", p)
        sys.exit("Parse failed validation — canonical file NOT written.")
    print("validation: OK — matches ISO/IEC 27001:2022 Annex A Table A.1 exactly")

    if args.dry_run:
        print("(dry run — nothing written)")
        return

    for out in (OUT_CANONICAL, OUT_MIRROR):
        if out.exists():
            backup = out.with_suffix(out.suffix + BACKUP_SUFFIX)
            if not backup.exists():
                backup.write_bytes(out.read_bytes())
                print(f"backed up previous file -> {backup.name}")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(canonical, fh, indent=2, ensure_ascii=False)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
