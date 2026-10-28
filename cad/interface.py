# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Flathead shared geometry: classic Apple duckhead interface (body, stud slot, C7 boss) and AU pins, used by D1 and D2.

Frame: brick notch walls at x=0 (stud side) and y=0 (C8 inlet side); part slides -Y into the notch.
Wall face is z=t, flush with the brick's large face; AU pins exit +Z. Brick hangs down, so "up" is +Y.
Values tagged ASSUMED have no public source (teardown photos, design practice); the first print confirms them.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from build123d import (
    Align,
    Axis,
    Box,
    Circle,
    Compound,
    Plane,
    Pos,
    Rectangle,
    Rot,
    chamfer,
    extrude,
    fillet,
)

OUT = Path(__file__).parent / "out"
FONT = str(Path(__file__).parent / "fonts" / "Aileron-Regular.ttf")  # CC0 (Dot Colon), the subset Pillow ships
MIN3 = (Align.MIN, Align.MIN, Align.MIN)
C_C_MIN = (Align.CENTER, Align.CENTER, Align.MIN)
MIN_MIN_C = (Align.MIN, Align.MIN, Align.CENTER)
FACE_PROUD = 0.5  # REQ-DIM-007, cl 2.8.1
SLEEVE_MIN = 8.2  # REQ-DIM-004 lower limit, 8.7 - 0.5


@dataclass(frozen=True)
class Params:
    # Brick corner notch the part slides into: photo-scaled from ChargerLAB teardowns [U, +/-1.5]
    notch_w: float = 29.5  # x, normal to the stud wall
    notch_l: float = 28.3  # y, along the slide
    # Body: both designs fill the notch, so the brick and plug outline is unchanged
    w: float = 29.5  # x
    l: float = 28.3  # y  # noqa: E741
    t: float = 28.5  # z, brick thickness [V ChargerLAB 70W 28.5, 140W 28.51; 29/61/87W 28]
    corner_r: float = 6.0  # outer vertical corner, matches brick corner. ASSUMED
    edge_r: float = 3.0  # wall-face and back edges on the brick-outline sides, match the brick's edge round. ASSUMED
    notch_corner_r: float = 1.0  # convex corner into the brick notch, clears its inside radius. ASSUMED
    wall: float = 2.2  # moulded wall, DFM 2.0 to 2.5 for PC
    draft: float = 1.0  # deg, general (moulded shells)
    draft_mate: float = 0.5  # deg, notch-mating walls, stud slot, C7 boss: keeps the fit tight
    rib_wall: float = 1.2  # plastic round inserts in internal columns, 40 to 60% of wall
    tongue_w: float = 0.8  # shell joint: tongue on the bottom rim, groove in the top rim
    tongue_h: float = 0.8
    joint_clr: float = 0.1  # groove clearance each side of the tongue
    ed_h: float = 0.28  # ultrasonic energy director on the tongue: 90 deg apex, 0.25 to 0.75 [S]
    grip_depth: float = 0.3  # grooves across each pin tail face for insert retention
    grip_w: float = 1.0

    # Stud slot: T-slot in the x=0 face captures the brick's mushroom-head stud (~8.5 mm head); the closed end is the
    # stop. Centred on the thickness. Photo-scaled from ChargerLAB duckheads [U]; no separate detent found.
    slot_w: float = 5.5  # opening (z), passes the stud neck
    slot_len: float = 18.0  # closed end from the mating face (y)
    lip_t: float = 1.0  # x depth of the opening, ASSUMED
    head_w: float = 9.0  # T-channel width (z), ASSUMED from the ~8.5 head
    head_t: float = 1.8  # x depth of the T-channel, ASSUMED

    # C7 boss on y=0 face, mates the brick's (modified) C8 inlet. Figure-eight per IEC 60320 C7
    c7_x: float = 14.75  # centred on the notch width, ASSUMED
    c7_z: float = 14.25  # centred on the thickness
    c7_r: float = 3.3  # lobe radius, ASSUMED: IEC 60320-1 sheet paywalled; fits Schurter 4810 C7 end 19.6 x 12 [S]
    c7_pitch: float = 8.6  # lobe and contact pitch [V Schurter 2578 datasheet, 8.6 +/-0.1; Wikipedia IEC 60320]
    c7_waist: float = 5.0  # ASSUMED
    c7_depth: float = 10.0  # protrusion along -y; C8 inlet is 10 deep [S Wikipedia IEC 60320]
    c7_hole_d: float = 2.6
    c7_angle: float = 0.0  # deg, lobe axis from +x about y
    c8_pin_d: float = 2.0  # brick inlet pin, ASSUMED
    c7_contact_od: float = 3.2  # tubular spring contact, ASSUMED
    c7_front_wall: float = 2.0  # boss front wall ahead of the contacts
    c7_contact_back: float = 6.0  # contact rear end, y into the body
    link_w: float = 1.6  # brass link, 1/16 in C260 rod (K&S 8162, 1.57) [S]
    link_sleeve: float = 0.8  # PTFE tube 1.6 x 3.2 (Bohlender R 750-08) [S]; < 1 mm, so 60335-1 29.3 electric strength
    link_join: float = -2.5  # link joins the pin tail this far along the blade from its centre, away from the slot

    # AU pins, AS/NZS 3112 via EESS TRF #20-019 (2.8.1, 2.2.4); layout AS/NZS 3112 Fig 2.1 (D 7.92, radials 60 deg)
    pin_w: float = 6.35
    pin_th: float = 1.63
    pin_len: float = 17.06
    sleeve_len: float = 8.7
    pin_r: float = 7.92  # pin centre to plug midpoint
    pin_tilt: float = 30.0  # blade to vertical, deg
    pin_tip_chamfer: float = 0.4  # lead-in, approximates Fig 2.1(h)
    pin_tail: float = 6.0  # embedded length, insert moulded; short keeps live metal clear of the stud slot
    pin_cx: float = 11.3  # plug midpoint: 9 mm off the outer edge (REQ-DIM-006), as far from the stud slot as allowed
    pin_cy: float = 11.8  # notch_l - 16.5: brick top 16.5 above the pins clears the switch (D-02)


