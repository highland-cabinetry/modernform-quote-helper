"""Stack planner: the production pallet engine.

Cabinet freight is not a general 3D bin-packing problem.  Every carton stays
upright with a fixed thickness, footprints fall into a few families, and the
warehouse builds pallets the same way every time:

* 80"+ items (panels, pantry/OC boxes) need a 96 x 40 deck.  They form a
  platform at the bottom or a cap on top of the cabinet stacks; a rigid load
  board levels an uneven layer when needed (at most two per pallet).
* Trim and moldings ride in a narrow strip along one side of a long deck.
* The largest cartons become pile bases spread across the deck, and smaller
  cartons stack on them pyramid-style.  Narrow cartons of equal thickness are
  paired side by side.  Fragile face-frame/door cartons go on top.
* Fewest pallets win, then the smallest deck (48 x 40 before 96 x 40), then even
  heights instead of a full pallet plus a short "runt".

The planner tries pallet counts and long/standard mixes in that order and, for
each, binary-searches one common height target so the pallets come out
balanced.  Every candidate is checked by the independent ``RulesValidator``;
every unit is either placed or reported as unpacked with a reason.
"""

from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil

from ..models import (
    CombinedItem,
    ItemKind,
    ManualOverride,
    OverrideOutcome,
    PackingResult,
    PackingRole,
    PackingSettings,
    PackingState,
    PalletSpec,
    PlacedItem,
    RotationPreference,
    Severity,
    UnpackedItem,
)
from ..services.product_exceptions import CSB42_RULE
from .base import PackingEngine
from .common import (
    CandidatePlan,
    PlanResultBuilder,
    Unit,
    WorkPallet,
    _assign_component_pairs,
    _expand_units,
    _is_csb42_assembled,
    _orientation,
    apply_edge_permission,
)
from .validator import EPSILON, RulesValidator, _compatible_ffd_stack, rectangle_union_area, support_contacts

TRIM_MAX_WIDTH = 8.0          # long items this narrow ride in the side trim strip
TRIM_STRIP_WIDTH = 8.0        # widest the side trim strip may grow
PAIR_MAX_WIDTH = 22.0         # cartons this narrow may be paired side by side
PAIR_THICKNESS_DELTA = 0.4    # paired cartons must form a level layer
NEW_PILE_HEIGHT_FACTOR = 2.0  # open a new pile once stacking would be this much taller
PLATFORM_FLAT_COVERAGE = 0.85 # platform top coverage that needs no load board
PILE_STRIP_MIN = 12.0         # open deck strip beside a platform wide enough for its own piles
TOP_BOARD_SUPPORT = 0.20      # share of a top board that must rest on the tallest stacks
TOP_BOARD_LEVEL = 2.0         # stacks within this of the tallest count as carrying a top board
BASE_LOOKAHEAD = 6            # upcoming cartons simulated when choosing a pile base
BUILD_STYLES = ("classic", "tiled")
WEIGHT_BOUND_SHARE = 0.9      # a long deck this close to its weight limit triggers the standard-first fill
TRUCK_EXTRA_PALLETS = 4       # extra pallet counts the truckload optimizer explores
SEED_SIMULATION_LAYERS = 24    # cartons simulated when scoring a base layout
SEED_TYPES = 6                # carton footprint types tried as the deck's pile bases
BALANCE_STEPS = 8             # binary-search steps for the common height target
RUNT_MAX_UNITS = 12            # a pallet this small may be spread over the others
RUNT_MAX_SHARE = 0.25         # ... when it carries at most this share of its weight limit
SEARCH_SECONDS = 22.0         # whole search budget (both build styles); the job runner stops at 30 s

LONG_MODES = {"auto": ("bottom", "top"), "bottom": ("bottom",), "top": ("top",)}


def _upright_rotations(unit: Unit, preferred: str | None) -> list[str]:
    rotations = [r for r in unit.allowed_rotations if r.endswith("H")] or ["LWH"]
    if preferred in rotations:
        rotations.remove(preferred)
        rotations.insert(0, preferred)
    return rotations


def _footprints(unit: Unit, preferred: str | None) -> list[tuple[str, float, float, float]]:
    seen, out = set(), []
    for rotation in _upright_rotations(unit, preferred):
        length, width, height = _orientation(unit, rotation)
        key = (round(length, 4), round(width, 4))
        if key not in seen:
            seen.add(key)
            out.append((rotation, length, width, height))
    return out


def _edge_footprints(unit: Unit) -> list[tuple[str, float, float, float]]:
    """Standing-on-edge orientations (thin side down), when the carton allows them."""
    if not unit.allow_edge_standing:
        return []
    seen, out = set(), []
    for rotation in unit.allowed_rotations:
        if rotation.endswith("H"):
            continue
        length, width, height = _orientation(unit, rotation)
        key = (round(length, 4), round(width, 4), round(height, 4))
        if key not in seen:
            seen.add(key)
            out.append((rotation, length, width, height))
    return out


def _lengthwise(unit: Unit) -> tuple[str, float, float, float]:
    options = _footprints(unit, None)
    return next((f for f in options if f[1] >= f[2]), options[0])


def _short_side(unit: Unit) -> float:
    return min(unit.length, unit.width)


@dataclass
class _Pool:
    """Units still waiting for a pallet, by how they are built."""

    normals: list[Unit]
    platform: list[list[Unit]]      # non-fragile long groups (bottom mode platform)
    caps: list[list[Unit]]          # long groups that always go on top
    trims: list[Unit]
    csb42: list[Unit]
    pair_of: dict[str, str]         # FFD unit_id -> BOX unit_id

    def copy(self) -> _Pool:
        return _Pool(list(self.normals), [list(g) for g in self.platform], [list(g) for g in self.caps],
                     list(self.trims), list(self.csb42), self.pair_of)

    def empty(self) -> bool:
        return not (self.normals or self.platform or self.caps or self.trims or self.csb42)


@dataclass
class _Pile:
    rect: tuple[float, float, float, float]
    top: tuple[float, float, float, float]
    members: list[PlacedItem] = field(default_factory=list)


class _FreeRects:
    """Maximal-rectangles free space for pile bases on one deck."""

    def __init__(self, x0: float, y0: float, x1: float, y1: float) -> None:
        self.rects = [(x0, y0, x1 - x0, y1 - y0)]

    def candidates(self, length: float, width: float, deck_l: float, deck_y0: float, deck_y1: float):
        """Yield (overhang, fit, x, y), pushing the carton toward the deck interior."""
        for rx, ry, rl, rw in self.rects:
            if length > rl + EPSILON or width > rw + EPSILON:
                continue
            ideal_x = (deck_l - length) / 2 if length > deck_l else 0.0
            ideal_y = deck_y0 + (deck_y1 - deck_y0 - width) / 2 if width > deck_y1 - deck_y0 else deck_y0
            x = min(max(rx, ideal_x), rx + rl - length)
            y = min(max(ry, ideal_y), ry + rw - width)
            overhang = (max(0.0, -x) + max(0.0, x + length - deck_l)
                        + max(0.0, deck_y0 - y) + max(0.0, y + width - deck_y1))
            yield round(overhang, 3), min(rl - length, rw - width), x, y

    def overlap(self, x: float, y: float, length: float, width: float) -> float:
        """Largest overlap area with any free rectangle (space still open for bases)."""
        best = 0.0
        for rx, ry, rl, rw in self.rects:
            dx = min(x + length, rx + rl) - max(x, rx)
            dy = min(y + width, ry + rw) - max(y, ry)
            if dx > 0 and dy > 0:
                best = max(best, dx * dy)
        return best

    def occupy(self, x: float, y: float, length: float, width: float) -> None:
        new = []
        for rx, ry, rl, rw in self.rects:
            if (x >= rx + rl - EPSILON or x + length <= rx + EPSILON
                    or y >= ry + rw - EPSILON or y + width <= ry + EPSILON):
                new.append((rx, ry, rl, rw))
                continue
            if x > rx + EPSILON:
                new.append((rx, ry, x - rx, rw))
            if x + length < rx + rl - EPSILON:
                new.append((x + length, ry, rx + rl - x - length, rw))
            if y > ry + EPSILON:
                new.append((rx, ry, rl, y - ry))
            if y + width < ry + rw - EPSILON:
                new.append((rx, y + width, rl, ry + rw - y - width))
        new = list(dict.fromkeys(r for r in new if r[2] > 0.5 and r[3] > 0.5))
        self.rects = [
            a for a in new
            if not any(
                b is not a and a[0] >= b[0] - EPSILON and a[1] >= b[1] - EPSILON
                and a[0] + a[2] <= b[0] + b[2] + EPSILON and a[1] + a[3] <= b[1] + b[3] + EPSILON
                for b in new
            )
        ]


