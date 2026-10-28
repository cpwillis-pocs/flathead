# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Flathead D1: fixed AU pins. Usage: uv run python cad/d1.py

Housing is two 2.2 mm shells split on z = c7_z, the plane through the C7 and stud axes, so the C7 bores, stud slot
and in-plane link runs mould half in each shell with a straight z pull and no side actions. Per pole, pin + link + C7
contact form one lead-frame insert: pin insert moulded in the top shell, the rest captured between shells, which are
then ultrasonically welded on the split plane. Links run in an insulating tube (supplementary insulation), so the
moulded plastic between a link and an accessible surface only has to be basic insulation, interface included.
"""

import copy
import math
from functools import cache
from itertools import pairwise

import draft
from build123d import Box, Compound, Plane, Pos, fillet, loft
from draft import rect, rotated_rect
from electrical import (
    BASIC,
    POLES,
    accessible,
    c7_contacts,
    c7_pockets,
    c7_points,
    chain,
    check_electrical,
    link_points,
)
from fit import BRICKS, GPOS, check_fit, check_usr, fit_table, scene
from interface import MIN3, OUT, Params, check_common, edge_clearance, pin, pin_angles, slot_depth
from provenance import deboss, debossed, export_step, export_stl, front

INSERTS = ("sleeve", "pin", "link", "c7", "insul")  # label prefixes of the lead-frame parts


def tail_point(p: Params, pole: str) -> tuple[float, float, float]:
    """Link joint on the pin tail bottom, link_join along the blade from its centre."""
    r = math.radians(pin_angles(p)[pole])
    tx, ty = -math.sin(r) * p.link_join, math.cos(r) * p.link_join
    return p.pin_cx + p.pin_r * math.cos(r) + tx, p.pin_cy + p.pin_r * math.sin(r) + ty, p.t - p.pin_tail


def route(p: Params, pole: str) -> list:
    """Link route: C7 contact rear, rise first (clear of the stud slot), then to the pin tail joint."""
    return link_points(p, pole, tail_point(p, pole), z_first=True)


def links(p: Params, w: float | None = None, cap: bool = True, path=route) -> dict:
    """C7 contact rear to each pin's joint along path(p, pole); w widens them (sleeve envelope)."""
    return {pole: chain(path(p, pole), w or p.link_w, cap) for pole in POLES}


def envelope(p: Params, path=route) -> dict:
    """Sleeved links: wire plus insulating tube, which stops flat at the C7 contact rear."""
    return links(p, p.link_w + 2 * p.link_sleeve, cap=False, path=path)


def insulation(p: Params, path=route, trim=()) -> dict:
    """Insulating tubes: envelope less the wire, cut back where they meet the C7 contact and trim (D1: pin tails)."""
    wire, c7 = links(p, path=path), c7_contacts(p)
    return {pole: e - wire[pole] - c7[pole] - list(trim) for pole, e in envelope(p, path).items()}


def tails(p: Params) -> list:
    return [pin(p, a)[0] for a in pin_angles(p).values()]


def side_wall_x(p: Params) -> float:
    """Inner face of the stud-side features (T-slot floor): the side wall is measured from here."""
    return slot_depth(p)


def cavity(p: Params, ceiling: float | None = None):
    """Hollow inside the housing, one wall in from every outer face and the stud slot; narrows away from the
    parting plane at draft (core side). ceiling: inside top of the top shell (default one wall under the face)."""
    g, x0, rv = p.draft, side_wall_x(p) + p.wall, p.wall  # vertical inside corners at 1 x wall
    sec = rect(x0, p.w - p.wall, p.wall, p.l - p.wall, (g, g, g, g), 1, (rv, rv, rv, p.corner_r - p.wall))
    r = 0.45 * p.wall  # floor and ceiling edges; well under rv so OCC blends the corners
    lower = fillet(draft.edges_at(draft.ruled(p, p.wall, p.c7_z, sec), p.wall), r)  # halves filleted apart
    top = ceiling or p.t - p.wall
    upper = fillet(draft.edges_at(draft.ruled(p, p.c7_z, top, sec), top), r)
    return lower + upper


def band(p: Params, z0: float, hw0: float, z1: float, hw1: float):
    """Ring on the rim centreline (mid-wall at the parting plane), half width hw0 at z0 to hw1 at z1."""
    x0, ri, rc = side_wall_x(p) + p.wall / 2, p.wall, p.corner_r - p.wall / 2

    def outline(hw: float):
        return rect(
            x0 - hw,
            p.w - p.wall / 2 + hw,
            p.wall / 2 - hw,
            p.l - p.wall / 2 + hw,
            radii=(ri / 2 + hw, ri / 2 + hw, ri / 2 + hw, rc + hw),
        )(0)

    outer = loft([Pos(0, 0, z0) * outline(hw0), Pos(0, 0, z1) * outline(hw1)], ruled=True)
    inner = loft([Pos(0, 0, z0) * outline(-hw0), Pos(0, 0, z1) * outline(-hw1)], ruled=True)
    return outer - inner


