"""
ingestion/parsers/__init__.py — Format-specific text extractors.

Each parser accepts a file path and returns List[RawChunk] — raw text blocks
with optional page/section provenance.  No chunking logic lives here; these
parsers are pure text extractors.  All semantic chunking is done downstream by
chunker.HierarchicalChunker.

Supported formats:
  PDF  — pdfplumber with multi-column layout detection + regex control-block splitting
  JSON — recursive depth-limited walk matching ID+text key patterns
  XML  — lxml recursive walk on control-hint tag names
  XLSX — openpyxl row serialisation
  CSV  — csv.DictReader row serialisation

Ported from compliance_ingestion/parsers/__init__.py and kept standalone so
this package has no dependency on the compliance_ingestion project.
"""

from __future__ import annotations

import csv
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, List, Optional


# ---------------------------------------------------------------------------
# Shared data class
# ---------------------------------------------------------------------------

@dataclass
class RawChunk:
    text: str
    source_page: Optional[int] = None
    source_section: Optional[str] = None
    source_format: str = "unknown"


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseParser(ABC):
    @abstractmethod
    def parse(self, file_path: str) -> List[RawChunk]:
        pass


# ---------------------------------------------------------------------------
# PDF parser  (pdfplumber)
# ---------------------------------------------------------------------------

_PDF_PATTERNS = [
    # NIST SP 800-53: AC-2, SI-7(1)
    r"(?m)^(?P<id>[A-Z]{2,4}-\d+(?:\s*\(\d+\))?)\s*[:\-]?\s*(?P<rest>[^\n]+(?:\n(?![A-Z]{2,4}-\d)[^\n]+){0,20})",
    # NIST CSF 2.0: ID.AM-1, PR.AC-3
    r"(?m)^(?P<id>[A-Z]{2,3}\.[A-Z]{2,4}-\d+)\s*[:\-]\s*(?P<rest>[^\n]+(?:\n(?![A-Z]{2,3}\.[A-Z]{2,4}-\d)[^\n]+){0,20})",
    # PCI DSS pure numeric on its own line (e.g. "3.2.1 PAN is secured...")
    r"(?m)^(?P<id>\d{1,2}\.\d+(?:\.\d+)?)\s+(?P<rest>[A-Z][^\n]+(?:\n(?!\d{1,2}\.\d)[^\n]+){0,20})",
    # PCI DSS: Requirement 1.2.3
    r"(?m)^(?P<id>Requirement\s+\d+(?:\.\d+)+)\s*[:\-]?\s*(?P<rest>[^\n]+(?:\n(?!Requirement)[^\n]+){0,20})",
    # CIS: Control 1.1 / Safeguard 1.1
    r"(?m)^(?P<id>(?:Control|Safeguard)\s+\d+(?:\.\d+)+)\s*[:\-]?\s*(?P<rest>[^\n]+(?:\n(?!(?:Control|Safeguard)\s+\d)[^\n]+){0,20})",
    # ISO 27001 / alphanumeric: A.9.2.1 or 8.1.1
    r"(?m)^(?P<id>[A-Z]\.\d+(?:\.\d+){1,3})\s+(?P<rest>[^\n]+(?:\n(?![A-Z]\.\d)[^\n]+){0,20})",
]

# Strip testing sub-procedure lines like "3.2.1.a Examine the policy…"
_TESTING_PROC_RE = re.compile(r"^\s*\d+\.\d+(?:\.\d+)+\.[a-z]\s")

# Block-start markers for guidance / rationale / notes (not requirements)
_GUIDANCE_BLOCK_RE = re.compile(
    r"^(?:Note|Guidance|Good Practice|Rationale|Applicability Notes?|"
    r"Customized Approach Objective|Defined Approach Testing Procedures|"
    r"Examples?|Further Information)\s*[:.]?\s*$",
    re.IGNORECASE,
)

# PCI-DSS Requirement number → canonical domain name
_PCI_DOMAIN_MAP = {
    "1": "Network Security Controls",
    "2": "Secure Configurations",
    "3": "Account Data Protection",
    "4": "Cryptography in Transit",
    "5": "Anti-Malware",
    "6": "Secure Development",
    "7": "Access Control",
    "8": "Authentication",
    "9": "Physical Access",
    "10": "Log Management",
    "11": "Security Testing",
    "12": "Information Security Policies",
}

# NIST CSF 2.0 function prefix → domain name
_NIST_CSF_DOMAIN_MAP = {
    "GV": "Govern", "ID": "Identify", "PR": "Protect",
    "DE": "Detect", "RS": "Respond", "RC": "Recover",
}


def _infer_section(control_id: str) -> Optional[str]:
    """Map a control ID to its canonical domain/section name (for source_section hint)."""
    pci = re.match(r"^(\d{1,2})\.", control_id)
    if pci:
        return _PCI_DOMAIN_MAP.get(pci.group(1))
    csf = re.match(r"^([A-Z]{2,3})\.", control_id)
    if csf:
        return _NIST_CSF_DOMAIN_MAP.get(csf.group(1))
    return None


