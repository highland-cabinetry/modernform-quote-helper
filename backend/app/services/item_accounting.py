"""Post-expansion accounting for items physically contained in another unit.

The packing engines intentionally know how to place cartons, while this small
step reconciles products that do not get an independent carton.  It is kept
duck typed so the service can run against either engine's unit dataclass or a
small test double without importing the engine and creating an import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..models import ContainedItem, PackingState, UnpackedItem
from .product_exceptions import TRASH_KIT_RULE, is_trash_kit_identity
from .sku_matching import normalize_sku

TRASH_KIT_NO_HOST_CODE = "TRASH_KIT_NO_ASSEMBLED_B18_HOST"
TRASH_KIT_CAPACITY_CODE = "TRASH_KIT_HOST_CAPACITY"
TRASH_KIT_MISSING_WEIGHT_CODE = "TRASH_KIT_MISSING_WEIGHT"
TRASH_KIT_SKU = TRASH_KIT_RULE.canonical_sku


def _state_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _is_assembled(unit: Any) -> bool:
    return _state_value(getattr(unit, "packing_state", "")) == PackingState.ASSEMBLED.value


def _is_b18(unit: Any) -> bool:
    """Recognize master and prefixed authored B18 SKUs without matching B180."""

    for value in (getattr(unit, "sku", ""),):
        raw = str(value or "").strip().upper()
        if re.search(r"(?:^|[\s._/\-])B18$", raw):
            return True
        if normalize_sku(raw) == "B18":
            return True
    return False


def _source_refs(unit: Any) -> list[str]:
    value = getattr(unit, "source_refs", [])
    if isinstance(value, str):
        return [value]
    try:
        return [str(ref) for ref in value if str(ref).strip()]
    except TypeError:
        return []


def _source_scope(value: str) -> str:
    """Collapse page/table suffixes to the order/document scope."""

    return re.split(r"\s+-\s+page\b|\bpage\s+\d+|\.pdf\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip().casefold()


def _unit_id(unit: Any) -> str:
    return str(getattr(unit, "unit_id", ""))


def _dimensions(unit: Any) -> tuple[float, float, float]:
    values = (
        getattr(unit, "length", 0),
        getattr(unit, "width", 0),
        getattr(unit, "height", 0),
    )
    try:
        return tuple(float(value or 0) for value in values)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0)


def _weight(unit: Any) -> float:
    try:
        return float(getattr(unit, "weight", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def _contained_items(host: Any) -> list[Any] | None:
    """Read or initialize a host's optional contained-items collection."""

    try:
        value = getattr(host, "contained_items", None)
    except Exception:  # pragma: no cover - defensive for unusual duck types
        return None
    if value is None:
        value = []
        try:
            host.contained_items = value
        except (AttributeError, TypeError):
            return None
    if not isinstance(value, list):
        try:
            value = list(value)
            host.contained_items = value
        except (TypeError, AttributeError):
            return None
    return value


def _existing_contained_unit_ids(host: Any) -> set[str]:
    items = _contained_items(host) or []
    return {str(getattr(item, "unit_id", "")) for item in items if getattr(item, "unit_id", None)}


def _unpacked_trash(unit: Any, code: str, reason: str) -> UnpackedItem:
    state = getattr(unit, "packing_state", PackingState.ASSEMBLED)
    # An assembled trash kit has no independent geometry: the B18 carton owns
    # its physical footprint.  Keep zero dimensions in the exception record so
    # reporting cannot accidentally count it as a second carton.
    dimensions = (0.0, 0.0, 0.0) if _is_assembled(unit) else _dimensions(unit)
    return UnpackedItem(
        unit_id=_unit_id(unit),
        sku=str(getattr(unit, "sku", "TRASH KIT") or "TRASH KIT"),
        original_sku=str(getattr(unit, "original_sku", "TRASH KIT") or "TRASH KIT"),
        packing_state=state,
        description=str(getattr(unit, "description", "Trash kit") or "Trash kit"),
        source_refs=_source_refs(unit),
        weight=max(0.0, _weight(unit)),
        dimensions=dimensions,
        reason_code=code,
        reason=reason,
    )


def _host_sort_key(index_by_id: dict[str, int], host: Any) -> tuple[int, str]:
    refs = _source_refs(host)
    return (min((index_by_id.get(ref, 10**9) for ref in refs), default=10**9), _unit_id(host))


def _same_source_scope(left: Any, right: Any) -> bool:
    left_scopes = {_source_scope(ref) for ref in _source_refs(left)}
    right_scopes = {_source_scope(ref) for ref in _source_refs(right)}
    return bool(left_scopes and right_scopes and left_scopes.intersection(right_scopes))


def _host_with_existing_trash(hosts: Iterable[Any], trash_id: str) -> Any | None:
    for host in hosts:
        items = _contained_items(host) or []
        if any(str(getattr(item, "unit_id", "")) == trash_id for item in items):
            return host
    return None