def joint(p: Params) -> tuple:
    """(tongue with energy director on the bottom rim, groove in the top rim). Drafted, narrowing away from the
    parting plane; the director is a 90 deg ridge, so its flanks are 45 deg."""
    pz, g = p.c7_z, draft.tan(p.draft)
    hw = p.tongue_w / 2
    tongue = band(p, pz, hw, pz + p.tongue_h, hw - g * p.tongue_h)
    ed = band(p, pz + p.tongue_h - 0.01, p.ed_h, pz + p.tongue_h + p.ed_h - 0.02, 0.02)
    gh = p.tongue_h + p.ed_h + 0.2  # 0.2 mm melt space over the director
    hg = hw + p.joint_clr
    return tongue + ed, band(p, pz, hg, pz + gh, hg - g * gh)


def merge(lo: float, hi: float, wall_lo: float, wall_hi: float, gap: float) -> tuple:
    """Column extent, run into the cavity wall when it ends within gap of it: a column stopping just short of a
    drafted wall leaves an undercut sliver between them."""
    return (wall_lo - 1 if lo - wall_lo < gap else lo), (wall_hi + 1 if wall_hi - hi < gap else hi)


def bosses(p: Params, path=route, pins=True):
    """Columns round each insert, full cavity height so nothing inside either cup is an undercut: C7 contact tubes,
    pin tails and the in-plane link runs. Walls round the metal are rib_wall (about 55% of wall, limits sink).
    Columns widen away from the parting plane at draft so the core slides off them. pins=False: no pin-tail columns."""
    g, rw, z0, z1, out = p.draft, p.rib_wall, p.wall, p.t - p.wall, []
    grow = (g, g, g, g)
    for x, _ in c7_points(p).values():
        half = p.c7_contact_od / 2 + rw
        out.append(draft.ruled(p, z0, z1, rect(x - half, x + half, 0, p.c7_contact_back + rw, grow, -1)))
    for pole in POLES:
        a = math.radians(pin_angles(p)[pole])
        cx, cy = p.pin_cx + p.pin_r * math.cos(a), p.pin_cy + p.pin_r * math.sin(a)
        sec = rotated_rect(cx, cy, pin_angles(p)[pole] + 90, p.pin_w + 2 * rw, p.pin_th + 2 * rw, g)
        if pins:
            out.append(draft.ruled(p, z0, z1, sec))
        h = p.link_w / 2 + p.link_sleeve + rw
        for (xa, ya, _), (xb, yb, _) in pairwise(path(p, pole)):
            x0, x1 = merge(min(xa, xb) - h, max(xa, xb) + h, side_wall_x(p) + p.wall, p.w - p.wall, rw)
            y0, y1 = merge(min(ya, yb) - h, max(ya, yb) + h, p.wall, p.l - p.wall, rw)
            sec = rect(x0, x1, y0, y1, grow, -1)
            out.append(draft.ruled(p, z0, z1, sec))
    return out


def marks(p: Params) -> tuple:
    """Deboss tools per shell, (tool, face normal): AUTHOR on the pin face at the unused earth position (read when
    unplugged) and on the bottom shell's inside floor, clear of the columns; WORDMARK centred on the room face."""
    up, down = (0, 0, 1), (0, 0, -1)
    top = [(deboss(Plane((p.pin_cx, p.pin_cy - p.pin_r, p.t), z_dir=up), size=1.2, depth=0.08), up)]  # mould-mark size
    bottom = [(deboss(Plane((p.w / 2, p.l - p.wall - 2.6, p.wall), z_dir=up), size=2.0), up)]
    return top, bottom + [(front(p.w / 2, p.l / 2), down)]


def split(shape, z: float, upper: bool):
    big = 500
    return shape & (Pos(-big / 2, -big / 2, z if upper else z - big) * Box(big, big, big, align=MIN3))


def shells(p: Params, metal: list, columns: list, cuts_top=(), mark_tools=None, ceiling=None) -> tuple:
    """(top, bottom) drafted shells round a lead frame: outer less cavity plus columns, less the metal pockets, split
    on the parting plane with the welded tongue and groove; cuts_top are open-top pockets in the pin face."""
    outer = draft.body(p)
    solid = outer - cavity(p, ceiling) + [b & outer for b in columns]
    solid -= metal
    tongue, groove = joint(p)
    gap = p.c7_contact_od + 2 * p.rib_wall  # tongue stops across each C7 pocket: a round cut would overhang the core
    g, top = p.draft, p.c7_z + 3 * p.tongue_h  # gap widens away from the plane so the tongue ends carry draft
    gaps = [
        draft.ruled(p, p.c7_z, top, rect(x - gap / 2, x + gap / 2, -2 * p.wall, 2 * p.wall, (g, g, 0, 0), -1))
        for x, _ in c7_points(p).values()
    ]
    mark_top, mark_bottom = ([t for t, _ in m] for m in (mark_tools or ([], [])))
    top = split(solid, p.c7_z, True) - groove - list(cuts_top) - mark_top
    return top, split(solid, p.c7_z, False) + (tongue - metal - gaps) - mark_bottom


