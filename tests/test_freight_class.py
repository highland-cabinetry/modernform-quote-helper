import pytest
from app.services.freight_class import density_freight_class


@pytest.mark.parametrize(
    ("density", "expected"),
    [
        (0.99, "400"), (1, "300"), (2, "250"), (4, "175"), (6, "125"),
        (8, "100"), (10, "92.5"), (12, "85"), (15, "70"), (22.5, "65"),
        (30, "60"), (35, "55"), (50, "50"),
    ],
)
def test_density_class_boundaries(density, expected):
    assert density_freight_class(density) == expected