def pin_angles(p: Params) -> dict[str, float]:
    """Pin centre angles, deg CCW from +x viewed from pins; pins 120 deg apart, earth position would be 270.
    Tangential blades then sit at pin_tilt to vertical. Neutral then active clockwise (cl 2.12.6)."""
    return {"N": 90 + 2 * p.pin_tilt, "A": 90 - 2 * p.pin_tilt}


def pin_footprint(p: Params, angle: float) -> list[tuple[float, float]]:
    """Blade cross-section corners on the wall face; long side tangential (30 deg to vertical)."""
    a = math.radians(angle)
    cx, cy = p.pin_cx + p.pin_r * math.cos(a), p.pin_cy + p.pin_r * math.sin(a)
    tx, ty = -math.sin(a), math.cos(a)  # tangential
    rx, ry = math.cos(a), math.sin(a)  # radial
    hw, ht = p.pin_w / 2, p.pin_th / 2
    return [(cx + sx * hw * tx + sr * ht * rx, cy + sx * hw * ty + sr * ht * ry) for sx in (-1, 1) for sr in (-1, 1)]


def pin(p: Params, angle: float) -> tuple:
    """Returns (sleeve, contact) solids for one pin; sleeve includes the embedded tail."""
    a = math.radians(angle)
    place = Pos(p.pin_cx + p.pin_r * math.cos(a), p.pin_cy + p.pin_r * math.sin(a), p.t) * Rot(0, 0, angle + 90)
    sleeve = place * Pos(0, 0, -p.pin_tail) * Box(p.pin_w, p.pin_th, p.pin_tail + p.sleeve_len, align=C_C_MIN)
    contact = place * Pos(0, 0, p.sleeve_len) * Box(p.pin_w, p.pin_th, p.pin_len - p.sleeve_len, align=C_C_MIN)
    contact = chamfer(contact.edges().group_by(Axis.Z)[-1], p.pin_tip_chamfer)
    off = p.pin_th / 2
    grips = [Pos(0, sy * off, -z) * Box(p.pin_w + 1, 2 * p.grip_depth, p.grip_w) for z in (2.5, 5.5) for sy in (-1, 1)]
    return sleeve - [place * g for g in grips], contact  # plastic fills the grooves: pull-out and torque retention


def c7_profile(p: Params, y: float = 0.0):
    """Figure-eight outline less the contact entry holes, on a plane at y facing -y."""
    r = p.c7_r
    lobes = Pos(-p.c7_pitch / 2, 0) * Circle(r) + Pos(p.c7_pitch / 2, 0) * Circle(r)
    outline = lobes + Rectangle(p.c7_pitch, p.c7_waist)
    holes = Pos(-p.c7_pitch / 2, 0) * Circle(p.c7_hole_d / 2) + Pos(p.c7_pitch / 2, 0) * Circle(p.c7_hole_d / 2)
    face = Plane((p.c7_x, y, p.c7_z), x_dir=(1, 0, 0), z_dir=(0, -1, 0)).rotated((0, 0, p.c7_angle))
    return face * (outline - holes)


def c7_boss(p: Params):
    return extrude(c7_profile(p), p.c7_depth)  # plane normal is -y


def slot_depth(p: Params) -> float:
    return p.lip_t + p.head_t


