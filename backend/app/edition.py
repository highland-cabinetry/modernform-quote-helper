"""Edition profile: the company-specific defaults of this build.

The pallet engine, validator, import, matching, Build2Quote and update code are shared with
Highland Cabinetry's Quote Helper (upstream: highland-cabinetry/freight-manager). What makes this
build *Modernform Quote Helper* is collected here, so upstream improvements can be ported without
touching these choices:

* the company created in a new database (name, document formats, product rule set, ship-from);
* the starter product catalog and packing settings written on first start (``seed/``).

``release.json`` names the edition. The environment variable ``QUOTE_HELPER_EDITION`` overrides it;
the regression tests use that to run the shared upstream tests against the upstream defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .release import RELEASE

EDITION_ENV = "QUOTE_HELPER_EDITION"
SEED_DIR = Path(__file__).resolve().parent / "seed"


@dataclass(frozen=True, slots=True)
class Edition:
    name: str
    app_name: str
    company_code: str
    company_name: str
    rule_set: str
    adapters: tuple[str, ...]
    document_markers: tuple[str, ...]
    bare_number_prefix: str = "INV"
    # Ship-from address used for broker quotes until the user changes it.
    origin: dict[str, str] = field(default_factory=dict)
    # Starter catalog and packing settings applied on first start (never over existing data).
    seed_file: str | None = None


EDITIONS: dict[str, Edition] = {
    "highland": Edition(
        name="highland",
        app_name="Quote Helper",
        company_code="HCI",
        company_name="Highland Cabinetry",
        rule_set="highland",
        adapters=("highland_pdf", "text_layout", "table"),
        document_markers=("Highland Cabinetry",),
    ),
    "modernform": Edition(
        name="modernform",
        app_name="Modernform Quote Helper",
        company_code="MCC",
        company_name="ModernForm Cabinets & Closet",
        rule_set="modernform",
        # The QuickBooks invoice/packing-slip layout first; generic readers as fallbacks.
        adapters=("modernform_pdf", "highland_pdf", "text_layout", "table"),
        document_markers=("ModernForm Cabinets",),
        origin={
            "name": "ModernForm Cabinets & Closet Inc",
            "street": "1520 Luna Rd Suite 140",
            "city": "Carrollton",
            "state": "TX",
            "postal_code": "75006",
        },
        seed_file="modernform_seed.json",
    ),
}


def current() -> Edition:
    name = os.environ.get(EDITION_ENV) or str(RELEASE.get("edition") or "highland")
    try:
        return EDITIONS[name]
    except KeyError:
        raise RuntimeError(f"Unknown edition {name!r} (release.json 'edition' or {EDITION_ENV})") from None


def seed_path(edition: Edition | None = None) -> Path | None:
    edition = edition or current()
    return SEED_DIR / edition.seed_file if edition.seed_file else None
