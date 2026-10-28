# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Brick envelope and AU GPO layouts; REQ-DIM-008 outline and REQ-FIT-002 GPO fit checks. Sets D-02 pin offset.

2D checks run in room view (looking at the wall from the room), origin at the plugged socket's pin midpoint, +y up.
Room x is the mirror of the part's x (the part frame is viewed from the pins). The brick's outer corner coincides with
the Flathead's outer corner (x=w, y=l), so the brick hangs down and extends to room-right of the pins.
"""

from dataclasses import dataclass

from build123d import Align, Box, Compound, Cylinder, Pos, Rot
from interface import MIN3, Params, socket_torque

USR_MARGIN = 5.0  # REQ-USR-001: wall protrusion <= brick thickness + 5 mm (target)
KEEP = 21.9  # compact plug half-width (REQ-DIM-008 lower band): room an adjacent plug needs about its socket
DIM008_BAND = (21.9, 27.0)  # outline projection must not fall strictly inside this band [U: datum is the pin midpoint]

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1


@dataclass(frozen=True)
class Brick:
    name: str
    bw: float  # room x extent
    bl: float  # room y extent
    bt: float
    mass_g: float


BRICKS = (  # classic duckhead family; outline orientation relative to the notch ASSUMED
    Brick("70W", 65.0, 65.0, 28.5, 165),  # [V] ChargerLAB A2743
    Brick("61W", 74.0, 74.0, 28.0, 209),  # thickness [V ChargerLAB A1719: 29/61/87W share 28]; outline, mass [U]
    Brick("96W", 80.0, 80.0, 28.6, 299),  # [U] forum figure; retail 317 to 454 g are boxed
    Brick("MagSafe2 85W", 78.0, 78.0, 28.0, 326),  # [U] retailers conflict (270 to 326 g)
    Brick("140W classic", 95.7, 74.4, 28.5, 285),  # [V] ChargerLAB A3607 body (newer pill connector excluded, D-01)
)


@dataclass(frozen=True)
class Gpo:
    name: str
    sockets: int
    pitch: float  # socket centre to centre, room x
    plate_w: float
    plate_h: float
    socket_y: float  # socket centre above plate centre
    switch: bool = True
    switch_dy: float = 26.0  # switch centre above its socket
    switch_w: float = 10.0
    switch_h: float = 12.0
    finger: float = 3.0  # clearance a finger needs round a switch


GPOS = (  # plate [S Clipsal 2025D 116 x 76; V 3025 120 x 78]; socket pitch, switch position ASSUMED (unpublished)
    Gpo("double 2025", 2, 54.0, 116.0, 76.0, -8.0),
    Gpo("double Iconic", 2, 56.0, 120.0, 78.0, -8.0),
    Gpo("board 4-way", 4, 45.0, 190.0, 50.0, 0.0, switch=False),  # pitch [S Jaycar MS4061 45]
)


def brick_rect(p: Params, b: Brick) -> Rect:
    """Brick outline: its outer corner is the notch's outer corner (notch_w, notch_l in the part frame)."""
    return (p.pin_cx - p.notch_w, p.notch_l - b.bl - p.pin_cy, p.pin_cx - p.notch_w + b.bw, p.notch_l - p.pin_cy)


def part_rect(p: Params) -> Rect:
    """The part's own outline; it matters if a variant overhangs the notch."""
    return (p.pin_cx - p.w, -p.pin_cy, p.pin_cx, p.l - p.pin_cy)


def gap(a: Rect, b: Rect) -> float:
    """Separation of two rectangles: > 0 clear by that much, <= 0 overlapping."""
    return max(b[0] - a[2], a[0] - b[2], b[1] - a[3], a[1] - b[3])


def projections(p: Params, b: Brick) -> dict[str, float]:
    """Outline of brick and part together, from the pin midpoint, room view."""
    (a0, b0, a1, b1), (c0, d0, c1, d1) = brick_rect(p, b), part_rect(p)
    return {"left": -min(a0, c0), "right": max(a1, c1), "up": max(b1, d1), "down": -min(b0, d0)}


