# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Flathead D2: folding AU pins inside the D1 envelope. Usage: uv run python cad/d2.py

Same notch-filling body, drafted shells, joint and link routing as D1, so plugged in the two look the same. Each pin
root sits in a carrier with trunnions along the blade width; the pin folds flat inward, toward the plug midpoint. The
folded pins cross: the under pin folds first into a deeper channel, the top pin folds over it, flush with the face.
Hinge depth and offset per pin (fold()) keep both folded pins inside the notch with a wall to spare; a single shared
axle cannot (it needs ~22 mm of run against 9 mm edge clearance and the switch rule, see README).
Contact break: a strap in the carrier joins the blade to a metal core in the trunnion; past the trunnion end a short
arm on the core rests on a sprung pad in the slot floor only while the pin is open. Folding swings the arm off the
pad (about 20 deg in), so a folded pin is disconnected even with the other pin in a live socket (REQ-FLD-004, F-17).
The pads and leads are the fixed lead frame, insert moulded in the top shell as in D1. Channels, carrier pockets and
trunnion and arm slots open to the pin face with draft, so both shells still pull straight.
"""

import copy
import math
from dataclasses import dataclass, replace
from functools import cache

import d1
import draft
import numpy as np
from build123d import Align, Axis, Box, Compound, Cylinder, Plane, Pos, Rot, Vector
from electrical import BASIC, CLEARANCE, POLES, accessible, c7_contacts, c7_pockets, check_electrical, link_points
from fit import check_fit, check_usr, fit_table
from interface import OUT, Params, check_common, edge_clearance, pin, pin_angles
from provenance import deboss, export_step, export_stl, front

INSERTS = ("link", "c7", "insul", "lead", "core", "pad")  # insert moulded in the top shell (pin assemblies fit after)
C_C_MIN = (Align.CENTER, Align.CENTER, Align.MIN)


@dataclass(frozen=True)
class D2Params(Params):
    top: str = "N"  # pole that folds last, on top, flush with the face
    flush: float = 0.9  # face to the folded top pin's centre plane
    stack: float = 1.9  # under pin below the top pin's carrier and trunnions, which it passes beneath
    off_top: float = -1.5  # hinge axis from the blade centre plane, outward +: inward keeps the root off x = 0
    off_under: float = 2.2  # outward: keeps the under pin's folded tip off the stud-side wall
    trunnion_r: float = 0.8
    trunnion_len: float = 1.5  # into the body each side
    cheek: float = 2.0  # carrier past the blade width each side (room for the under pin's lead beside its channel)
    fold_clr: float = 0.2
    top_trunnion: float = 3.0  # top pin: one-sided hinge, on the end away from the under pin's path
    lead_d: float = 1.4  # lead, link to contact pad: Alpha Wire 5855/7, 22 AWG PTFE 600 V 200 C, OD 1.27 [S RS]
    lead_core: float = 0.8  # 22 AWG 7-strand, 0.76
    top_lead: float = 8.5  # link joint depth below the face, under the pad
    under_lead: float = 10.0  # below the under pin's channel floor: link and sleeve clear the finger in it
    arm_len: float = 3.5  # contact arm, hinge axis to tip: folded, its side clears the pad by >= 3.0 (reinforced)
    arm_t: float = 0.6  # along the hinge axis
    arm_w: float = 0.8
    pad_t: float = 1.6  # fixed contact under the arm tip
    pad_w: float = 1.0  # across the swing
    core_r: float = 0.45  # metal core in the trunnion, strap to arm
    top_run: float = 17.5  # top pin's link runs at this z under the under pin's channel, then rises past it
    finger_d: float = 5.6  # small finger, AS/NZS 3112 J4.8.1: IEC 61032 fig 13 (probe 19, 5.6 dia [S catalogue])


def fold(p: D2Params, pole: str) -> tuple[float, float]:
    """(hinge depth below the face, hinge offset from the blade centre plane, outward +). Folded centre plane depth
    is depth + offset: flush for the top pin, a blade and a gap deeper for the under pin."""
    centre = p.flush if pole == p.top else p.flush + p.pin_th + p.stack
    off = p.off_top if pole == p.top else p.off_under
    return centre - off, off


def place(p: Params, pole: str):
    """Pin root frame on the face: x along the blade width, y inward (toward the plug midpoint), z out of the face."""
    a = pin_angles(p)[pole]
    r = math.radians(a)
    return Pos(p.pin_cx + p.pin_r * math.cos(r), p.pin_cy + p.pin_r * math.sin(r), p.t) * Rot(0, 0, a + 90)


def at(p: Params, pole: str, x: float, y: float, z: float) -> Vector:
    """Root-frame point in part coordinates."""
    return (place(p, pole) * Pos(x, y, z)).position


def root_len(p: D2Params, pole: str) -> float:
    return fold(p, pole)[0] + p.trunnion_r + 0.4


def carrier_span(p: D2Params, pole: str) -> tuple[float, float, float]:
    """(y0, y1, z0) of the carrier block, root frame: on the hinge side of the blade root, never past the blade on
    the other side (that side ends up on top when folded)."""
    d, e = fold(p, pole)
    tr, th = p.trunnion_r, p.pin_th
    y0, y1 = (-th / 2, -e + tr) if e < 0 else (-(e + tr), th / 2)
    return y0, y1, -(d + tr + 0.3)


def ends(p: D2Params, pole: str) -> tuple:
    """(carrier x0, x1, trunnion x0, x1), root frame. The top pin's +x end faces the under pin's path, so its carrier
    stops at the blade there and it hinges on one longer trunnion at -x."""
    cw = p.pin_w / 2 + p.cheek
    if pole == p.top:
        return -cw, p.pin_w / 2, -cw - p.top_trunnion, p.pin_w / 2
    return -cw, cw, -cw - p.trunnion_len, cw + p.trunnion_len


def arm_x(p: D2Params, pole: str) -> tuple[int, float, float]:
    """(side, trunnion end, arm centre x), root frame: the arm sits just past the trunnion on the contact side (top
    pin: its one trunnion at -x; under pin: +x)."""
    _, _, t0, t1 = ends(p, pole)
    side = -1 if pole == p.top else 1
    end = t0 if side < 0 else t1
    return side, end, end + side * (0.1 + p.arm_t / 2)


def contact_local(p: D2Params, pole: str):
    """Moving contact, root frame: strap from the blade end to the hinge axis inside the carrier, core along the
    axis through the trunnion, arm hanging from the axis at the open position."""
    d, e = fold(p, pole)
    side, _, xa = arm_x(p, pole)
    hw = p.pin_w / 2
    xs = sorted((side * (hw - 0.3), side * (hw + 0.5)))  # short: folded, it swings down beside the pad
    ys = sorted((0.0, -e))
    strap = Pos(sum(xs) / 2, sum(ys) / 2, -d) * Box(xs[1] - xs[0], ys[1] - ys[0] + 0.8, 0.8)
    xc = sorted((side * (hw + 0.2), xa + side * p.arm_t / 2))
    core = Pos(sum(xc) / 2, -e, -d) * Rot(0, 90, 0) * Cylinder(p.core_r, xc[1] - xc[0])
    arm = Pos(xa, -e, -d - p.arm_len) * Box(p.arm_t, p.arm_w, p.arm_len + p.core_r, align=C_C_MIN)
    arm &= Pos(xa, -e, -d) * Rot(0, 90, 0) * Cylinder(p.arm_len, p.arm_t)  # tip arc about the axis: wipes, never digs
    return strap + core + arm


def pad_local(p: D2Params, pole: str):
    """Fixed contact, root frame: top at the open arm's tip."""
    d, e = fold(p, pole)
    _, _, xa = arm_x(p, pole)
    return Pos(xa, -e, -d - p.arm_len) * Box(p.arm_t, p.pad_w, p.pad_t, align=(Align.CENTER, Align.CENTER, Align.MAX))


