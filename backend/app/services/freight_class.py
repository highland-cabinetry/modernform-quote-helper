from __future__ import annotations

# NMFTA 13-sub density scale, kept here as versioned application policy.
DENSITY_CLASS_BREAKS: tuple[tuple[float, str], ...] = (
    (1.0, "400"),
    (2.0, "300"),
    (4.0, "250"),
    (6.0, "175"),
    (8.0, "125"),
    (10.0, "100"),
    (12.0, "92.5"),
    (15.0, "85"),
    (22.5, "70"),
    (30.0, "65"),
    (35.0, "60"),
    (50.0, "55"),
)


def density_freight_class(density_pcf: float) -> str:
    """Return the density-derived class; callers must label it as an estimate."""

    for upper_bound, freight_class in DENSITY_CLASS_BREAKS:
        if density_pcf < upper_bound:
            return freight_class
    return "50"