def keepouts(g: Gpo, k: int) -> list[tuple[str, Rect]]:
    """Zones the brick must not cover when plugged into socket k: other sockets, every switch."""
    zones = []
    for j in range(g.sockets):
        x = (j - k) * g.pitch
        if j != k:
            zones.append((f"socket {j}", (x - KEEP, -KEEP, x + KEEP, KEEP)))
        if g.switch:
            hw, hh = g.switch_w / 2 + g.finger, g.switch_h / 2 + g.finger
            zones.append((f"switch {j}", (x - hw, g.switch_dy - hh, x + hw, g.switch_dy + hh)))
    return zones


def fit_margin(p: Params, b: Brick, g: Gpo, k: int) -> float:
    """REQ-FIT-002 worst clearance (mm) plugged into socket k; negative means something is covered."""
    rects = (brick_rect(p, b), part_rect(p))
    return min(gap(r, z) for _, z in keepouts(g, k) for r in rects)


def fit_table(p: Params) -> list[str]:
    rows = []
    for g in GPOS:
        for b in BRICKS:
            cells = [f"s{k} {fit_margin(p, b, g, k):+5.1f}" for k in range(g.sockets)]
            rows.append(f"{g.name:14} {b.name:13} " + "  ".join(cells))
    return rows


def check_fit(p: Params) -> None:
    worst = max(socket_torque(b.mass_g, b.bt) for b in BRICKS)
    assert worst <= 0.25, f"REQ-MEC-001 torque {worst:.3f} Nm"
    for b in BRICKS:
        bad = {d: v for d, v in projections(p, b).items() if DIM008_BAND[0] < v < DIM008_BAND[1]}
        assert not bad, f"REQ-DIM-008 {b.name}: {bad} inside {DIM008_BAND}"
    for g in GPOS:
        for b in BRICKS:
            best = max(fit_margin(p, b, g, k) for k in range(g.sockets))
            assert best > 0, f"REQ-FIT-002 {b.name} covers a socket or switch in every socket of {g.name}"


def protrusion(p: Params, part: Compound, b: Brick) -> float:
    """Wall to farthest point, mm: brick face flush with the plug face (z = t), so the deeper of brick and part."""
    face = max(p.t, *(c.bounding_box().max.Z for c in part.children if c.label not in ("pin", "sleeve")))
    deepest = min(c.bounding_box().min.Z for c in part.children)
    return max(face - deepest, b.bt + face - p.t)


def check_usr(p: Params, part: Compound) -> None:
    for b in BRICKS:
        d = protrusion(p, part, b)
        assert d <= b.bt + USR_MARGIN, f"REQ-USR-001 {b.name}: {d:.1f} mm from the wall > {b.bt} + {USR_MARGIN}"


def scene(p: Params, part: Compound, b: Brick, g: Gpo, k: int) -> Compound:
    """3D context: part plugged into socket k, brick envelope behind it, GPO plate on the wall (z > t).
    Takes ownership of part.children (build123d re-parents them); pass a fresh build."""
    brick = Pos(p.notch_w - b.bw, p.notch_l - b.bl, p.t - b.bt) * Box(b.bw, b.bl, b.bt, align=MIN3)
    brick -= Box(p.notch_w, p.notch_l, p.t, align=MIN3)  # notch the part sits in
    cx = ((g.sockets - 1) / 2 - k) * g.pitch  # plate centre, room x from the plugged socket
    plate = Pos(p.pin_cx - cx, p.pin_cy - g.socket_y, p.t) * Box(
        g.plate_w, g.plate_h, 8, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    for j in range(g.sockets):
        x = p.pin_cx - (j - k) * g.pitch
        plate -= Pos(x, p.pin_cy, p.t) * Cylinder(18, 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
        if g.switch:
            plate += (
                Pos(x, p.pin_cy + g.switch_dy, p.t)
                * Rot(0, 180, 0)
                * Box(g.switch_w, g.switch_h, 3, align=(Align.CENTER, Align.CENTER, Align.MIN))
            )
    brick.label, plate.label = "brick", "gpo"
    return Compound(label="scene", children=[*part.children, brick, plate])
