"""Shared deterministic ordering; only call after independent validation."""


def result_rank(result):
    return (
        len(result.pallets),
        round(sum(p.load_length * p.load_width * p.freight_height for p in result.pallets), 6),
        sum(len(p.required_actions) for p in result.pallets),
        -round(min((i.support_ratio for p in result.pallets for i in p.items), default=0), 6),
        round(
            sum(
                abs(p.center_of_gravity[0] - p.length / 2) + abs(p.center_of_gravity[1] - p.width / 2)
                for p in result.pallets
            ),
            6,
        ),
    )
