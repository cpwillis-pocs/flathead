# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Draft as tapered primitives about the parting plane z = c7_z (straight z pull), for the moulded D1 and D2 shells.

Each solid is ruled between sections, nominal at the parting plane and offset linearly with distance d from it.
Outer walls and pockets the core fills narrow away from the plane (sign +1); columns, part material hanging inside a
cup, widen away from it (sign -1) so the core slides off. draft_mate on the notch-mating walls (x=0, y=0), the stud
slot and the C7 boss; draft elsewhere.
"""

import math
from itertools import pairwise

from build123d import Axis, Polyline, Pos, Rectangle, Rot, extrude, fillet, loft, make_face
from interface import Params, c7_profile, head_end, slot_depth


def tan(deg: float) -> float:
    return math.tan(math.radians(deg))


def ruled(p: Params, z0: float, z1: float, section):
    """Solid z0..z1 whose section at height z is section(d), d = |z - c7_z|; split at the parting plane."""
    pz = p.c7_z
    zs = sorted({z0, z1} | ({pz} if z0 < pz < z1 else set()))
    out = None
    for a, b in pairwise(zs):
        s = loft([Pos(0, 0, a) * section(abs(a - pz)), Pos(0, 0, b) * section(abs(b - pz))], ruled=True)
        out = s if out is None else out + s
    return out


def rect(x0, x1, y0, y1, angles=(0, 0, 0, 0), sign=1, radii=(0, 0, 0, 0), radius_deg=0.0):
    """Section function: rectangle offset per side (x0, x1, y0, y1 angles) by sign * tan(angle) * d. radii round the
    (x0 y0, x1 y0, x0 y1, x1 y1) corners. Constant radii (radius_deg 0) give corner arcs the same draft as their
    sides; radius_deg shrinks (sign 1) or grows them."""

    def section(d: float):
        ox0, ox1, oy0, oy1 = (sign * tan(a) * d for a in angles)
        a0, a1, b0, b1 = x0 + ox0, x1 - ox1, y0 + oy0, y1 - oy1
        r = Pos((a0 + a1) / 2, (b0 + b1) / 2) * Rectangle(a1 - a0, b1 - b0)
        for (ix, iy), rad in zip(((0, 0), (-1, 0), (0, -1), (-1, -1)), radii, strict=True):
            if rad:
                r = fillet(r.vertices().group_by(Axis.X)[ix].group_by(Axis.Y)[iy], rad - sign * tan(radius_deg) * d)
        return r

    return section


def edges_at(solid, z: float, keep=lambda c: True):
    """Edges lying in the plane z (eg the rim of a top or bottom face), filtered on their centre."""

    def flat(e) -> bool:
        b = e.bounding_box()
        return abs(b.min.Z - z) < 1e-6 and abs(b.max.Z - z) < 1e-6

    return [e for e in solid.edges() if flat(e) and keep(e.center())]


def rotated_rect(cx, cy, angle, w, h, deg, sign=-1):
    """Section function: w x h rectangle at (cx, cy) turned by angle, every side offset by deg (columns grow)."""

    def section(d: float):
        e = -sign * tan(deg) * d
        return Pos(cx, cy) * Rot(0, 0, angle) * Rectangle(w + 2 * e, h + 2 * e)

    return section


def v_prism(p: Params, axis: str, at: float, deg: float, keep_above: bool, big: float = 60.0):
    """Half-space bounded by a shallow V about the parting plane: coord >= at + tan(deg) * |z - c7_z| (keep_above)
    or <= at - tan(deg) * |z - c7_z|. axis 'y' bounds y (prism along x); 'x' bounds x (prism along y)."""
    pz, s = p.c7_z, 1 if keep_above else -1
    v = [
        (at + s * tan(deg) * big, pz - big),
        (at, pz),
        (at + s * tan(deg) * big, pz + big),
        (at + s * big, pz + big),
        (at + s * big, pz - big),
    ]
    if axis == "y":
        face = make_face(Polyline(*[(-big, y, z) for y, z in v], close=True))
        return extrude(face, 2 * big, dir=(1, 0, 0))
    face = make_face(Polyline(*[(x, -big, z) for x, z in v], close=True))
    return extrude(face, 2 * big, dir=(0, 1, 0))


def stud_slot(p: Params):
    """T-slot for the brick's mushroom-head stud: opening then T-channel, open at the mating face (y = 0), centred on
    the parting plane. Drafted at draft_mate (it mates the brick), narrowing away from the plane."""
    m, pz = p.draft_mate, p.c7_z
    sw, hw = p.slot_w, p.head_w
    opening = ruled(p, pz - sw / 2, pz + sw / 2, rect(-1, p.lip_t, -1, p.slot_len, (0, 0, 0, m)))
    # lip back face drafted too; start 0.05 inside the lip so the opening still meets the channel at its edges
    channel = ruled(p, pz - hw / 2, pz + hw / 2, rect(p.lip_t - 0.05, slot_depth(p), -1, head_end(p), (m, m, 0, m)))
    return opening + channel


def outer(p: Params):
    """Drafted outer block, no slot or boss: convex."""
    m, g = p.draft_mate, p.draft
    out = ruled(p, 0, p.t, rect(0, p.w, 0, p.l, (m, g, m, g), 1, (p.notch_corner_r, 0, 0, p.corner_r)))
    brick_side = lambda c: c.X > 1 and c.Y > 1  # noqa: E731  x=0 and y=0 edges meet the brick flush: stay sharp
    return fillet(edges_at(out, 0, brick_side) + edges_at(out, p.t, brick_side), p.edge_r)


def body(p: Params):
    """Drafted interface body: outer, stud T-slot, C7 boss. Same features as interface.body."""
    boss = extrude(c7_profile(p, 1.0), p.c7_depth + 1) & v_prism(p, "y", -p.c7_depth, p.draft_mate, True)
    return outer(p) - stud_slot(p) + boss