def carrier_local(p: D2Params, pole: str):
    """Carrier block wrapping the blade ends, plus the trunnions along the blade width; the contact runs inside."""
    d, e = fold(p, pole)
    y0, y1, z0 = carrier_span(p, pole)
    x0, x1, t0, t1 = ends(p, pole)
    block = Pos((x0 + x1) / 2, (y0 + y1) / 2, z0) * Box(x1 - x0, y1 - y0, -z0, align=C_C_MIN)
    blade = Pos(0, 0, z0 - 1) * Box(p.pin_w, p.pin_th, -z0 + 2, align=C_C_MIN)
    trunnion = Pos((t0 + t1) / 2, -e, -d) * Rot(0, 90, 0) * Cylinder(p.trunnion_r, t1 - t0)
    return block - blade + trunnion - contact_local(p, pole)


def hinge(p: D2Params, pole: str) -> Axis:
    d, e = fold(p, pole)
    return Axis(at(p, pole, 0, -e, -d), at(p, pole, 1, 0, 0) - at(p, pole, 0, 0, 0))


def angles(deg: float) -> dict:
    """Fold state 0 to 180: the under pin folds over 0..90, then the top pin over 90..180."""
    return {"under": min(deg, 90.0), "top": max(0.0, deg - 90.0)}


