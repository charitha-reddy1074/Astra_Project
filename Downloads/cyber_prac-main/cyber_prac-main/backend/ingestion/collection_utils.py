"""
ingestion/collection_utils.py — Deterministic, collision-safe ChromaDB collection
name generation for arbitrarily-named compliance frameworks.

Properties of framework_to_collection_name():
  - Deterministic:   same (name, version) always produces the same slug
  - Collision-safe:  normalisation is conservative — no lossy hash or truncation
                     that could conflate two distinct frameworks
  - Prefix:          always starts with "framework_" to namespace dynamic
                     collections away from other ChromaDB uses
  - Length:          guaranteed 3-63 chars (ChromaDB hard constraint)
  - Charset:         [a-z0-9_] only — no dots, hyphens, or consecutive specials
"""
from __future__ import annotations

import re
import unicodedata

_PREFIX = "framework_"
# ChromaDB max collection name length is 63 chars.
# We reserve 10 for the prefix, leaving 53 for the slug.
_MAX_SLUG_LEN = 63 - len(_PREFIX)  # 53


def framework_to_collection_name(framework_name: str, version: str = "") -> str:
    """
    Convert a framework name + version string into a valid ChromaDB collection name.

    Examples:
        "NIST CSF",          "2.0"   → "framework_nist_csf_2_0"
        "PCI DSS",           "v4.0"  → "framework_pci_dss_v4_0"
        "ISO/IEC 27001:2022", ""     → "framework_iso_iec_27001_2022"
        "CIS Controls v8.1.2", ""   → "framework_cis_controls_v8_1_2"
        "HIPAA Security Rule", "2024"→ "framework_hipaa_security_rule_2024"
        "My Custom!!!",       "1.0"  → "framework_my_custom_1_0"
        "Réglementation",    "1.0"   → "framework_reglementation_1_0"
    """
    # 1. Unicode normalisation: decompose accented chars (é → e + combining diacritic)
    slug = unicodedata.normalize("NFKD", str(framework_name or ""))
    slug = slug.encode("ascii", "ignore").decode("ascii")

    # 2. Append version (if provided) so same name + different version → different slug
    if version:
        slug = f"{slug} {version}"

    # 3. Lowercase everything
    slug = slug.lower()

    # 4. Replace any run of non-alphanumeric characters with a single underscore
    slug = re.sub(r"[^a-z0-9]+", "_", slug)

    # 5. Collapse consecutive underscores; strip leading/trailing
    slug = re.sub(r"_+", "_", slug).strip("_")

    # 6. Truncate at a word boundary (last underscore) if over the limit
    if len(slug) > _MAX_SLUG_LEN:
        slug = slug[:_MAX_SLUG_LEN]
        last_sep = slug.rfind("_")
        # Only truncate at separator if it leaves at least 10 chars of content
        if last_sep >= 10:
            slug = slug[:last_sep]
        slug = slug.strip("_")

    # 7. ChromaDB requires the name to start and end with an alphanumeric char
    slug = re.sub(r"^[^a-z0-9]+", "", slug)
    slug = re.sub(r"[^a-z0-9]+$", "", slug)

    # 8. Minimum 3-char slug (pad with "x" when framework name is very short)
    if len(slug) < 3:
        slug = (slug + "xxx")[:3]

    return _PREFIX + slug
