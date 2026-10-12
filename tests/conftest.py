from __future__ import annotations

import os

# The shared regression tests (from upstream Quote Helper) assume the upstream defaults: the
# Highland default company and an empty catalog. Modernform behaviour is tested in
# test_modernform.py, which switches the edition per test.
os.environ.setdefault("QUOTE_HELPER_EDITION", "highland")

import pytest  # noqa: E402
from app.models import CombinedItem, ItemMaster, PackingState, SourceReference  # noqa: E402

# Freight-domain fixtures (isolated repository + API client); see fixtures_freight.py.
from fixtures_freight import api, freight_repo  # noqa: F401, E402


@pytest.fixture
def combined_factory():
    def factory(
        *,
        quantity: int = 1,
        original_sku: str | None = None,
        packing_state: PackingState = PackingState.FLATPACK,
        **overrides,
    ) -> CombinedItem:
        defaults = dict(
            id=1,
            sku="TEST-BOX",
            description="Test cabinet carton",
            length=10,
            width=10,
            height=10,
            weight=25,
            category="Cabinetry",
            stackable=True,
            fragile=False,
            must_stay_upright=False,
            no_upside_down=True,
            allowed_rotations=["LWH", "WLH", "LHW", "HLW", "WHL", "HWL"],
            max_stack_layers=8,
            max_weight_on_top=500,
            load_bearing_strength=500,
            preferred_pallet_type="either",
        )
        defaults.update(overrides)
        master = ItemMaster(**defaults)
        authored = original_sku or master.sku
        return CombinedItem(
            key=str(master.id),
            master_item=master,
            packing_state=packing_state,
            quantity=quantity,
            sources=[SourceReference(source_ref="TEST-ORDER", original_sku=authored, quantity=quantity)],
            original_skus=[authored],
        )

    return factory


def pytest_configure(config):
    config.addinivalue_line("markers", "performance: timing check with a generous bound; runs by default")