def moving(p: D2Params, pole: str, deg: float) -> list:
    """(label, solid) for one pin assembly at its own fold angle (0 open, 90 folded flat)."""
    pp = replace(p, pin_tail=root_len(p, pole))
    sleeve, contact = pin(pp, pin_angles(p)[pole])
    loc = place(p, pole)
    parts = [("sleeve", sleeve), ("pin", contact), ("carrier", loc * carrier_local(p, pole))]
    parts.append(("arm", loc * contact_local(p, pole)))
    return [(lab, s.rotate(hinge(p, pole), -deg)) for lab, s in parts]  # right hand about +x: -deg tips inward


def sweep_local(p: D2Params, pole: str) -> tuple:
    """(y0, y1, z0, half width) bounding carrier and pin root through the fold, root frame, below the face."""
    d, e = fold(p, pole)
    y0, y1, z0 = carrier_span(p, pole)
    pts = [(y, z) for y in (y0, y1, -p.pin_th / 2, p.pin_th / 2) for z in (z0, 0.0, -root_len(p, pole))]
    ys, zs = [], []
    for phi in np.radians(np.arange(0, 90.5, 1.0)):
        for y, z in pts:
            yy, zz = y + e, z + d  # about the hinge; positive phi swings the tip inward (+y)
            ys.append(yy * math.cos(phi) + zz * math.sin(phi) - e)
            zs.append(-yy * math.sin(phi) + zz * math.cos(phi) - d)
    return min(ys), max(ys), min(min(zs), z0)


def local_rect(p: D2Params, pole: str, x0: float, x1: float, y0: float, y1: float, sign: int):
    """Drafted section function for a root-frame rectangle x0..x1, y0..y1."""
    c = at(p, pole, (x0 + x1) / 2, (y0 + y1) / 2, 0)
    return draft.rotated_rect(c.X, c.Y, pin_angles(p)[pole] + 90, x1 - x0, y1 - y0, p.draft, sign=sign)


def rects(p: D2Params, pole: str) -> list:
    """(x0, x1, y0, y1, depth below face) per feature: carrier pocket, fold channel, trunnion slots, arm slot."""
    d, e = fold(p, pole)
    y0, y1, z0 = sweep_local(p, pole)
    x0, x1, t0, t1 = ends(p, pole)
    tr, hw = p.trunnion_r, p.pin_w / 2
    reach = d - e + p.pin_len  # folded tip, inward of the blade centre plane
    floor = d + e + p.pin_th / 2  # folded blade underside
    side, end, xa = arm_x(p, pole)
    ax = sorted((end, xa + side * p.arm_t / 2))
    arm = (ax[0], ax[1], -e - p.arm_len - p.arm_w / 2, -e + max(tr, p.arm_w / 2), d + p.arm_len)
    return [(x0, x1, y0, y1, -z0), (-hw, hw, -e - tr, reach, floor), (t0, t1, -e - tr, -e + tr, d + tr), arm]


def cuts(p: D2Params, grow: float = 0.0) -> list:
    """Open-top pockets in the pin face. The cavity half forms them pulling away from the parting plane, so the
    walls widen toward the face (ruled sign -1); full size at the floor, larger above."""
    c = p.fold_clr + grow
    return [
        draft.ruled(p, p.t - depth - c, p.t + 1, local_rect(p, pole, x0 - c, x1 + c, y0 - c, y1 + c, -1))
        for pole in POLES
        for x0, x1, y0, y1, depth in rects(p, pole)
    ]


def ceiling(p: D2Params) -> float:
    """Inside top of the top shell: a solid deck under the pin face deep enough to hold every pocket, slot, pad and
    lead joint with rib_wall below, so the pockets need no columns (columns ending near the drafted walls leave
    undercut slivers). Thick section: sink risk F-10."""
    deepest = max(depth for pole in POLES for *_, depth in rects(p, pole))
    pad = max(fold(p, pole)[0] + p.arm_len + p.pad_t for pole in POLES)
    return p.t - max(deepest + p.fold_clr, pad) - p.rib_wall


def joint(p: D2Params, pole: str) -> tuple:
    """Link joint under the contact pad."""
    _, e = fold(p, pole)
    c = at(p, pole, arm_x(p, pole)[2], -e, 0)
    return c.X, c.Y, p.t - (p.top_lead if pole == p.top else p.under_lead)


