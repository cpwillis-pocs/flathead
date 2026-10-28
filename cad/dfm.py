# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Mouldability report for a part pulled along +/-z: draft per face and undercuts by ray cast.

Each face is assigned to the mould half its normal faces (+z cavity side or -z core side, either way per part).
Undercut: a ray from a sample point on the face (3x3 uv grid, points confirmed on the face) along its pull direction
hits the part again. Draft classes: none (< 0.45 deg), mating (0.45 to 0.95, the 0.5 deg walls), general (>= 0.95).
Faces formed against a metal insert are skipped: the insert stays in the part, nothing slides on them.
Usage: uv run python cad/dfm.py
"""

import math

from build123d import Axis, Vector, Vertex

NONE_BELOW, MATE_BELOW = 0.45, 0.95  # deg


def samples(f) -> list[Vector]:
    pts = [f.position_at(u, v) for u in (0.2, 0.5, 0.8) for v in (0.2, 0.5, 0.8)]
    return [pt for pt in pts if f.distance_to(Vertex(pt)) < 1e-6]


def blocked(solid, start: Vector, d: Vector) -> bool:
    return any((pt - start).dot(d) > 1e-3 for pt, _ in solid.find_intersection_points(Axis(start, d)))


def touches(f, insert) -> bool:
    """Face lies against the insert. OCC's distance can throw on some pairs: fall back to bounding boxes."""
    try:
        return f.distance_to(insert) < 1e-4
    except Exception:
        a, b = f.bounding_box(), insert.bounding_box()
        return all(
            getattr(a.min, k) >= getattr(b.min, k) - 1e-3 and getattr(a.max, k) <= getattr(b.max, k) + 1e-3
            for k in "XYZ"
        )


def draft_deg(f) -> float:
    """Smallest draft over the face's sample points (curved faces vary)."""
    pts = samples(f) or [f.center()]
    return min(90 - math.degrees(math.acos(max(-1.0, min(1.0, abs(f.normal_at(pt).Z))))) for pt in pts)


def analyse(part, inserts=()) -> dict:
    solid = part.solids()[0] if len(part.solids()) == 1 else part
    out = {"none": [], "mate": [], "undercuts": [], "insert": []}
    for f in part.faces():
        if any(touches(f, i) for i in inserts):
            out["insert"].append(f)
            continue
        deg = draft_deg(f)
        if deg < NONE_BELOW:
            out["none"].append(f)
            continue
        if deg < MATE_BELOW:
            out["mate"].append(f)
        n = f.normal_at(f.center())
        d = Vector(0, 0, 1 if n.Z > 0 else -1)
        if any(blocked(solid, pt + f.normal_at(pt) * 0.01, d) for pt in samples(f)):
            out["undercuts"].append(f)
    return out


def report(name: str, part, inserts=()) -> list[str]:
    r = analyse(part, inserts)
    area = {k: sum(f.area for f in v) for k, v in r.items()}

    def where(fs):
        return ", ".join(f"({f.center().X:.1f}, {f.center().Y:.1f}, {f.center().Z:.1f})" for f in fs[:6])

    lines = [
        f"{name}: {len(part.faces())} faces, no draft {area['none']:.1f} mm2 ({len(r['none'])}), mating "
        f"{area['mate']:.0f} mm2, insert-formed {area['insert']:.0f} mm2, {len(r['undercuts'])} undercut faces"
    ]
    if r["none"]:
        lines.append(f"  no draft at {where(r['none'])}")
    if r["undercuts"]:
        lines.append(f"  undercuts at {where(r['undercuts'])}")
    return lines


if __name__ == "__main__":
    import d1
    import d2

    lines = []
    for mod in (d1, d2):  # moulded shells: straight pull, fully drafted; only the top shell carries inserts
        part = mod.build()
        inserts = [c for c in part.children if c.label.split("_")[0] in mod.INSERTS]
        for c in part.children:
            if c.label.startswith("housing"):
                ins = inserts if c.label == "housing_top" else []
                lines += report(f"{part.label} {c.label}", c, ins)
                r = analyse(c, ins)
                assert not r["undercuts"] and not r["none"], f"{part.label} {c.label}: undercuts or undrafted faces"
    print("\n".join(lines))
