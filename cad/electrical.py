# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Conductive path shared by D1 and D2: C7 contacts and brass links; REQ-ELE-007 clearance/creepage screen.

Screen: straight-line distance from internal live conductors to finger-accessible surfaces. A straight line is a lower
bound on clearance (through air) and creepage (along surfaces), so a pass is conservative. Clearance is asserted.
A path under the creepage target that runs through housing plastic all the way to the surface (air only in the live
part's own pocket) is solid insulation: its plastic length is asserted >= DTI. Any other path under target is flagged
for a surface-path check on the stage 5 drawing, not asserted.
Excluded: AU pins (AS/NZS 3112 sleeve rules) and C7 contacts (IEC 60320 C7 geometry, test finger REQ-ELE-004).
Accessible: outer faces of the interface body (wall face, sides, slot over the brick's metal stud, mating face), not
the C7 boss; designs add their own (eg the D2 fold recess).
"""

from itertools import pairwise

from build123d import Align, Cone, Cylinder, Plane, Pos, Rot, Sphere
from interface import Params, body

CLEARANCE, CREEPAGE, POLE = 3.0, 5.0, 2.5  # mm, IEC 60664-1 F.2 (4 kV reinforced), F.5 (250 V PD2 IIIa) [S]
DTI = 2.0  # mm, reinforced solid insulation without material tests, IEC/EN 60335-1 cl 29.3 default [S]
BASIC = 2.5  # mm, basic insulation creepage (IEC 60664-1 Table F.5): outside a supplementary insulating sleeve [S]
POLES = ("N", "A")  # N on the left lobe and left pin, viewed from pins


def c7_points(p: Params) -> dict[str, tuple[float, float]]:
    """(x, z) of each C7 contact axis."""
    face = Plane((p.c7_x, 0, p.c7_z), x_dir=(1, 0, 0), z_dir=(0, -1, 0)).rotated((0, 0, p.c7_angle))
    pts = {pole: face.from_local_coords((sx * p.c7_pitch / 2, 0)) for pole, sx in zip(POLES, (-1, 1), strict=True)}
    return {pole: (v.X, v.Z) for pole, v in pts.items()}


def c7_tube(p: Params, x: float, z: float, d: float):
    y0 = -p.c7_depth + p.c7_front_wall
    return (
        Pos(x, y0, z)
        * Rot(-90, 0, 0)
        * Cylinder(d / 2, p.c7_contact_back - y0, align=(Align.CENTER, Align.CENTER, Align.MIN))
    )


def c7_contacts(p: Params) -> dict:
    return {k: c7_tube(p, x, z, p.c7_contact_od) - c7_tube(p, x, z, p.c8_pin_d) for k, (x, z) in c7_points(p).items()}


def c7_pockets(p: Params) -> list:
    """Contact pockets with a 45 deg cone at the front and a round rear end: no flat end faces, so the half pockets
    the bottom shell's core forms need no draft."""
    y0, r, rh = -p.c7_depth + p.c7_front_wall, p.c7_contact_od / 2, p.c7_hole_d / 2
    out = []
    for x, z in c7_points(p).values():
        cone = Pos(x, y0, z) * Rot(90, 0, 0) * Cone(r, rh, r - rh, align=(Align.CENTER, Align.CENTER, Align.MIN))
        out.append(c7_tube(p, x, z, p.c7_contact_od) + cone + Pos(x, p.c7_contact_back, z) * Sphere(r))
    return out


AXIS_ROT = {0: Rot(0, 90, 0), 1: Rot(-90, 0, 0), 2: Rot(0, 0, 0)}  # turn +z onto +x, +y, +z


def seg(a, b, w: float, cap_a: bool = True):
    """Round bar of diameter w from a to b (axis-aligned), ball ends so corners join (cap_a False: flat at a). Round,
    so pockets lying in the parting plane are half-cylinders and need no draft."""
    ball = Rot(37, 23, 0) * Sphere(w / 2)  # seams off the parting plane (coincident seams break mould booleans)
    out = Pos(*a) * ball + Pos(*b) * ball if cap_a else Pos(*b) * ball
    axis = next((i for i in range(3) if abs(b[i] - a[i]) > 1e-9), None)
    if axis is None:
        return out
    lo = a if a[axis] < b[axis] else b
    bar = Rot(0, 0, 45) * Cylinder(w / 2, abs(b[axis] - a[axis]), align=(Align.CENTER, Align.CENTER, Align.MIN))
    return out + Pos(*lo) * AXIS_ROT[axis] * bar


def link_points(p: Params, pole: str, end: tuple[float, float, float], z_first: bool = False) -> list:
    """Route from the C7 contact rear to end (x, y, z): along y, then x, then z; z_first runs z first, to pass above
    or below parts in the way."""
    x, z = c7_points(p)[pole]
    if z_first:
        return [(x, p.c7_contact_back, z), (x, p.c7_contact_back, end[2]), (x, end[1], end[2]), end]
    return [(x, p.c7_contact_back, z), (x, end[1], z), (end[0], end[1], z), end]


def chain(pts: list, w: float, cap: bool = True):
    """Round bar through the points (axis-aligned runs); cap False leaves the first end flat (a tube stops there)."""
    out = seg(pts[0], pts[1], w, cap)
    for a, b in pairwise(pts[1:]):
        out += seg(a, b, w)
    return out


def accessible(p: Params, extra=(), base=None) -> list:
    """base: the outer body the part actually has (D1 passes its drafted body)."""
    return [f for f in (base or body(p)).faces() if f.bounding_box().min.Y > -1e-6] + list(extra)


def through_solid(solid, a, b, n: int = 60) -> float | None:
    """Plastic length on the straight path a (live) to b (accessible) if, once in the solid, it stays in it to b;
    else None (the path crosses air the stage 5 surface-path check must follow)."""
    inside = [solid.is_inside(a + (b - a) * (i / n)) for i in range(1, n)]
    if True not in inside or not all(inside[inside.index(True) :]):
        return None
    k = inside.index(True)
    return (b - a).length * (1 if k == 0 else (n - k - 1) / n)  # k 0: embedded live part, plastic from a


def screen(live: dict[str, list], faces: list, solid=None) -> tuple[list[str], float, float, float]:
    """Returns (report lines, min distance to accessible, min pole-to-pole, min solid path under creepage) for live
    parts grouped by pole. solid: the housing, to sort short paths into solid insulation and surface paths."""
    lines, worst, thin = [], float("inf"), float("inf")
    for pole, parts in live.items():
        for name, s in parts:
            paths = [s.distance_to_with_closest_points(f) for f in faces]
            d = min(r[0] for r in paths)
            worst = min(worst, d)
            short = [r for r in paths if r[0] < CREEPAGE]
            solid_len = [through_solid(solid, a, b) for _, a, b in short] if solid else [None] * len(short)
            flag = ""
            if None in solid_len:
                flag = "  < creepage target, check surface path"
            elif short:
                thin = min(thin, *solid_len)
                flag = f"  through {min(solid_len):.2f} mm solid (DTI >= {DTI})"
            lines.append(f"{pole} {name:8} to accessible {d:5.2f} mm{flag}")
    pole = min(a.distance_to(b) for _, a in live["N"] for _, b in live["A"])
    lines.append(f"N to A {pole:5.2f} mm")
    return lines, worst, pole, thin


def check_electrical(live: dict[str, list], faces: list, solid=None) -> list[str]:
    lines, worst, pole, thin = screen(live, faces, solid)
    assert worst >= CLEARANCE, f"REQ-ELE-007 clearance {worst:.2f} mm < {CLEARANCE}"
    assert pole >= POLE, f"REQ-ELE-007 pole to pole {pole:.2f} mm < {POLE}"
    assert thin >= DTI, f"REQ-ELE-007 solid insulation {thin:.2f} mm < {DTI}"
    return lines