@cache
def housing(p: Params, marked: bool = True) -> tuple:
    """(top, bottom) shells with insert pockets; marked=False leaves out the deboss marks (line art draws them)."""
    sleeves, _ = zip(*(pin(p, a) for a in pin_angles(p).values()), strict=True)
    metal = list(sleeves) + c7_pockets(p) + list(envelope(p).values())
    return shells(p, metal, bosses(p), mark_tools=marks(p) if marked else None)


def build(p: Params = Params()) -> Compound:
    sleeves, contacts = zip(*(pin(p, a) for a in pin_angles(p).values()), strict=True)
    lk, c7, ins = links(p), c7_contacts(p), insulation(p, trim=tails(p))
    top, bottom = (copy.copy(s) for s in housing(p))  # cached: a Compound would re-parent the originals
    for s in sleeves:
        s.label = "sleeve"
    for c in contacts:
        c.label = "pin"
    for pole in POLES:
        lk[pole].label, c7[pole].label, ins[pole].label = f"link_{pole}", f"c7_{pole}", f"insul_{pole}"
    top.label, bottom.label = "housing_top", "housing_bottom"
    return Compound(
        label="flathead_d1", children=[top, bottom, *sleeves, *contacts, *lk.values(), *c7.values(), *ins.values()]
    )


def check_housing(p: Params, part: Compound, mark_tools=None, inserts=INSERTS) -> list[str]:
    """Shells: valid, touch only on the split plane, no insert clash; mean wall 2V/A as a thin-wall proxy."""
    top, bottom = (next(c for c in part.children if c.label == n) for n in ("housing_top", "housing_bottom"))
    assert (top & bottom).volume < 1e-3, "shells overlap"
    metal = [c for c in part.children if c.label.split("_")[0] in inserts]
    clash = sum((m & s).volume for m in metal for s in (top, bottom))
    assert clash < 1e-3, f"insert clashes with housing {clash:.3f} mm3"
    for shell, tools in zip((top, bottom), mark_tools or marks(p), strict=True):
        assert all(debossed(shell, t, n) for t, n in tools), f"{shell.label}: provenance mark missing"
    gap = joint(p)[1].distance_to(draft.stud_slot(p))
    assert gap >= 0.5, f"shell joint groove {gap:.2f} mm from the stud slot"
    lines = [f"joint groove to stud slot {gap:.2f} mm"]
    for name, s in (("top", top), ("bottom", bottom)):
        mean = 2 * s.volume / s.area
        assert mean <= 2 * p.wall, f"{name} shell mean section {mean:.2f} mm, still a solid block"
        lines.append(f"{name} shell {s.volume:.0f} mm3, mean section {mean:.2f} mm")
    return lines


def check(p: Params = Params()) -> list[str]:
    part = build(p)
    check_common(p, part)
    check_fit(p)
    check_usr(p, part)
    shells = check_housing(p, part)
    live = {pole: [("link", c) for c in part.children if c.label == f"link_{pole}"] for pole in POLES}
    housing = Compound(children=[c for c in part.children if c.label.startswith("housing")])
    faces = accessible(p, base=draft.body(p))
    lines = shells + check_electrical(live, faces, housing)
    for pole, e in envelope(p).items():  # outside the tube: basic insulation, interface counted as creepage
        d = min(e.distance_to(f) for f in faces)
        assert d >= BASIC, f"REQ-ELE-007 {pole} link sleeve {d:.2f} mm from accessible < {BASIC}"
        lines.append(f"{pole} sleeve   to accessible {d:5.2f} mm (basic >= {BASIC})")
    return lines


if __name__ == "__main__":
    p = Params()
    report = check(p)
    part = build(p)
    OUT.mkdir(exist_ok=True)
    export_step(part, OUT / "flathead_d1.step")
    export_step(scene(p, build(p), BRICKS[-1], GPOS[0], 1), OUT / "flathead_d1_scene.step")  # 140W, right socket
    export_stl(part.children[0], OUT / "flathead_d1_top.stl")
    export_stl(part.children[1], OUT / "flathead_d1_bottom.stl")
    print("\n".join(["fit margin mm, room view, s0 = leftmost socket:", *fit_table(p)]))
    print("\n".join(["housing + REQ-ELE-007 screen:", *report]))
    print(f"ok: edge clearance {edge_clearance(p):.2f} mm, exported to {OUT}")