def _reconcile_host_weight(host: Any, items: list[Any], *, base_weight: float | None = None) -> None:
    """Set host weight from its immutable base plus contained item weights."""

    if base_weight is None:
        prior = next(
            (float(item.host_base_weight) for item in items if getattr(item, "host_base_weight", None)),
            None,
        )
        base_weight = prior if prior is not None else _weight(host)
    contained_weight = sum(max(0.0, float(getattr(item, "weight", 0) or 0)) for item in items)
    try:
        host.weight = float(base_weight) + contained_weight
    except (AttributeError, TypeError):
        return


def prepare_units(units: list[Any]) -> tuple[list[Any], list[UnpackedItem]]:
    """Attach assembled trash kits to eligible B18 units and return carton units.

    At most one kit is assigned to each assembled B18.  Hosts in the same
    source scope are preferred, followed by the remaining B18 hosts in stable
    source order and unit-id order.  Calling this helper repeatedly is safe:
    existing contained identities are recognized and host weight is rebuilt
    from the stored base weight instead of being incremented again.
    """

    indexed = list(enumerate(units))
    hosts = [
        unit
        for _, unit in indexed
        if _is_assembled(unit) and _is_b18(unit) and _contained_items(unit) is not None
    ]
    source_index: dict[str, int] = {}
    for index, unit in indexed:
        for ref in _source_refs(unit):
            source_index.setdefault(ref, index)
    hosts.sort(key=lambda unit: _host_sort_key(source_index, unit))

    trash_units = [
        unit
        for _, unit in indexed
        if _is_assembled(unit)
        and is_trash_kit_identity(
            sku=str(getattr(unit, "sku", "") or ""),
            original_sku=str(getattr(unit, "original_sku", "") or ""),
            description=str(getattr(unit, "description", "") or ""),
        )
    ]
    trash_units.sort(
        key=lambda unit: (
            source_index.get(_source_refs(unit)[0], 10**9) if _source_refs(unit) else 10**9,
            _unit_id(unit),
        )
    )

    trash_object_ids = {id(unit) for unit in trash_units}
    physical = [unit for unit in units if id(unit) not in trash_object_ids]
    unavailable: list[UnpackedItem] = []

    for trash in trash_units:
        trash_id = _unit_id(trash)
        if _weight(trash) <= 0:
            unavailable.append(
                _unpacked_trash(
                    trash,
                    TRASH_KIT_MISSING_WEIGHT_CODE,
                    "Trash kit weight is missing or not positive; supply a verified shipping weight.",
                )
            )
            continue
        already_attached = _host_with_existing_trash(hosts, trash_id)
        if already_attached is not None:
            # Repair a legacy result's host total from the saved base weight,
            # then keep the kit out of the physical carton list.
            existing_items = _contained_items(already_attached) or []
            _reconcile_host_weight(already_attached, existing_items)
            continue

        available = [host for host in hosts if not (_contained_items(host) or [])]
        scoped = [host for host in available if _same_source_scope(trash, host)]
        selected = (scoped or available)[0] if (scoped or available) else None
        if selected is None:
            has_any_host = bool(hosts)
            code = TRASH_KIT_CAPACITY_CODE if has_any_host else TRASH_KIT_NO_HOST_CODE
            reason = (
                "Every eligible assembled B18 already contains one trash kit; one kit per host is the packing limit."
                if has_any_host
                else "No eligible assembled B18 host was found for this contained trash kit."
            )
            unavailable.append(_unpacked_trash(trash, code, reason))
            continue

        items = _contained_items(selected)
        if items is None:  # pragma: no cover - hosts were filtered above
            unavailable.append(
                _unpacked_trash(
                    trash,
                    TRASH_KIT_NO_HOST_CODE,
                    "The assembled B18 host cannot record contained-item accounting.",
                )
            )
            continue
        base_weight = _weight(selected)
        if base_weight <= 0:
            unavailable.append(
                _unpacked_trash(
                    trash,
                    TRASH_KIT_NO_HOST_CODE,
                    "The assembled B18 host has no positive base weight for contained-item accounting.",
                )
            )
            continue
        contained = ContainedItem(
            unit_id=trash_id,
            host_unit_id=_unit_id(selected),
            sku=str(getattr(trash, "sku", TRASH_KIT_SKU) or TRASH_KIT_SKU),
            original_sku=str(getattr(trash, "original_sku", TRASH_KIT_SKU) or TRASH_KIT_SKU),
            source_refs=_source_refs(trash),
            packing_state=PackingState.ASSEMBLED,
            weight=_weight(trash),
            host_base_weight=base_weight,
        )
        items.append(contained)
        _reconcile_host_weight(selected, items, base_weight=base_weight)

    return physical, unavailable