class _PalletBuild:
    """Greedy, rule-checked construction of one pallet."""

    def __init__(self, engine: StackPlannerPackingEngine, spec: PalletSpec, target: float, mode: str) -> None:
        self.engine = engine
        self.settings = engine.settings
        self.spec = spec
        self.target = target
        self.mode = mode
        self.ov = self.settings.general_overhang_per_side
        self.tol = self.settings.support_height_tolerance
        self.min_support = self.settings.minimum_support_ratio
        self.placed: list[PlacedItem] = []
        self.boards: list[PlacedItem] = []
        self.weight = 0.0
        self.weight_cap = spec.max_total_weight - spec.tare_weight
        self.has_assembled = False
        self.piles: list[_Pile] = []
        self.region_y0 = -self.ov
        self.failed_pair = False
        # Set when face-frame/door cartons with a reviewed low top-load limit form the top of the
        # platform: nothing (no load board, no cartons) may be stacked on this pallet any more.
        self.sealed = False

    @property
    def solids(self) -> list[PlacedItem]:
        return [*self.placed, *self.boards]

    # ----------------------------------------------------------------- limits
    def cap_for(self, unit: Unit | None) -> float:
        assembled = self.has_assembled or (unit is not None and unit.packing_state == PackingState.ASSEMBLED)
        return min(self.target, self.engine.cargo_cap(assembled, self.spec))

    def top(self) -> float:
        return max((s.z + s.height for s in self.solids), default=0.0)

    def drop_z(self, x: float, y: float, length: float, width: float) -> float:
        z = 0.0
        for solid in self.solids:
            if (x < solid.x + solid.length - EPSILON and solid.x < x + length - EPSILON
                    and y < solid.y + solid.width - EPSILON and solid.y < y + width - EPSILON):
                z = max(z, solid.z + solid.height)
        return z

    def make(self, unit: Unit, rotation: str, x: float, y: float, z: float) -> PlacedItem:
        return self.engine._make_placement(unit, (x, y, z), _orientation(unit, rotation), rotation)

    def supported(self, item: PlacedItem) -> bool:
        if item.z <= EPSILON:
            return True
        ratio, contacts = support_contacts(item, self.solids, self.tol)
        if ratio + EPSILON < self.min_support:
            return False
        by_id = {s.unit_id: s for s in self.solids}
        for contact in contacts:
            below = by_id[contact.supporting_unit_id]
            if not below.stackable:
                return False
            if below.fragile and item.item_key != "load-board" and not _compatible_ffd_stack(below, item) and (
                item.weight > self.settings.fragile_heavy_threshold or item.fragile is False
            ):
                return False
            if below.sku.upper().endswith("FFD") and below.pair_group_id and item.sku.upper().endswith("BOX"):
                return False
        return True

    def fits(self, unit: Unit, item: PlacedItem) -> bool:
        return (item.z + item.height <= self.cap_for(unit) + EPSILON
                and self.weight + unit.weight <= self.weight_cap + EPSILON
                and item.x >= -self.ov - EPSILON and item.y >= -self.ov - EPSILON
                and item.x + item.length <= self.spec.length + self.ov + EPSILON
                and item.y + item.width <= self.spec.width + self.ov + EPSILON)

    def commit(self, unit: Unit, item: PlacedItem) -> None:
        self.placed.append(item)
        self.weight += unit.weight
        if unit.packing_state == PackingState.ASSEMBLED:
            self.has_assembled = True

    def uncommit(self, item: PlacedItem) -> None:
        self.placed.remove(item)
        self.weight -= self.engine.units_by_id[item.unit_id].weight
        self.has_assembled = any(i.packing_state == PackingState.ASSEMBLED for i in self.placed)

    def add_board(self, z: float) -> PlacedItem | None:
        if len(self.boards) >= self.settings.max_load_boards_per_pallet:
            return None
        thickness = self.settings.load_board_thickness
        if z + thickness > self.cap_for(None) + EPSILON:
            return None
        if self.weight + self.settings.load_board_weight > self.weight_cap + EPSILON:
            return None
        board = PlacedItem(
            unit_id=f"load-board-{len(self.boards) + 1}", item_key="load-board", sku="LOAD BOARD",
            original_sku="Load board", description="Rigid load board (warehouse dunnage)", category="Dunnage",
            item_kind=ItemKind.OTHER, source_refs=["Warehouse dunnage"], x=0.0, y=0.0, z=z,
            length=self.spec.length, width=self.spec.width, height=thickness, rotation="LWH",
            weight=self.settings.load_board_weight, fragile=False, stackable=True, must_stay_upright=True,
            allowed_rotations=["LWH"], max_stack_layers=None, max_weight_on_top=1e9, load_bearing_strength=1e9,
            color="#C8A97E", packing_role=PackingRole.STANDARD,
        )
        self.boards.append(board)
        self.weight += board.weight
        return board

    # ------------------------------------------------------------ long items
    def place_long(self, unit: Unit) -> PlacedItem | None:
        rotation, length, width, height = _lengthwise(unit)
        x = (self.spec.length - length) / 2
        base_y = max(0.0, self.region_y0)
        ys = [base_y, self.spec.width - width, base_y + (self.spec.width - base_y - width) / 2]
        for solid in self.solids:
            if solid.length > 48:
                ys += [solid.y + (solid.width - width) / 2, solid.y, solid.y + solid.width - width]
        floor = 0.0
        box_id = self.engine.pair_box.get(unit.unit_id)
        if box_id is not None:
            box = next((i for i in self.placed if i.unit_id == box_id), None)
            if box is None:
                return None
            floor = box.z + box.height
        best = None
        for y in dict.fromkeys(round(v, 4) for v in ys):
            if y < self.region_y0 - EPSILON:
                continue
            z = self.drop_z(x, y, length, width)
            if z + EPSILON < floor:
                continue
            item = self.make(unit, rotation, x, y, z)
            if not self.fits(unit, item) or not self.supported(item):
                continue
            overhang = max(0.0, -y) + max(0.0, y + width - self.spec.width)
            score = (round(z + height, 3), overhang, abs(y + width / 2 - self.spec.width / 2))
            if best is None or score < best[0]:
                best = (score, item)
        if best is None:
            return None
        self.commit(unit, best[1])
        return best[1]

    def place_group(self, group: list[Unit]) -> bool:
        done = []
        for unit in group:
            item = self.place_long(unit)
            if item is None:
                for placed in reversed(done):
                    self.uncommit(placed)
                return False
            done.append(item)
        return True

    # ------------------------------------------------------------ PC/OC pairs
    @staticmethod
    def is_pair_group(group: list[Unit]) -> bool:
        return (len(group) >= 2 and bool(group[0].pair_group_id) and group[0].sku.upper().endswith("BOX")
                and all(u.pair_group_id == group[0].pair_group_id for u in group))

    @staticmethod
    def _stack_key(group: list[Unit]) -> tuple:
        ffd = group[1]
        return (ffd.paired_family, ffd.original_sku.split("-", 1)[0] if "-" in ffd.original_sku else "",
                ffd.source_refs[0].split(" - ")[0] if ffd.source_refs else "", ffd.packing_state)

    @staticmethod
    def _top_limit(unit: Unit) -> float:
        """Weight a reviewed carton may carry; unreviewed defaults only raise a warning."""
        return min(unit.max_weight_on_top, unit.load_bearing_strength) if unit.rules_reviewed else float("inf")

    def place_pair_batch(self, groups: list[list[Unit]]) -> list[list[Unit]]:
        """Stack several PC/OC pairs on one long deck: all BOX cartons first, their FFD cartons on top.

        Placing pair by pair stops after one pair, because a BOX may never rest on an FFD. A
        warehouse stacks the boxes, then the face-frame/door cartons of the same family on top of
        them (FFD on FFD is allowed). Every FFD stays on the same pallet as its BOX. The number of
        pairs is limited by the deck's weight and height and by the reviewed top-load limit of the
        FFD cartons, which carry the FFDs above them. Returns the groups not placed.
        """
        if not groups:
            return []
        key = self._stack_key(groups[0])
        family = [g for g in groups if self._stack_key(g) == key]
        others = [g for g in groups if self._stack_key(g) != key]
        count = len(family)
        while count >= 1:
            chosen = family[:count]
            ffds = [u for g in chosen for u in g[1:]]
            # FFD i carries every FFD placed after it in the column.
            if all(sum(f.weight for f in ffds[index + 1:]) <= self._top_limit(ffd) + EPSILON
                   for index, ffd in enumerate(ffds)):
                done: list[PlacedItem] = []
                ok = True
                for unit in [g[0] for g in chosen] + ffds:
                    item = self.place_long(unit)
                    if item is None:
                        ok = False
                        break
                    done.append(item)
                if ok:
                    if count > 1 or len(family) == 1:
                        self._seal_if_fragile_top(ffds)
                    return family[count:] + others
                for item in reversed(done):
                    self.uncommit(item)
            count -= 1
        return family + others

    def _seal_if_fragile_top(self, ffds: list[Unit]) -> None:
        carried = 0.0
        spare = float("inf")
        for unit in reversed(ffds):
            spare = min(spare, self._top_limit(unit) - carried)
            carried += unit.weight
        if spare < self.settings.load_board_weight + self.settings.fragile_heavy_threshold:
            self.sealed = True

    def place_trims(self, trims: list[Unit]) -> list[Unit]:
        """Stack trim/molding in lanes along the y=0 edge of a long deck."""
        lanes: list[list[float]] = []   # [y0, width, top]
        left = []
        for unit in sorted(trims, key=lambda u: (-_short_side(u), -u.height, u.unit_id)):
            rotation, length, width, height = _lengthwise(unit)
            x = (self.spec.length - length) / 2
            placed = False
            for lane in sorted(lanes, key=lambda lane: lane[2]):
                if width > lane[1] + EPSILON:
                    continue
                item = self.make(unit, rotation, x, lane[0] + (lane[1] - width) / 2,
                                 self.drop_z(x, lane[0] + (lane[1] - width) / 2, length, width))
                if self.fits(unit, item) and self.supported(item):
                    self.commit(unit, item)
                    lane[2] = item.z + item.height
                    placed = True
                    break
            if not placed:
                y0 = lanes[-1][0] + lanes[-1][1] if lanes else 0.0
                if y0 + width <= TRIM_STRIP_WIDTH + EPSILON:
                    item = self.make(unit, rotation, x, y0, 0.0)
                    if self.fits(unit, item) and self.drop_z(x, y0, length, width) <= EPSILON:
                        self.commit(unit, item)
                        lanes.append([y0, width, height])
                        placed = True
            if not placed:
                left.append(unit)
        if lanes:
            self.region_y0 = lanes[-1][0] + lanes[-1][1]
        return left

    def platform_needs_board(self) -> bool:
        longs = [i for i in self.placed if i.length > 48]
        if not longs:
            return False
        top = max(i.z + i.height for i in longs)
        flat = [i for i in self.placed if top - (i.z + i.height) <= self.tol + EPSILON and i.z + i.height > EPSILON]
        area = rectangle_union_area([(i.x, i.y, i.x + i.length, i.y + i.width) for i in flat])
        # Compare with the deck the piles would have to use: everything except open
        # strips beside the platform that are wide enough to hold a pile on the deck.
        y0, y1 = max(0.0, self.region_y0), self.spec.width
        x0b = min(i.x for i in longs)
        x1b = max(i.x + i.length for i in longs)
        y0b = min(i.y for i in longs)
        y1b = max(i.y + i.width for i in longs)
        strips = [x0b, self.spec.length - x1b, y0b - y0, y1 - y1b]
        spans = [y1 - y0, y1 - y0, x1b - x0b, x1b - x0b]
        usable = sum(depth * span for depth, span in zip(strips, spans, strict=True) if depth >= PILE_STRIP_MIN)
        reference = max(self.spec.length * (y1 - y0) - usable, 1.0)
        return area < PLATFORM_FLAT_COVERAGE * reference or any(i.fragile for i in flat)


    def top_surfaces(self) -> list[PlacedItem]:
        """Solids with nothing resting on them."""
        solids = self.solids
        return [s for s in solids if not any(
            o is not s and o.z >= s.z + s.height - EPSILON
            and o.x < s.x + s.length - EPSILON and s.x < o.x + o.length - EPSILON
            and o.y < s.y + s.width - EPSILON and s.y < o.y + o.width - EPSILON
            for o in solids
        )]

    def top_board_ok(self) -> bool:
        """A top board may rest on the tallest stacks if they reach both ends."""
        z = self.top()
        level = [s for s in self.top_surfaces() if z - (s.z + s.height) <= TOP_BOARD_LEVEL + EPSILON]
        area = rectangle_union_area([(s.x, s.y, s.x + s.length, s.y + s.width) for s in level])
        half = self.spec.length / 2
        reaches = ({"low" for s in level if s.x < half - 6} | {"high" for s in level if s.x + s.length > half + 6})
        return area >= TOP_BOARD_SUPPORT * self.spec.length * self.spec.width and len(reaches) == 2

    # ------------------------------------------------------------------ piles
    def lookahead(self, free: _FreeRects, x, y, length, width, upcoming, deck_y0) -> float:
        """Deck area covered if this base is used and the next large cartons follow."""
        trial = _FreeRects(0, 0, 0, 0)
        trial.rects = list(free.rects)
        trial.occupy(x, y, length, width)
        covered = length * width
        for unit in upcoming[:BASE_LOOKAHEAD]:
            best = None
            for _, ul, uw, _ in _footprints(unit, None):
                for overhang, fit, ux, uy in trial.candidates(ul, uw, self.spec.length, deck_y0, self.spec.width):
                    key = (overhang, fit)
                    if best is None or key < best[0]:
                        best = (key, ux, uy, ul, uw)
            if best is not None:
                _, ux, uy, ul, uw = best
                trial.occupy(ux, uy, ul, uw)
                covered += ul * uw
        return covered

    def seed_layers(self, layers: list[list[Unit]], free: _FreeRects) -> list[list[Unit]]:
        """Lay the first course of pile bases as an even grid of the best-tiling carton type.

        Largest-first can leave a strip nothing else fits (one 31" pile on a 48" deck);
        two 25" piles side by side, centred with a little overhang, halve the height.
        Each candidate type is laid as a centred grid, the rest of the deck is filled
        greedily in simulation, and the layout covering the most deck wins.  The
        winning grid is placed immediately; the remaining layers are returned.
        """
        if not layers or len(free.rects) != 1:
            return layers
        rx, ry, rl, rw = free.rects[0]
        deck_y0 = max(0.0, self.region_y0)
        basic = [layer for layer in layers if not any(u.fragile for u in layer)
                 and not all(self.engine.is_topper(u) for u in layer)]

        def footprint(layer):
            _, length, width, _, _ = self.engine.layer_variants(layer)[0]
            return (round(min(length, width), 2), round(max(length, width), 2))

        def greedy(trial: _FreeRects, order) -> float:
            covered = 0.0
            for layer in order[:SEED_SIMULATION_LAYERS]:
                best = None
                for _, length, width, _, _ in self.engine.layer_variants(layer):
                    for overhang, fit, x, y in trial.candidates(length, width, self.spec.length, deck_y0,
                                                                self.spec.width):
                        key = (overhang, fit)
                        if best is None or key < best[0]:
                            best = (key, x, y, length, width)
                if best is not None:
                    _, x, y, length, width = best
                    trial.occupy(x, y, length, width)
                    covered += length * width
            return covered

        def fresh() -> _FreeRects:
            trial = _FreeRects(0, 0, 0, 0)
            trial.rects = list(free.rects)
            return trial

        best = (greedy(fresh(), basic), None)
        types = []
        for layer in basic:
            key = footprint(layer)
            if key not in types:
                types.append(key)
        span_y = (rw + ry) - max(ry, deck_y0 - self.ov) if self.region_y0 > 0 else rw
        for key in types[:SEED_TYPES]:
            group = [layer for layer in basic if footprint(layer) == key]
            for length, width in {(key[0], key[1]), (key[1], key[0])}:
                nx = int((rl + EPSILON) // length)
                ny = int((span_y + EPSILON) // width)
                count = min(nx * ny, len(group))
                if count < 2:
                    continue
                # Fill whole rows across the width first.
                cols = min(nx, ceil(count / ny))
                rows = min(ny, ceil(count / cols))
                x0 = (self.spec.length - cols * length) / 2
                y_lo = deck_y0 if self.region_y0 > 0 else 0.0
                y0 = y_lo + (self.spec.width - y_lo - rows * width) / 2
                y0 = max(y0, ry)
                cells = [(x0 + i * length, y0 + j * width) for i in range(cols) for j in range(rows)][:count]
                trial = fresh()
                for x, y in cells:
                    trial.occupy(x, y, length, width)
                rest = [layer for layer in basic if not any(layer is g for g in group[:count])]
                covered = count * length * width + greedy(trial, rest)
                if covered > best[0] + 1.0:
                    best = (covered, (group[:count], cells, length, width))
        if best[1] is None:
            return layers
        group, cells, length, width = best[1]
        placed_ids = set()
        for layer, (x, y) in zip(group, cells, strict=True):
            if self.place_base_at(layer, x, y, length, width, free):
                placed_ids.add(id(layer))
        return [layer for layer in layers if id(layer) not in placed_ids]

    def place_base_at(self, units: list[Unit], x: float, y: float, length: float, width: float,
                      free: _FreeRects) -> bool:
        variant = next((v for v in self.engine.layer_variants(units)
                        if abs(v[1] - length) < 0.01 and abs(v[2] - width) < 0.01), None)
        if variant is None:
            return False
        _, length, width, parts, axis = variant
        z = self.drop_z(x, y, length, width)
        items, offset = [], 0.0
        for unit, rotation, part_x, part_y, _height in parts:
            if axis == "y":
                px, py = x + (length - part_x) / 2, y + offset
                offset += part_y
            else:
                px, py = x + offset, y + (width - part_y) / 2
                offset += part_x
            items.append((unit, self.make(unit, rotation, px, py, z)))
        if not all(self.fits(u, i) for u, i in items) or not all(self.supported(i) for _, i in items):
            return False
        if self.weight + sum(u.weight for u, _ in items) > self.weight_cap + EPSILON:
            return False
        for unit, item in items:
            self.commit(unit, item)
        free.occupy(x, y, length, width)
        rect = (x, y, x + length, y + width)
        self.piles.append(_Pile(rect=rect, top=rect, members=[i for _, i in items]))
        return True

    def place_layer(self, units: list[Unit], free: _FreeRects, upcoming: list[Unit] | None = None,
                    allow_spill: bool = True) -> bool:
        upcoming = upcoming or []
        if self.weight + sum(u.weight for u in units) > self.weight_cap + EPSILON:
            return False
        variants = self.engine.layer_variants(units)
        deck_y0 = max(0.0, self.region_y0)
        options = []
        for _pile_index, pile in enumerate(self.piles):
            if any(u.max_stack_layers is not None and sum(m.item_key == u.item_key for m in pile.members)
                   + 1 > u.max_stack_layers for u in units):
                continue
            tx0, ty0, tx1, ty1 = pile.top
            for pref_rank, length, width, parts, axis in variants:
                estimate = min(length, tx1 - tx0) * min(width, ty1 - ty0) / (length * width)
                if estimate + EPSILON < self.min_support:
                    continue
                # Centre on the pile top, but keep inside the pile's own base where it
                # fits so stacked cartons do not hang over deck space still free.
                px0, py0, px1, py1 = pile.rect
                x = (tx0 + tx1 - length) / 2
                x = min(max(x, px0), px1 - length) if length <= px1 - px0 else (px0 + px1 - length) / 2
                y = (ty0 + ty1 - width) / 2
                y = min(max(y, py0), py1 - width) if width <= py1 - py0 else (py0 + py1 - width) / 2
                x = min(max(x, -self.ov), self.spec.length + self.ov - length)
                y = min(max(y, self.region_y0), self.spec.width + self.ov - width)
                z = self.drop_z(x, y, length, width)
                spill = free.overlap(x, y, length, width) + sum(
                    max(0.0, min(x + length, o.rect[2]) - max(x, o.rect[0]))
                    * max(0.0, min(y + width, o.rect[3]) - max(y, o.rect[1]))
                    for o in self.piles if o is not pile
                )
                if not allow_spill and spill > 1.0:
                    continue
                options.append(((pref_rank, spill > 1.0, round(z + max(p[4] for p in parts), 3), -estimate),
                                pile, x, y, z, length, width, parts, axis))
        options.sort(key=lambda o: o[0])
        base = None
        for pref_rank, length, width, parts, axis in variants:
            best_here = None
            for overhang, fit, x, y in free.candidates(length, width, self.spec.length, deck_y0, self.spec.width):
                key = (overhang, fit, y, x)
                if best_here is None or key < best_here[0]:
                    best_here = (key, x, y)
            if best_here is None:
                continue
            (overhang, fit, _, _), x, y = best_here
            covered = self.lookahead(free, x, y, length, width, upcoming, deck_y0)
            key = (pref_rank, -round(covered, 1), overhang, fit, y, x)
            if base is None or key < base[0]:
                base = (key, x, y, length, width, parts, axis)
        edge = None
        if base is None and len(units) == 1 and self.engine.style == "tiled":
            unit = units[0]
            for rotation, length, width, height in _edge_footprints(unit):
                for overhang, fit, x, y in free.candidates(length, width, self.spec.length, deck_y0,
                                                           self.spec.width):
                    z = self.drop_z(x, y, length, width)
                    key = (round(z + height, 3), overhang, fit, y, x)
                    if edge is None or key < edge[0]:
                        edge = (key, ("base", None, x, y, z, length, width,
                                      [(unit, rotation, length, width, height)], "y"))
        tries = []
        if edge is not None:
            # Stand a flatpack carton on edge in a strip too narrow for a flat pile
            # whenever that keeps the load lower than stacking it on a pile.
            if not options or edge[0][0] < options[0][0][2] - EPSILON:
                tries.append(edge[1])
            tries += [("pile", *o[1:]) for o in options]
            if tries[0] is not edge[1]:
                tries.append(edge[1])
        elif base is not None:
            _, x, y, length, width, parts, axis = base
            z = self.drop_z(x, y, length, width)
            layer_h = max(p[4] for p in parts)
            entry = ("base", None, x, y, z, length, width, parts, axis)
            # Cover the deck first, then build up: a new pile wins unless it would
            # start well above the existing stacks (e.g. a gap on a platform edge).
            toppers = all(self.engine.is_topper(u) for u in units)
            if not options or (not toppers and (z + layer_h)
                               <= options[0][0][2] + NEW_PILE_HEIGHT_FACTOR * layer_h + EPSILON):
                tries.append(entry)
            tries += [("pile", *o[1:]) for o in options]
            if tries[0][0] != "base":
                tries.append(entry)
        else:
            tries = [("pile", *o[1:]) for o in options]
        for kind, pile, x, y, z, length, width, parts, axis in tries:
            items, offset = [], 0.0
            for unit, rotation, part_x, part_y, _height in parts:
                if axis == "y":
                    px, py = x + (length - part_x) / 2, y + offset
                    offset += part_y
                else:
                    px, py = x + offset, y + (width - part_y) / 2
                    offset += part_x
                items.append((unit, self.make(unit, rotation, px, py, z)))
            if not all(self.fits(u, i) for u, i in items) or not all(self.supported(i) for _, i in items):
                continue
            if sum(u.weight for u, _ in items) + self.weight > self.weight_cap + EPSILON:
                continue
            for unit, item in items:
                self.commit(unit, item)
            rect = (x, y, x + length, y + width)
            free.occupy(x, y, length, width)
            if kind == "base":
                pile = _Pile(rect=rect, top=rect)
                self.piles.append(pile)
            pile.top = rect
            pile.members.extend(i for _, i in items)
            for other in self.piles:
                # A carton bridging level neighbouring piles becomes their new top too.
                if other is not pile and (min(rect[2], other.rect[2]) - max(rect[0], other.rect[0]) > 1.0
                                          and min(rect[3], other.rect[3]) - max(rect[1], other.rect[1]) > 1.0):
                    other.top = rect
            return True
        return False

    # ------------------------------------------------------------------ build
    def build(self, pool: _Pool) -> None:
        """Take what fits from ``pool`` (mutated in place)."""
        is_long = self.spec.name == "long"
        if pool.csb42 and not is_long and self.engine.csb42_deck(self.spec):
            unit = pool.csb42[0]
            rotation, length, width, height = _footprints(unit, None)[0]
            x = (self.spec.length - CSB42_RULE.assembled_length) / 2
            y = (self.spec.width - CSB42_RULE.assembled_width) / 2
            item = self.make(unit, rotation, x, y, 0.0)
            if height <= self.cap_for(unit) + EPSILON and self.weight + unit.weight <= self.weight_cap + EPSILON:
                self.commit(unit, item)
                rect = (x, y, x + length, y + width)
                self.piles.append(_Pile(rect=rect, top=rect, members=[item]))
                pool.csb42.pop(0)
        if is_long and pool.trims:
            pool.trims[:] = self.place_trims(pool.trims)
        if is_long and self.mode in ("bottom", "stacked") and pool.platform:
            remaining = []
            # "stacked": the PC/OC pairs' BOX cartons stacked together with their FFDs on top
            # (several pairs per deck); "bottom": one pair per deck under a load board.
            stacked = self.mode == "stacked"
            pairs = [group for group in pool.platform if self.is_pair_group(group)] if stacked else []
            for group in pool.platform:
                if stacked and self.is_pair_group(group):
                    if pairs:
                        remaining.extend(self.place_pair_batch(pairs))
                        pairs = []
                    continue
                if not self.place_group(group):
                    remaining.append(group)
            order = {id(group): index for index, group in enumerate(pool.platform)}
            pool.platform[:] = sorted(remaining, key=lambda group: order[id(group)])
            if self.sealed:
                return
            if self.platform_needs_board():
                if self.add_board(self.top()) is not None:
                    self.region_y0 = -self.ov
        free = _FreeRects(-self.ov, self.region_y0, self.spec.length + self.ov, self.spec.width + self.ov)
        for pile in self.piles:
            x0, y0, x1, y1 = pile.rect
            free.occupy(x0, y0, x1 - x0, y1 - y0)
        remaining_normals: list[Unit] = []
        layers = self.engine.layers_for(pool.normals)
        if self.engine.style == "classic":
            for index, layer in enumerate(layers):
                upcoming = [u for later in layers[index + 1:index + 1 + BASE_LOOKAHEAD] for u in later[:1]]
                if not self.place_layer(layer, free, upcoming):
                    remaining_normals.extend(layer)
            pool.normals[:] = remaining_normals
            if is_long:
                self.place_caps(pool)
            return
        layers = self.seed_layers(layers, free)
        # First pass keeps every carton inside its own pile; cartons that only fit by
        # hanging over a neighbouring pile wait until the piles are built, then bridge them.
        deferred: list[list[Unit]] = []
        for index, layer in enumerate(layers):
            upcoming = [u for later in layers[index + 1:index + 1 + BASE_LOOKAHEAD] for u in later[:1]]
            if not self.place_layer(layer, free, upcoming, allow_spill=False):
                deferred.append(layer)
        for layer in deferred:
            if not self.place_layer(layer, free, [], allow_spill=True):
                remaining_normals.extend(layer)
        pool.normals[:] = remaining_normals
        if is_long:
            self.place_caps(pool)

    def place_caps(self, pool: _Pool) -> None:
        """Long items on top: non-fragile first (top mode), then fragile FFDs."""
        # In bottom mode, long items the platform could not take may still ride on top.
        first = list(pool.platform)
        placed: set[int] = set()
        for stage in (first, pool.caps):
            on_pallet = {i.unit_id for i in self.placed}
            own = [g for g in stage
                   if all(pool.pair_of[u.unit_id] in on_pallet for u in g if u.unit_id in pool.pair_of)]
            for group in sorted(own, key=lambda g: (-_short_side(g[0]), g[0].unit_id)):
                if self.place_group(group):
                    placed.add(id(group))
                    continue
                if self.top_board_ok() and self.add_board(self.top()) is not None:
                    if self.place_group(group):
                        placed.add(id(group))
                        continue
                    self.weight -= self.boards.pop().weight
        pool.platform[:] = [g for g in pool.platform if id(g) not in placed]
        pool.caps[:] = [g for g in pool.caps if id(g) not in placed]
        on_pallet = {i.unit_id for i in self.placed}
        self.failed_pair = any(pool.pair_of.get(u.unit_id) in on_pallet for g in pool.caps for u in g)


class StackPlannerPackingEngine(PlanResultBuilder, PackingEngine):
    name = "stack-planner"
    version = "3.3.0"

    def __init__(self) -> None:
        self.validator = RulesValidator()
        self.style = "tiled"
        # "standard": fewest pallets, then smallest deck (LTL).
        # "truckload": least floor length on a dedicated truck, pallets two across.
        self.objective = "standard"
        self._deadline = float("inf")

    # ----------------------------------------------------------- utilities
    def pref(self, unit: Unit) -> str | None:
        return self._rotation_preferences.get(unit.unit_id)

    def cargo_cap(self, assembled: bool, spec: PalletSpec) -> float:
        total = self.settings.assembled_max_total_height if assembled else self.settings.flatpack_max_total_height
        return max(0.0, total - spec.deck_height)

    @staticmethod
    def csb42_deck(spec: PalletSpec) -> bool:
        return (abs(spec.length - CSB42_RULE.standard_deck[0]) <= EPSILON
                and abs(spec.width - CSB42_RULE.standard_deck[1]) <= EPSILON)

    def fits_deck(self, unit: Unit, spec: PalletSpec) -> bool:
        ov = self.settings.general_overhang_per_side
        if _is_csb42_assembled(unit):
            ov = max(ov, CSB42_RULE.overhang_per_side)
        return any(length <= spec.length + 2 * ov + EPSILON and width <= spec.width + 2 * ov + EPSILON
                   for _, length, width, _ in _footprints(unit, None))

    def is_long(self, unit: Unit) -> bool:
        return unit.preferred_pallet_type == "long" or not self.fits_deck(unit, self.settings.standard_pallet)

    def cannot_fit_reason(self, unit: Unit) -> tuple[str, str] | None:
        specs = [self.settings.standard_pallet, self.settings.long_pallet]
        if all(unit.weight + spec.tare_weight > spec.max_total_weight for spec in specs):
            return "OVERWEIGHT_ITEM", "Item plus pallet tare exceeds every configured pallet weight limit."
        if not any(self.fits_deck(unit, spec) for spec in specs):
            return (
                "OVERSIZE_FOOTPRINT",
                f"Item footprint does not fit a {self.settings.standard_pallet.length:g} × "
                f"{self.settings.standard_pallet.width:g} or {self.settings.long_pallet.length:g} × "
                f"{self.settings.long_pallet.width:g} in pallet with up to "
                f"{self.settings.general_overhang_per_side:g} in overhang per side.",
            )
        assembled = unit.packing_state == PackingState.ASSEMBLED
        lowest = min(_orientation(unit, r)[2] for r in _upright_rotations(unit, None))
        if all(lowest > self.cargo_cap(assembled, spec) + EPSILON for spec in specs):
            return "OVERHEIGHT_ITEM", "Item is taller than the configured total freight height."
        return None

    @staticmethod
    def is_topper(unit: Unit) -> bool:
        """Accessories and small loose items ride on top of the cabinet stacks."""
        return unit.item_kind in {ItemKind.ACCESSORY, ItemKind.OTHER}

    def layer_variants(self, units: list[Unit]):
        """Footprints for one pile layer: (pref_rank, length, width, parts, axis).

        ``parts`` are (unit, rotation, x_size, y_size, height).  A pair lies with
        its long sides parallel, side by side across y (axis "y") or along x.
        """
        if len(units) == 1:
            unit = units[0]
            pref = self.pref(unit)
            return [(rank if pref else 0, length, width, [(unit, rotation, length, width, height)], "y")
                    for rank, (rotation, length, width, height) in enumerate(_footprints(unit, pref))]
        a, b = units
        variants = []
        for rotation_a, la, wa, ha in _footprints(a, None):
            for rotation_b, lb, wb, hb in _footprints(b, None):
                parts = [(a, rotation_a, la, wa, ha), (b, rotation_b, lb, wb, hb)]
                if la >= wa and lb >= wb:
                    variants.append((0, max(la, lb), wa + wb, parts, "y"))
                elif la <= wa and lb <= wb:
                    variants.append((0, la + lb, max(wa, wb), parts, "x"))
        return variants

    def layers_for(self, normals: list[Unit]) -> list[list[Unit]]:
        """Largest footprints first, fragile cartons last, narrow equal-thickness pairs."""
        ordered = sorted(normals, key=lambda u: (u.fragile, self.is_topper(u), -u.length * u.width, -u.height,
                                                 u.unit_id))
        layers: list[list[Unit]] = []
        used: set[str] = set()
        for index, a in enumerate(ordered):
            if a.unit_id in used:
                continue
            used.add(a.unit_id)
            mate = None
            if _short_side(a) <= PAIR_MAX_WIDTH and not self.pref(a) and a.stackable and not a.fragile:
                for b in ordered[index + 1:]:
                    if b.unit_id in used or b.fragile or not b.stackable or self.pref(b):
                        continue
                    la, lb = max(a.length, a.width), max(b.length, b.width)
                    if (_short_side(b) <= PAIR_MAX_WIDTH and abs(a.height - b.height) <= PAIR_THICKNESS_DELTA
                            and _short_side(a) + _short_side(b) <= 44.0 and min(la, lb) >= 0.7 * max(la, lb)):
                        mate = b
                        break
            if mate is not None:
                used.add(mate.unit_id)
                layers.append([a, mate])
            else:
                layers.append([a])

        def key(layer):
            area = (layer[0].length * layer[0].width if len(layer) == 1 else
                    max(max(u.length, u.width) for u in layer) * sum(_short_side(u) for u in layer))
            return (any(u.fragile for u in layer), all(self.is_topper(u) for u in layer),
                    -area, -max(u.height for u in layer), layer[0].unit_id)

        return sorted(layers, key=key)

    # ------------------------------------------------------------- search
    def _pool(self, units: list[Unit]) -> _Pool:
        normals, trims, csb42, platform, caps = [], [], [], [], []
        pair_of: dict[str, str] = {}
        boxes: dict[str, Unit] = {}
        for unit in units:
            if unit.pair_group_id and unit.sku.upper().endswith("BOX"):
                boxes[unit.pair_group_id] = unit
        for unit in units:
            if _is_csb42_assembled(unit):
                csb42.append(unit)
            elif self.is_long(unit) or (unit.pair_group_id and unit.pair_group_id in boxes
                                        and self.is_long(boxes[unit.pair_group_id])):
                if unit.pair_group_id and unit.pair_group_id in boxes:
                    if not unit.sku.upper().endswith("BOX"):
                        pair_of[unit.unit_id] = boxes[unit.pair_group_id].unit_id
                    continue
                if _short_side(unit) <= TRIM_MAX_WIDTH and not unit.fragile:
                    trims.append(unit)
                else:
                    # Fragile long cartons (door/face-frame boxes) may sit in the platform:
                    # the load board laid over the platform spreads the load above them.
                    platform.append([unit])
            else:
                normals.append(unit)
        for group_id, box in boxes.items():
            ffds = [u for u in units if u.pair_group_id == group_id and u is not box]
            if self.is_long(box) or any(self.is_long(f) for f in ffds):
                platform.append([box, *ffds])
        platform.sort(key=lambda g: (-_short_side(g[0]), -g[0].height, g[0].unit_id))
        return _Pool(normals, platform, caps, trims, csb42, pair_of)

    def _try_mix(self, pool: _Pool, mix: list[PalletSpec], target: float, mode: str):
        pool = pool.copy()
        builds = []
        for spec in mix:
            build = _PalletBuild(self, spec, target, mode)
            build.build(pool)
            if not build.placed or build.failed_pair:
                return None
            builds.append(build)
        return builds if pool.empty() else None

    def _greedy(self, pool: _Pool, mode: str) -> tuple[list[_PalletBuild], _Pool]:
        """Sequential fill at full height: always terminates; the upper bound."""
        pool = pool.copy()
        builds = []
        while not pool.empty() and len(builds) < self.settings.max_pallets:
            needs_long = bool(pool.platform or pool.caps or pool.trims)
            spec = self.settings.long_pallet if needs_long else self.settings.standard_pallet
            trial = pool.copy()
            build = _PalletBuild(self, spec, 1e9, mode)
            build.build(trial)
            if not needs_long and not trial.empty():
                long_trial = pool.copy()
                long_build = _PalletBuild(self, self.settings.long_pallet, 1e9, mode)
                long_build.build(long_trial)
                if len(long_build.placed) > len(build.placed):
                    build, trial = long_build, long_trial
            if not build.placed:
                break
            builds.append(build)
            pool = trial
        return builds, pool

    def _mixes(self, n: int, need_long: bool) -> list[list[PalletSpec]]:
        std, long = self.settings.standard_pallet, self.settings.long_pallet
        mixes = [[long] * longs + [std] * (n - longs) for longs in range(0, n + 1) if not (need_long and longs == 0)]
        if self.objective == "truckload":
            return sorted(mixes, key=lambda mix: self.floor_inches([spec.name for spec in mix]))
        return sorted(mixes, key=lambda mix: sum(spec.length * spec.width for spec in mix))

    def _balanced(self, pool: _Pool, mix, mode: str, cap: float, low: float):
        """Fill the mix at the lowest common height target that still holds the order.

        Pallets fill in sequence, so a mixed long/standard mix is also tried with the
        standard decks filled first: when the long deck reaches its weight limit, filling
        it first can leave the 48 x 40 with an awkward remainder that only piles up tall.
        """
        orders = [list(mix)]
        if len({spec.name for spec in mix}) > 1:
            orders.append(sorted(mix, key=lambda spec: spec.name != "standard"))
        found = []
        for index, order in enumerate(orders):
            if index and found and not any(self._weight_bound(b) for b in found[0]):
                break   # no long deck ran into its weight limit; the usual order is fine
            best = self._try_mix(pool, order, cap, mode)
            if best is None:
                continue
            lo, hi = low, cap
            for _ in range(BALANCE_STEPS):
                if hi - lo < 0.5:
                    break
                mid = (lo + hi) / 2
                attempt = self._try_mix(pool, order, mid, mode)
                if attempt is None:
                    lo = mid
                else:
                    hi, best = mid, attempt
            found.append(best)
        if len(found) > 1:
            found.sort(key=lambda builds: (self._errors(builds) > 0, self._rank(builds)))
        return found[0] if found else None

    @staticmethod
    def _weight_bound(build: _PalletBuild) -> bool:
        if build.spec.name != "long":
            return False
        return build.weight >= WEIGHT_BOUND_SHARE * build.weight_cap

    def _work_pallets(self, builds: list[_PalletBuild]) -> list[WorkPallet]:
        pallets = []
        builds = sorted(builds, key=lambda build: build.spec.name != "long")   # long decks listed first
        for number, build in enumerate(builds, start=1):
            assembled = any(i.packing_state == PackingState.ASSEMBLED for i in build.placed)
            cap = self.cargo_cap(assembled, build.spec) + build.spec.deck_height
            spec = build.spec.model_copy(update={"max_total_height": cap})
            pallets.append(WorkPallet(spec=spec, number=number, items=list(build.placed),
                                      load_boards=list(build.boards)))
        return pallets

    def _errors(self, builds: list[_PalletBuild]) -> int:
        plans = self._pallet_plans(deepcopy(self._work_pallets(builds)), self.settings)
        return sum(issue.severity == Severity.ERROR for issue in self.validator.validate(plans, self.settings))

    @staticmethod
    def floor_inches(names: list[str]) -> float:
        """Truck floor length with pallets loaded two across and never stacked.

        96 x 40 pallets run lengthwise in two 40 in lanes (96 in of floor per pair). An odd
        96 x 40 leaves a 96 in lane beside it that holds two 48 x 40 pallets lengthwise.
        Other 48 x 40 pallets go in pairs turned sideways (48 + 48 in across, 40 in of floor).
        """
        shorts = sum(name == "standard" for name in names)
        longs = len(names) - shorts
        beside = 2 if longs % 2 else 0
        return ceil(longs / 2) * 96.0 + ceil(max(0, shorts - beside) / 2) * 40.0

    def _rank(self, builds: list[_PalletBuild]) -> tuple:
        heights = [max((s.z + s.height for s in [*b.placed, *b.boards]), default=0.0) for b in builds]
        rest = (sum(len(b.boards) for b in builds), round(max(heights), 1), round(sum(heights), 1))
        if self.objective == "truckload":
            return (self.floor_inches([b.spec.name for b in builds]), len(builds), *rest)
        return (len(builds), sum(b.spec.length * b.spec.width for b in builds), *rest)

    def _search(self, units: list[Unit]) -> tuple[list[WorkPallet], list[Unit]]:
        if not units:
            return [], []
        pool = self._pool(units)
        need_long = bool(pool.platform or pool.caps or pool.trims)
        modes = LONG_MODES.get(self.settings.long_items_position, LONG_MODES["auto"]) if need_long else ("bottom",)
        if "bottom" in modes and sum(_PalletBuild.is_pair_group(group) for group in pool.platform) >= 2:
            modes = (*modes, "stacked")   # several pantry/OC pairs: also try stacking them on one deck
        any_assembled = any(u.packing_state == PackingState.ASSEMBLED for u in units)
        long_spec, std_spec = self.settings.long_pallet, self.settings.standard_pallet
        cap = self.cargo_cap(any_assembled, long_spec)
        per_pallet = max(long_spec.max_total_weight - long_spec.tare_weight,
                         std_spec.max_total_weight - std_spec.tare_weight, 1.0)
        volume = sum(u.length * u.width * u.height for u in units)
        order_weight = sum(u.weight for u in units)
        start = max(1, ceil(order_weight / per_pallet),
                    ceil(volume / (long_spec.length * long_spec.width * max(cap, 1.0))))

        fallback = None
        for mode in modes:
            builds, left = self._greedy(pool, mode)
            if builds:
                candidate = (left.empty() is False, self._errors(builds) > 0, self._rank(builds), builds, left)
                if fallback is None or candidate[:3] < fallback[:3]:
                    fallback = candidate
        upper = len(fallback[3]) if fallback and fallback[4].empty() else self.settings.max_pallets
        errored = None
        best_clean = None
        truckload = self.objective == "truckload"
        if truckload:
            upper = min(upper + TRUCK_EXTRA_PALLETS, self.settings.max_pallets)
            if fallback is not None and fallback[4].empty() and not fallback[1]:
                best_clean = (fallback[2], fallback[3])   # sequential fill: the floor to beat
        for n in range(start, upper + 1):
            if time.monotonic() > self._deadline:
                break
            if truckload and best_clean is not None and ceil(n / 2) * 40.0 > best_clean[0][0]:
                break   # more pallets can no longer shorten the truck floor
            for mix in self._mixes(n, need_long):
                if time.monotonic() > self._deadline:
                    break
                if sum(spec.max_total_weight - spec.tare_weight for spec in mix) < order_weight - EPSILON:
                    continue   # the decks cannot carry the order's weight
                if truckload and best_clean is not None and (
                        self.floor_inches([spec.name for spec in mix]) >= best_clean[0][0]):
                    continue
                deck = sum(spec.length * spec.width for spec in mix)
                low = volume / deck if deck else 0.0
                if low > cap + EPSILON:
                    continue   # the decks cannot hold the order's volume under the height cap
                results = []
                for mode in modes:
                    builds = self._balanced(pool, mix, mode, cap, low)
                    if builds is not None:
                        results.append((self._errors(builds) > 0, self._rank(builds), builds))
                if not results:
                    continue
                results.sort(key=lambda r: (r[0], r[1]))
                if not results[0][0]:
                    if not truckload:
                        return self._work_pallets(results[0][2]), []
                    if best_clean is None or results[0][1] < best_clean[0]:
                        best_clean = (results[0][1], results[0][2])
                    continue
                if errored is None or results[0][1] < errored[1]:
                    errored = results[0]
        if best_clean is not None:
            return self._work_pallets(best_clean[1]), []
        if fallback is not None and fallback[4].empty() and not fallback[1]:
            return self._work_pallets(fallback[3]), []
        if errored is not None:
            return self._work_pallets(errored[2]), []
        if fallback is not None:
            left = fallback[4]
            leftover = left.normals + [u for g in left.platform + left.caps for u in g] + left.trims + left.csb42
            return self._work_pallets(fallback[3]), leftover
        return [], list(units)

    # -------------------------------------------------------------- locks
    def _apply_locks(self, pallets: list[WorkPallet], overrides: list[ManualOverride]) -> list[OverrideOutcome]:
        outcomes = []
        for override in overrides:
            unit = self.units_by_id.get(override.unit_id)
            source = next((p for p in pallets if any(i.unit_id == override.unit_id for i in p.items)), None)
            index = override.pallet_number - 1
            reason = ""
            if unit is None or source is None:
                reason = "The carton is not in the rebuilt plan, so its lock was not applied."
            elif index >= len(pallets):
                reason = f"Pallet {override.pallet_number} does not exist in the rebuilt plan."
            elif override.pallet_type and pallets[index].spec.name != override.pallet_type:
                reason = f"Pallet {override.pallet_number} is no longer a {override.pallet_type} pallet."
            elif override.rotation not in unit.allowed_rotations:
                reason = "Requested rotation is not allowed for this carton."
            elif not override.rotation.endswith("H") and (unit.must_stay_upright or not unit.allow_edge_standing):
                reason = "Edge-standing orientation is not approved for this carton."
            if not reason:
                target = pallets[index]
                affected = [source] if source is target else [source, target]
                before = self._error_count(affected)
                original = next(i for i in source.items if i.unit_id == unit.unit_id)
                source.items.remove(original)
                locked = self._make_placement(unit, (override.x, override.y, override.z),
                                              _orientation(unit, override.rotation), override.rotation,
                                              locked=True, manually_placed=override.manually_placed)
                target.items.append(locked)
                if self._error_count(affected) > before:
                    target.items.remove(locked)
                    source.items.append(original)
                    reason = "The locked position collides, lacks support, or exceeds pallet limits."
            if reason:
                outcomes.append(OverrideOutcome(unit_id=override.unit_id, kind="exact_lock", status="fallback",
                                                requested_rotation=override.rotation,
                                                message=f"{reason} The carton stayed automatically packed."))
            else:
                outcomes.append(OverrideOutcome(unit_id=override.unit_id, kind="exact_lock", status="accepted",
                                                requested_rotation=override.rotation,
                                                applied_rotation=override.rotation,
                                                message="Exact position lock applied."))
        return outcomes

    # ------------------------------------------------------- runt pallet
    def _top_up(self, pallet: WorkPallet, unit: Unit) -> PlacedItem | None:
        """The lowest supported spot for ``unit`` on top of what ``pallet`` already carries."""
        spec = pallet.spec
        ov = self.settings.general_overhang_per_side
        cap = spec.max_total_height - spec.deck_height
        weight_cap = spec.max_total_weight - spec.tare_weight
        if sum(i.weight for i in [*pallet.items, *pallet.load_boards]) + unit.weight > weight_cap + EPSILON:
            return None
        solids = [*pallet.items, *pallet.load_boards]
        best = None
        for rotation, length, width, height in _footprints(unit, None):
            spots = {(-ov, -ov), (spec.length + ov - length, -ov), (-ov, spec.width + ov - width),
                     (spec.length + ov - length, spec.width + ov - width)}
            for solid in solids:
                spots |= {(solid.x, solid.y), (solid.x + solid.length - length, solid.y),
                          (solid.x, solid.y + solid.width - width),
                          (solid.x + solid.length - length, solid.y + solid.width - width)}
            for x, y in spots:
                if (x < -ov - EPSILON or y < -ov - EPSILON or x + length > spec.length + ov + EPSILON
                        or y + width > spec.width + ov + EPSILON):
                    continue
                z = max((o.z + o.height for o in solids
                         if x < o.x + o.length - EPSILON and o.x < x + length - EPSILON
                         and y < o.y + o.width - EPSILON and o.y < y + width - EPSILON), default=0.0)
                if z + height > cap + EPSILON or (best is not None and z + height >= best[0]):
                    continue
                item = self._make_placement(unit, (x, y, z), (length, width, height), rotation)
                if z > EPSILON:
                    ratio, contacts = support_contacts(item, solids, self.settings.support_height_tolerance)
                    if ratio + EPSILON < self.settings.minimum_support_ratio:
                        continue
                    below = {o.unit_id: o for o in solids}
                    if any(not below[c.supporting_unit_id].stackable
                           or (below[c.supporting_unit_id].fragile
                               and below[c.supporting_unit_id].item_key != "load-board"
                               and (unit.weight > self.settings.fragile_heavy_threshold or not unit.fragile))
                           for c in contacts):
                        continue
                best = (z + height, item)
        return best[1] if best else None

    def _absorb_runt(self, pallets: list[WorkPallet]) -> list[WorkPallet]:
        """Move the cartons of a nearly empty last pallet onto the others when they fit safely.

        The sequential fill can leave a few small cartons (trash kits, sample doors) on a pallet of
        their own. Each carton is set on the lowest supported spot of another pallet; the move is
        kept only if every carton finds a spot and the validator finds no new error.
        """
        if len(pallets) < 2:
            return pallets
        runt = min(pallets, key=lambda pallet: (pallet.item_weight, len(pallet.items)))
        if len(runt.items) > RUNT_MAX_UNITS or runt.item_weight > RUNT_MAX_SHARE * (
                runt.spec.max_total_weight - runt.spec.tare_weight):
            return pallets
        trial = deepcopy([pallet for pallet in pallets if pallet is not runt])
        for moved in sorted(runt.items, key=lambda item: -(item.length * item.width * item.height)):
            unit = self.units_by_id.get(moved.unit_id)
            if unit is None or unit.pair_group_id:
                return pallets
            spot = None
            for pallet in sorted(trial, key=lambda p: max((i.z + i.height for i in [*p.items, *p.load_boards]),
                                                          default=0.0)):
                spot = self._top_up(pallet, unit)
                if spot is not None:
                    pallet.items.append(spot)
                    break
            if spot is None:
                return pallets
        if self._error_count(trial) > self._error_count(pallets):
            return pallets
        return trial

    def _error_count(self, pallets: list[WorkPallet]) -> int:
        plans = self._pallet_plans(deepcopy(pallets), self.settings)
        return sum(issue.severity == Severity.ERROR for issue in self.validator.validate(plans, self.settings))

    # --------------------------------------------------------------- pack
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
        self.settings = settings
        units, unavailable = _expand_units(items, set(split_bundle_ids or []))
        apply_edge_permission(units, settings)
        self.units_by_id = {unit.unit_id: unit for unit in units}
        self._expected_unit_ids = (
            [unit.unit_id for unit in units]
            + [item.unit_id for unit in units for item in unit.contained_items]
            + [item.unit_id for item in unavailable]
        )
        self._pairing_warnings = _assign_component_pairs(units)
        boxes = {u.pair_group_id: u.unit_id for u in units if u.pair_group_id and u.sku.upper().endswith("BOX")}
        self.pair_box = {u.unit_id: boxes[u.pair_group_id] for u in units
                         if u.pair_group_id in boxes and not u.sku.upper().endswith("BOX")}
        override_list = list(overrides or [])
        preferences = {value.unit_id: value.rotation for value in rotation_preferences or []}
        for override in override_list:
            preferences.setdefault(override.unit_id, override.rotation)
        self._rotation_preferences = preferences

        blocked: list[UnpackedItem] = []
        packable: list[Unit] = []
        for unit in units:
            reason = self.cannot_fit_reason(unit)
            if reason:
                blocked.append(self._unpacked(unit, *reason))
            else:
                packable.append(unit)
        blocked_ids = {b.unit_id for b in blocked}
        held_pairs = {u.pair_group_id for u in units if u.pair_group_id and u.unit_id in blocked_ids}
        for unit in [u for u in packable if u.pair_group_id in held_pairs]:
            packable.remove(unit)
            blocked.append(self._unpacked(unit, "PAIRED_COMPONENT_BLOCKED",
                                          "Its matching PC/OC component cannot be packed, so the pair is held."))

        # Two build styles: largest-first piles ("classic") and a deck tiled with the
        # best-fitting carton type first ("tiled"). Neither wins every order, so both are
        # planned and the better plan is kept.
        best = None
        search_started = time.monotonic()
        for index, style in enumerate(BUILD_STYLES, start=1):
            self.style = style
            self._deadline = search_started + SEARCH_SECONDS * index / len(BUILD_STYLES)
            pallets, leftover = self._search(packable)
            if pallets and not leftover and not override_list:
                pallets = self._absorb_runt(pallets)
            heights = [max((i.z + i.height for i in [*p.items, *p.load_boards]), default=0.0) for p in pallets]
            size = (len(pallets), sum(p.spec.length * p.spec.width for p in pallets))
            if self.objective == "truckload":
                size = (self.floor_inches([p.spec.name for p in pallets]), len(pallets))
            key = (len(leftover), self._error_count(pallets) > 0 if pallets else True, *size,
                   round(max(heights, default=0.0), 1), round(sum(heights), 1))
            if best is None or key < best[0]:
                best = (key, pallets, leftover)
        _, pallets, leftover = best
        for unit in leftover:
            blocked.append(self._unpacked(unit, "NO_SAFE_POSITION",
                                          "No supported position was found within the pallet limits."))
        outcomes = self._apply_locks(pallets, override_list) if pallets else [
            OverrideOutcome(unit_id=o.unit_id, kind="exact_lock", status="fallback",
                            requested_rotation=o.rotation, message="No plan was available to apply the lock.")
            for o in override_list
        ]
        plan = CandidatePlan(label=f"{self.name} {self.version}", pallets=pallets,
                             unpacked=blocked + deepcopy(unavailable), override_outcomes=outcomes)
        plan.score = float(len(pallets))
        return self._to_result(shipment_name, units, plan, [plan], settings)
