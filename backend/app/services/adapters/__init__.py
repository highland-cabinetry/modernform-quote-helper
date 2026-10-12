"""Document adapter registry.

Each supported layout is one adapter with its own tests. Adding a company format means
adding (or configuring) an adapter; the packing engine and other adapters are untouched.
"""

from __future__ import annotations

from .base import ImportContext, ParsedLine, ParsedOrder, ParseOutcome
from .highland import HighlandPdfAdapter
from .modernform import ModernformPdfAdapter
from .table import TableAdapter
from .text_layout import TextLayoutAdapter

ADAPTERS = {
    adapter.name: adapter
    for adapter in (HighlandPdfAdapter(), ModernformPdfAdapter(), TextLayoutAdapter(), TableAdapter())
}


def adapter_catalog() -> list[dict[str, str]]:
    return [{"name": a.name, "label": a.label, "description": a.description} for a in ADAPTERS.values()]


def choose_adapter(context: ImportContext, enabled: list[str]):
    """Pick the enabled adapter that is most confident it can read the file.

    Returns (adapter, score). Ties keep the company's configured priority order.
    """

    best = None
    best_score = 0.0
    for name in enabled:
        adapter = ADAPTERS.get(name)
        if adapter is None:
            continue
        try:
            score = float(adapter.score(context))
        except Exception:
            score = 0.0
        if score > best_score:
            best, best_score = adapter, score
    return best, best_score


__all__ = [
    "ADAPTERS",
    "ImportContext",
    "ParseOutcome",
    "ParsedLine",
    "ParsedOrder",
    "adapter_catalog",
    "choose_adapter",
]