def _left_column_boundary(words: list, page_width: float) -> float:
    """
    Detect a true two-column layout by finding a vertical whitespace *gutter*.

    A genuine multi-column PDF has a continuous vertical band in the central
    region of the page that NO word occupies (the gutter between columns). We
    look for the widest such empty band; its left edge marks where the right
    column begins, so callers keep only words to its left.

    Crucially, we test the full horizontal extent of each word ([x0, x1]) — not
    just its start — because ordinary single-column prose has word-starts
    scattered across the whole width and would otherwise be mistaken for a
    second column. When no real gutter exists we return page_width unchanged so
    that NO text is dropped (safe default for single-column documents).
    """
    if not words or len(words) < 20:
        return page_width

    BIN = 6  # px per bucket
    nbins = int(page_width // BIN) + 2
    occupied = [False] * nbins
    for w in words:
        x0 = float(w.get("x0", 0.0))
        x1 = float(w.get("x1", x0))
        for b in range(int(x0 // BIN), int(x1 // BIN) + 1):
            if 0 <= b < nbins:
                occupied[b] = True

    # Find the widest empty run whose midpoint lies in the central band.
    lo, hi = page_width * 0.30, page_width * 0.70
    best_start, best_len = None, 0
    run_start: Optional[int] = None
    for b in range(nbins):
        if not occupied[b]:
            if run_start is None:
                run_start = b
        elif run_start is not None:
            mid_px = (run_start + b) / 2 * BIN
            run_px = (b - run_start) * BIN
            if lo <= mid_px <= hi and run_px > best_len:
                best_start, best_len = run_start, run_px
            run_start = None

    # Require a meaningful gutter (>= 4% of page width) to call it two-column.
    if best_start is not None and best_len >= page_width * 0.04:
        return best_start * BIN

    return page_width


class PDFParser(BaseParser):
    def parse(self, file_path: str) -> List[RawChunk]:
        try:
            import pdfplumber
        except ImportError:
            raise ImportError("pdfplumber is required: pip install pdfplumber")

        full_text = ""
        page_offsets: List[tuple] = []

        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = self._page_to_text(page)
                page_offsets.append((len(full_text), page_num))
                full_text += text + "\n"

        full_text = self._strip_non_requirement_lines(full_text)
        chunks = self._split_by_controls(full_text, page_offsets)
        if not chunks:
            chunks = self._split_by_paragraphs(full_text)
        return chunks

    @staticmethod
    def _page_to_text(page) -> str:
        """Extract text from the leftmost column only when multi-column layout is detected."""
        words = page.extract_words()
        if not words:
            return page.extract_text() or ""

        page_width = float(page.width or 600)
        boundary = _left_column_boundary(words, page_width)

        # Only restrict to the left column when a genuine gutter was detected;
        # otherwise keep every word (single-column pages must not lose content).
        if boundary < page_width * 0.95:
            words = [w for w in words if w["x0"] < boundary]

        if not words:
            return page.extract_text() or ""

        words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
        lines: List[str] = []
        current_line: List[str] = []
        current_y: Optional[float] = None
        for w in words:
            y = round(w["top"], 1)
            if current_y is None or abs(y - current_y) <= 3:
                current_line.append(w["text"])
                current_y = y
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [w["text"]]
                current_y = y
        if current_line:
            lines.append(" ".join(current_line))
        return "\n".join(lines)

    @staticmethod
    def _strip_non_requirement_lines(text: str) -> str:
        """Remove standalone guidance-header lines and testing sub-procedure lines."""
        lines = text.splitlines()
        out: List[str] = []
        for line in lines:
            if _GUIDANCE_BLOCK_RE.match(line.strip()):
                continue
            if _TESTING_PROC_RE.match(line):
                continue
            out.append(line)
        return "\n".join(out)

    def _find_page(self, offset: int, page_offsets: List[tuple]) -> int:
        for start, pnum in reversed(page_offsets):
            if offset >= start:
                return pnum
        return 1

    def _split_by_controls(self, text: str, page_offsets: List[tuple]) -> List[RawChunk]:
        for pattern in _PDF_PATTERNS:
            matches = list(re.finditer(pattern, text, re.MULTILINE))
            if len(matches) >= 5:
                seen_ids: set = set()
                chunks: List[RawChunk] = []
                for m in matches:
                    block = m.group(0).strip()
                    cid = m.group("id").strip()
                    if len(block) < 60 or cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                    chunks.append(RawChunk(
                        text=block,
                        source_page=self._find_page(m.start(), page_offsets),
                        source_section=_infer_section(cid),
                        source_format="pdf",
                    ))
                return chunks
        return []

    def _split_by_paragraphs(self, text: str) -> List[RawChunk]:
        return [
            RawChunk(text=p.strip(), source_format="pdf")
            for p in re.split(r"\n{2,}", text)
            if len(p.strip()) >= 100
        ]


# ---------------------------------------------------------------------------
# JSON parser
# ---------------------------------------------------------------------------

_ID_KEYS   = {"id", "control_id", "safeguard_id", "req_id", "identifier", "number", "cis_id"}
_TEXT_KEYS = {
    "description", "statement", "requirement", "text", "content",
    "rationale", "guidance", "detail", "title", "name",
}


class JSONParser(BaseParser):
    def parse(self, file_path: str) -> List[RawChunk]:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        chunks: List[RawChunk] = []
        self._walk(data, chunks)
        return chunks

    def _walk(self, obj: Any, chunks: List[RawChunk], depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(obj, dict):
            if (_ID_KEYS & set(obj)) and (_TEXT_KEYS & set(obj)):
                parts = []
                for k, v in obj.items():
                    if isinstance(v, str) and v.strip():
                        parts.append(f"{k}: {v.strip()}")
                    elif isinstance(v, list) and all(isinstance(i, str) for i in v):
                        parts.append(f"{k}: {', '.join(v)}")
                text = "\n".join(parts)
                if len(text) >= 60:
                    chunks.append(RawChunk(text=text, source_format="json"))
            for v in obj.values():
                self._walk(v, chunks, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._walk(item, chunks, depth + 1)


# ---------------------------------------------------------------------------
# XML parser  (lxml)
# ---------------------------------------------------------------------------

_XML_CONTROL_HINTS = {"control", "requirement", "safeguard", "rule", "policy", "item", "measure"}


class XMLParser(BaseParser):
    def parse(self, file_path: str) -> List[RawChunk]:
        try:
            from lxml import etree
        except ImportError:
            raise ImportError("lxml is required: pip install lxml")

        root = etree.parse(file_path).getroot()
        chunks: List[RawChunk] = []
        self._walk(root, chunks)
        return chunks

    @staticmethod
    def _local(tag: str) -> str:
        return tag.split("}")[-1] if "}" in tag else tag

    def _walk(self, elem, chunks: List[RawChunk], depth: int = 0) -> None:
        if depth > 12:
            return
        tag = self._local(elem.tag).lower()
        if any(h in tag for h in _XML_CONTROL_HINTS):
            parts = []
            for child in elem:
                ct = self._local(child.tag)
                cv = (child.text or "").strip()
                if cv:
                    parts.append(f"{ct}: {cv}")
            if elem.text and elem.text.strip():
                parts.insert(0, elem.text.strip())
            text = "\n".join(parts)
            if len(text) >= 60:
                chunks.append(RawChunk(
                    text=f"{self._local(elem.tag)}:\n{text}",
                    source_format="xml",
                ))
        for child in elem:
            self._walk(child, chunks, depth + 1)


# ---------------------------------------------------------------------------
# Excel / CSV parser  (openpyxl)
# ---------------------------------------------------------------------------

class ExcelParser(BaseParser):
    def parse(self, file_path: str) -> List[RawChunk]:
        ext = file_path.rsplit(".", 1)[-1].lower()
        return self._parse_csv(file_path) if ext == "csv" else self._parse_xlsx(file_path)

    def _parse_xlsx(self, file_path: str) -> List[RawChunk]:
        try:
            import openpyxl
        except ImportError:
            raise ImportError("openpyxl is required: pip install openpyxl")

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        chunks: List[RawChunk] = []
        for sheet in wb.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            if len(rows) < 2:
                continue
            headers = [str(h or "").strip() for h in rows[0]]
            for row in rows[1:]:
                if not any(row):
                    continue
                parts = [
                    f"{headers[i]}: {str(v).strip()}"
                    for i, v in enumerate(row)
                    if i < len(headers) and v is not None and str(v).strip()
                ]
                text = "\n".join(parts)
                if len(text) >= 60:
                    chunks.append(RawChunk(text=text, source_format="xlsx"))
        wb.close()
        return chunks

    def _parse_csv(self, file_path: str) -> List[RawChunk]:
        chunks: List[RawChunk] = []
        with open(file_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                parts = [f"{k}: {v.strip()}" for k, v in row.items() if v and v.strip()]
                text = "\n".join(parts)
                if len(text) >= 60:
                    chunks.append(RawChunk(text=text, source_format="csv"))
        return chunks


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def get_parser(file_path: str) -> BaseParser:
    """Return the appropriate parser based on file extension."""
    ext = file_path.rsplit(".", 1)[-1].lower()
    return {
        "pdf":  PDFParser(),
        "json": JSONParser(),
        "xml":  XMLParser(),
        "xlsx": ExcelParser(),
        "csv":  ExcelParser(),
    }.get(ext, PDFParser())