def route(p: D2Params, pole: str) -> list:
    """Link route. Top pin: low under the under pin's channel, rise past it, then to the joint (clear of the stud
    slot as in D1). Under pin: rise first, then to its joint below its channel."""
    end = joint(p, pole)
    pts = link_points(p, pole, end, z_first=True)
    if pole == p.top:
        (x, y0, _), (_, y1, z1) = pts[0], pts[2]
        pts = [pts[0], (x, y0, p.top_run), (x, y1, p.top_run), (x, y1, z1), end]
    return pts


def pads(p: D2Params) -> dict:
    return {pole: place(p, pole) * pad_local(p, pole) for pole in POLES}


def leads(p: D2Params) -> dict:
    """Insulated lead per pole, from the link joint up to the underside of its contact pad: (OD, core)."""
    out = {}
    for pole in POLES:
        x, y, z = joint(p, pole)
        d, _ = fold(p, pole)
        top = p.t - d - p.arm_len - p.pad_t
        out[pole] = tuple(Pos(x, y, z) * Cylinder(r, top - z, align=C_C_MIN) for r in (p.lead_d / 2, p.lead_core / 2))
    return out


def marks(p: D2Params) -> tuple:
    """Small AUTHOR on the pin face (clear of the pockets) and WORDMARK on the room face; the inside floor is taken
    by the pocket columns."""
    up, down = (0, 0, 1), (0, 0, -1)
    top = [(deboss(Plane((p.pin_cx, 3.0, p.t), z_dir=up), size=1.2, depth=0.08), up)]  # below the pockets, as D1
    return top, [(front(p.w / 2, p.l / 2), down)]


@cache
def housing(p: D2Params = D2Params(), marked: bool = True) -> tuple:
    """(top, bottom) shells: D1's, with fold pockets cut into a solid deck under the pin face; marked as d1.housing."""
    metal = c7_pockets(p) + list(d1.envelope(p, route).values()) + [od for od, _ in leads(p).values()]
    metal += list(pads(p).values())
    return d1.shells(p, metal, d1.bosses(p, route, pins=False), cuts(p), marks(p) if marked else None, ceiling(p))


def build(p: D2Params = D2Params(), deg: float = 0.0) -> Compound:
    """deg: fold state 0 (open) to 180 (both folded): the under pin folds over 0..90, then the top pin 90..180."""
    top, bottom = (copy.copy(s) for s in housing(p))  # cached: a Compound would re-parent the originals
    top.label, bottom.label = "housing_top", "housing_bottom"
    state = angles(deg)
    children = [top, bottom]
    for pole in POLES:
        for lab, s in moving(p, pole, state["top" if pole == p.top else "under"]):
            s.label = lab
            children.append(s)
    lk, c7, ins, pd = d1.links(p, path=route), c7_contacts(p), d1.insulation(p, route), pads(p)
    for pole in POLES:
        od, core = leads(p)[pole]
        lead = od - core
        lk[pole].label, c7[pole].label, ins[pole].label = f"link_{pole}", f"c7_{pole}", f"insul_{pole}"
        lead.label, core.label, pd[pole].label = f"lead_{pole}", f"core_{pole}", f"pad_{pole}"
        children += [lk[pole], c7[pole], ins[pole], lead, core, pd[pole]]
    return Compound(label=f"flathead_d2_{deg:g}", children=children)


def channel_faces(p: D2Params) -> list:
    """Finger-reachable surfaces of the face openings. The small finger (IEC 61032 fig 13, per AS/NZS 3112 J4.8.1)
    enters any opening at least its diameter wide down to the floor; narrower ones only to its tip's sagitta."""
    r, c, zone = p.finger_d / 2, p.fold_clr, None
    feats = [rect for pole in POLES for rect in rects(p, pole)]
    for (x0, x1, y0, y1, depth), cut in zip(feats, cuts(p), strict=True):
        half = min(x1 - x0, y1 - y0) / 2 + c
        deep = depth + c if r <= half else r - math.sqrt(r * r - half * half)
        piece = cut & (Pos(0, 0, p.t - deep) * Box(500, 500, deep + 1, align=C_C_MIN))
        if piece.solids():
            zone = piece if zone is None else zone + piece
    return [f for f in zone.faces() if p.t - 1e-3 > f.center().Z]


def live(part: Compound) -> dict:
    """Live conductors per pole for the screen: link wire and lead core. Pins (AS/NZS 3112 sleeve rules) and the
    contact parts (arm, pad, like the C7 contacts) are judged by the test finger, REQ-ELE-004."""
    return {
        pole: [(k, c) for c in part.children for k in ("link", "core") if c.label == f"{k}_{pole}"] for pole in POLES
    }


