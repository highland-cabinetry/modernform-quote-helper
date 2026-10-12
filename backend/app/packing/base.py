from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import CombinedItem, ManualOverride, PackingResult, PackingSettings, RotationPreference


class PackingEngine(ABC):
    """Replaceable boundary for any 3D packing implementation."""

    name: str
    version: str

    @abstractmethod
    def pack(
        self,
        shipment_name: str,
        items: list[CombinedItem],
        settings: PackingSettings,
        *,
        overrides: list[ManualOverride] | None = None,
        rotation_preferences: list[RotationPreference] | None = None,
        split_bundle_ids: list[str] | None = None,
    ) -> PackingResult:
        """Produce a complete plan. Never silently discard an item."""


class PackingEngineUnavailable(RuntimeError):
    pass
