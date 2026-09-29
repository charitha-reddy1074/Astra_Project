def detect_framework(filename: str) -> str:
    """Detect framework type from filename. Returns a framework code string."""
    name = filename.lower()

    if "nist" in name or "csf" in name:
        return "NIST"

    if "iso" in name or "27001" in name:
        return "ISO27001"

    if "cis" in name:
        return "CIS"

    if "pci" in name or "dss" in name:
        return "PCI_DSS"

    if "market" in name:
        return "MARKET_ASSESSMENT"

    return "UNKNOWN"