def moving_parts(part: Compound) -> list:
    return [c for c in part.children if c.label in ("sleeve", "pin", "carrier", "arm")]


def contact_gap(part: Compound, pole: str) -> float:
    """Arm to pad, the pole's moving contact being the arm built for that pole (build order: N parts, then A)."""
    arms = [c for c in part.children if c.label == "arm"]
    pad = next(c for c in part.children if c.label == f"pad_{pole}")
    return arms[POLES.index(pole)].distance_to(pad)


def contact_break(p: D2Params) -> str:
    """REQ-FLD-004: open pins rest on their pads, folding breaks contact, folded pins clear by >= CLEARANCE."""
    under = next(q for q in POLES if q != p.top)
    gaps = {}
    for deg, closed, need in (
        (0, POLES, 0),
        (45, (p.top,), 1e-3),
        (90, (p.top,), CLEARANCE),
        (135, (), 1e-3),
        (180, (), CLEARANCE),
    ):
        part = build(p, deg)
        for pole in POLES:
            gap = contact_gap(part, pole)
            if pole in closed:
                assert gap < 1e-3, f"REQ-FLD-004 {pole} open but its contact is {gap:.2f} mm off the pad"
            elif need:
                assert gap >= need, f"REQ-FLD-004 {pole} at fold state {deg} deg: contact only {gap:.2f} mm off"
                gaps[pole] = gap
    gap_text = ", ".join(f"{q} {g:.2f} mm" for q, g in gaps.items())
    return f"contact break: open pins on their pads; folded ({under} first) contacts clear by {gap_text}"


def check(p: D2Params = D2Params()) -> list[str]:
    open_ = build(p, 0)
    check_common(p, open_)
    check_fit(p)
    check_usr(p, open_)
    lines = d1.check_housing(p, open_, marks(p), INSERTS)
    for deg in range(0, 181, 15):  # fold sequence: no moving part cuts the shells, the pads or the other pin
        part = build(p, deg)
        mov = moving_parts(part)
        fixed = [c for c in part.children if c.label.startswith(("housing", "pad"))]
        hit = sum(v.volume for m in mov for s in fixed if (v := m & s))
        assert hit < 1e-3, f"REQ-FLD-002 fold interference {hit:.4f} mm3 at {deg} deg"
        half = len(mov) // 2
        cross = sum(v.volume for a in mov[:half] for b in mov[half:] if (v := a & b))
        assert cross < 1e-3, f"pins collide {cross:.4f} mm3 at {deg} deg"
    lines.append(contact_break(p))
    folded = moving_parts(build(p, 180))
    proud = max(c.bounding_box().max.Z for c in folded) - p.t
    assert proud <= 1e-6, f"REQ-FLD-002 folded parts {proud:.2f} mm proud of the face"
    bb = Compound(children=folded).bounding_box()
    inside = min(bb.min.X, bb.min.Y, p.w - bb.max.X, p.l - bb.max.Y)
    assert inside >= 1.0, f"folded pins {inside:.2f} mm from the notch outline"
    lines.append(f"fold 0..180 deg clear; folded {-proud:.2f} mm below the face, {inside:.2f} mm inside the outline")
    faces = accessible(p, channel_faces(p), base=draft.body(p))
    housing_ = Compound(children=[c for c in open_.children if c.label.startswith("housing")])
    lines += check_electrical(live(open_), faces, housing_)
    for pole, e in d1.envelope(p, route).items():
        dist = min(e.distance_to(f) for f in faces)
        assert dist >= BASIC, f"REQ-ELE-007 {pole} link sleeve {dist:.2f} mm from accessible < {BASIC}"
        lines.append(f"{pole} sleeve   to accessible {dist:5.2f} mm (basic >= {BASIC})")
    return lines


if __name__ == "__main__":
    p = D2Params()
    report = check(p)
    for name, deg in (("open", 0), ("folded", 180)):
        export_step(build(p, deg), OUT / f"flathead_d2_{name}.step")
    top, bottom = housing(p)
    export_stl(top, OUT / "flathead_d2_top.stl")
    export_stl(bottom, OUT / "flathead_d2_bottom.stl")
    print("\n".join(["fit margin mm, room view, s0 = leftmost socket:", *fit_table(p)]))
    print("\n".join(["housing, fold + REQ-ELE-007 screen:", *report]))
    print(f"ok: edge clearance {edge_clearance(p):.2f} mm")