def head_end(p: Params) -> float:
    """T-channel end (y): the stud seats at the slot's closed end, its head reaches head_w / 2 past that centre."""
    return p.slot_len - p.slot_w / 2 + p.head_w / 2


def body(p: Params):
    """Undrafted body (D2 and the clearance screen): outer, T-slot for the stud, C7 boss."""
    b = Box(p.w, p.l, p.t, align=MIN3)
    b = fillet(b.edges().filter_by(Axis.Z).group_by(Axis.X)[-1].group_by(Axis.Y)[-1], p.corner_r)
    outer = [e for e in b.edges() if e.center().X > 1 and e.center().Y > 1 and e.center().Z in (0, p.t)]
    b = fillet(outer, p.edge_r)  # face and back edges on the brick-outline sides; brick-mating edges stay sharp
    sw, hw = p.slot_w, p.head_w
    opening = Pos(-1, -1, p.c7_z) * Box(p.lip_t + 1, p.slot_len + 1, sw, align=MIN_MIN_C)
    channel = Pos(p.lip_t - 0.01, -1, p.c7_z) * Box(p.head_t + 0.01, head_end(p) + 1, hw, align=MIN_MIN_C)
    return b - opening - channel + c7_boss(p)


def edge_clearance(p: Params) -> float:
    """REQ-DIM-006: min distance live pin to the edge of the plug face (>= 9 mm). The face is the part plus the flush
    brick face, so only the part's outer sides (x = w, y = l) are moulding edges [U interpretation]."""
    pts = [pt for a in pin_angles(p).values() for pt in pin_footprint(p, a)]
    return min(min(p.w - x, p.l - y) for x, y in pts)


def socket_torque(mass_g: float, thick_mm: float, gap_mm: float = 3.0) -> float:
    """REQ-MEC-001: Nm on socket, brick flat on wall, CoM at half thickness plus face gap (<= 0.25)."""
    return mass_g / 1000 * 9.81 * (thick_mm / 2 + gap_mm) / 1000


def check_common(p: Params, part: Compound) -> None:
    """Checks shared by D1 and D2 (open position): solids, pin layout and length, C7 boss, DIM-006, DIM-007,
    ELE-006. MEC-001 lives in fit.check_fit with the brick data."""
    assert all(s.is_valid for s in part.children), "invalid solid"
    ang = pin_angles(p)
    assert set(ang) == {"N", "A"} and ang["N"] > ang["A"], "neutral left of active viewed from pins (cl 2.12.6)"
    for a in ang.values():
        cx = sum(x for x, _ in pin_footprint(p, a)) / 4
        cy = sum(y for _, y in pin_footprint(p, a)) / 4
        assert math.isclose(math.dist((cx, cy), (p.pin_cx, p.pin_cy)), p.pin_r), "pin radius"
    tip = max(c.bounding_box().max.Z for c in part.children if c.label == "pin")
    assert math.isclose(tip - p.t, p.pin_len), "pin length from face"
    boss = c7_boss(p).bounding_box()
    assert boss.min.Y < 0 <= boss.max.Y + 1e-6, "C7 boss protrudes -y"
    clr = edge_clearance(p)
    assert clr >= 9.0, f"REQ-DIM-006 edge clearance {clr:.2f} mm < 9"
    proud = max(c.bounding_box().max.Z for c in part.children if c.label not in ("pin", "sleeve")) - p.t
    assert proud <= FACE_PROUD + 1e-6, f"REQ-DIM-007 face feature {proud:.2f} mm proud of the plug face"
    check_partial_engagement(p, part)


def check_partial_engagement(p: Params, part: Compound) -> None:
    """REQ-ELE-006, insulated-pin plug (cl 2.2.4, Fig 2.4 basis): sleeve unbroken from inside the body to >= 8.2 mm
    out, bare metal only beyond it. With the plug withdrawn a gap g, bare metal is in the gap only if g > sleeve, and
    stays live only while the tip still reaches the socket contacts; so sockets with contacts deeper than
    pin_len - sleeve (8.36 mm nominal) never expose live metal. Socket depth is the socket standard's job."""
    sleeves = [c.bounding_box() for c in part.children if c.label == "sleeve"]
    bare = min(c.bounding_box().min.Z for c in part.children if c.label == "pin") - p.t
    assert len(sleeves) == 2 and all(p.t > b.min.Z for b in sleeves), "REQ-ELE-006 sleeve must start inside the body"
    assert all(b.max.Z - p.t >= bare - 1e-6 for b in sleeves), "REQ-ELE-006 gap between sleeve and bare pin"
    assert bare >= SLEEVE_MIN - 1e-6, f"REQ-ELE-006 bare pin {bare:.2f} mm from face < {SLEEVE_MIN}"
