"""Adapter registry.

Explicit, ordered resolution: the first adapter whose aliases appear in the
framework's code/name wins. Ordering is significant — put the most specific
aliases first.
"""
from __future__ import annotations

from backend.api.compliance.adapters.base import FrameworkAdapter
from backend.api.compliance.adapters.nist import NistCsf20Adapter
from backend.api.compliance.adapters.soc2 import Soc2Type1Adapter

#: Ordered registry. The compliance evaluation surface is scoped to these two.
_REGISTRY: tuple[type[FrameworkAdapter], ...] = (
    Soc2Type1Adapter,
    NistCsf20Adapter,
)

_ADAPTERS: dict[str, FrameworkAdapter] = {}


def _adapter_for(cls: type[FrameworkAdapter]) -> FrameworkAdapter:
    cached = _ADAPTERS.get(cls.key)
    if cached is None:
        cached = cls()
        _ADAPTERS[cls.key] = cached
    return cached


def list_adapters() -> list[FrameworkAdapter]:
    """Every registered adapter, in resolution order."""
    return [_adapter_for(cls) for cls in _REGISTRY]


def supported_framework_keys() -> list[str]:
    return [cls.key for cls in _REGISTRY]


def get_adapter(*candidates: str | None) -> FrameworkAdapter | None:
    """Resolve an adapter from a framework code, name or dataset id.

    Returns None for any framework outside the supported scope (NIST CSF and
    SOC 2 Type 1) — such a framework can still be ingested and assessed through
    the existing endpoints, it simply has no compliance evaluation profile.
    """
    for cls in _REGISTRY:
        if cls.matches(*candidates):
            return _adapter_for(cls)
    return None


__all__ = [
    "FrameworkAdapter",
    "NistCsf20Adapter",
    "Soc2Type1Adapter",
    "get_adapter",
    "list_adapters",
    "supported_framework_keys",
]
