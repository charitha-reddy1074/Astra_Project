"""
Generic chunk builder for any framework following the canonical JSON schema:
    Framework → domains → categories → controls

Produces one chunk per control. Works for NIST, ISO, CIS, Market Assessment,
and any future framework — no framework-specific code required.
"""


def build_chunks(framework: dict) -> list[dict]:
    chunks = []
    fw_code = framework["code"]
    fw_name = framework["name"]
    fw_version = framework.get("version", "")

    for domain in framework.get("domains", []):
        # Frameworks with a flat domain → control structure (NIST, CIS, PCI DSS)
        # get one synthetic pass-through category, mirroring the importer so the
        # nesting is uniform here too. Without this they produce zero chunks.
        categories = domain.get("categories") or [{
            "code": domain.get("code"),
            "name": None,
            "controls": domain.get("controls", []),
        }]
        for category in categories:
            for control in category.get("controls", []):
                statement = (control.get("statement") or "").replace("\n", " ").strip()
                if not statement or len(statement) < 10:
                    continue

                content = _build_content(fw_name, fw_version, domain, category, control, statement)

                chunks.append({
                    "chunk_id":     f"{fw_code}_{control['code']}",
                    "framework":    fw_name,
                    "framework_code": fw_code,
                    "version":      fw_version,
                    "domain":       domain.get("name") or "",
                    "category":     category.get("name") or "",
                    "control_code": control["code"],
                    "control_name": control.get("name", ""),
                    "statement":    statement,
                    "content":      content,
                    "metadata": {
                        "framework":    fw_code,
                        "version":      fw_version,
                        "domain":       domain.get("name") or "",
                        "category":     category.get("name") or "",
                        "control_id":   control["code"],
                    },
                })

    return chunks


def _build_content(fw_name, fw_version, domain, category, control, statement) -> str:
    name = control.get("name", "")
    label = f"{control['code']} - {name}" if name else control["code"]
    domain_name = domain.get("name") or ""
    category_name = category.get("name") or ""
    parts = [
        f"Framework: {fw_name}\n",
        f"Version: {fw_version}\n\n",
        f"Domain: {domain_name}\n",
    ]
    # Synthetic pass-through categories have no name — omit the line entirely.
    if category_name:
        parts.append(f"Category: {category_name}\n")
    parts.append(f"\nControl: {label}\n\n")
    parts.append(f"Requirement:\n{statement}")
    return "".join(parts)
