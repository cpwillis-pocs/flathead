# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Stage 6: two-plate moulds for the D1 and D2 housing shells. Usage: uv run python cad/mould.py

Parting plane z = c7_z (the shell split plane), straight +/-z pull, no side actions (dfm.py: 0 undercuts).
Cavity half forms the shell's outside, core half rises into the cup. Steel under a downward-facing part surface (top
shell) or over an upward-facing one (bottom shell) must be core: found by extruding those faces to the parting plane.
Top shell is insert moulded with the per-pole lead frame (pin, link, C7 contact), held in pockets in both halves.
Part scaled for shrink; inserts are metal and not scaled (POC mismatch <= shrink x size, ~0.2 mm).
Feed: sprue through the cavity half, runner and edge gate on the parting plane into the back wall.
"""

import math
from dataclasses import dataclass

import d1
import d2
from build123d import Align, Box, Cone, Cylinder, Pos, extrude
from interface import C_C_MIN, MIN3, OUT, Params
from provenance import export_step, export_stl

SHOT_MAX = 16_000.0  # mm3, HoliPress 16+ class benchtop press [V]


@dataclass(frozen=True)
class MouldParams:
    shrink: float = 0.007  # Makrolon 6555: 0.70 parallel, 0.75 % normal [V Covestro]
    margin: float = 12.0  # steel round the part, x and y
    floor: float = 12.0  # steel behind the part on each side, z
    gate_w: float = 2.5
    gate_h: float = 1.2  # ~55 % of the 2.2 mm wall
    gate_len: float = 1.5
    runner_w: float = 3.0
    runner_h: float = 2.0
    runner_len: float = 8.0
    sprue_d: tuple[float, float] = (3.0, 5.0)  # at the runner, at the nozzle
    ejector_d: float = 2.0
    vent_w: float = 3.0
    vent_depth: float = 0.05
    dowel_d: float = 4.0


def solids(shape):
    """Keep only the solids of a boolean result: features on the parting plane can leave stray faces or edges."""
    found = list(shape.solids())
    out = found[0]
    for x in found[1:]:
        out += x
    return out


def half_space(z: float, upper: bool, big: float = 1000.0):
    return Pos(-big / 2, -big / 2, z if upper else z - big) * Box(big, big, big, align=MIN3)


def near(face, inserts, tol: float = 0.35) -> bool:
    """Face formed against a metal insert (the insert fills that space in the tool). tol covers the shrink offset
    between the scaled part and the unscaled inserts."""
    for i in inserts:
        try:
            if face.distance_to(i) < tol:
                return True
        except Exception:
            continue
    return False


def shadow(part, pz: float, upper_part: bool, block, inserts=()):
    """Space between the part and the parting plane that is only reachable from the parting plane side: faces that
    look toward the parting plane, extruded onto it."""
    sign = -1 if upper_part else 1
    faces = [f for f in part.faces() if f.normal_at(f.center()).Z * sign > 1e-6 and not near(f, inserts)]
    if not faces:
        return None
    region = None
    for f in faces:  # fuse one by one: a loose compound of overlapping prisms breaks later booleans
        prism = extrude(f, amount=abs(f.center().Z - pz) + 1, dir=(0, 0, sign))
        assert prism.is_valid, f"invalid shadow prism from {f.geom_type} at {f.center()}"
        region = prism if region is None else region + prism
    side = block & half_space(pz, upper_part)  # bound by the block: a huge half-space fails silently in OCC
    out = (region.clean() & side) - part
    assert out.volume < side.volume, "shadow boolean failed"
    return out


def mould(p: Params, mp: MouldParams, shell, upper: bool, inserts: list) -> dict:
    """Core and cavity halves for one shell. upper: shell sits above the parting plane (cavity side +z)."""
    pz, k = p.c7_z, 1 + mp.shrink
    c = (p.w / 2, p.l / 2, pz)  # scale about a point on the parting plane so the split face stays on it
    part = shell.scale(k).translate(tuple(v * (1 - k) for v in c))  # build123d scales about the world origin
    bb = part.bounding_box()
    stick = max((i.bounding_box().max.Z for i in inserts), default=bb.max.Z) if upper else bb.min.Z
    z_out = (max(bb.max.Z, stick) + mp.floor) if upper else (bb.min.Z - mp.floor)
    z_in = pz - mp.floor if upper else pz + mp.floor
    x0, y0 = bb.min.X - mp.margin, bb.min.Y - mp.margin
    w, l = bb.size.X + 2 * mp.margin, bb.size.Y + 2 * mp.margin + mp.runner_len  # noqa: E741
    block = Pos(x0, y0, min(z_in, z_out)) * Box(w, l, abs(z_out - z_in), align=MIN3)
    out_side, in_side = block & half_space(pz, upper), block & half_space(pz, not upper)
    under = shadow(part, pz, upper, block, inserts)
    core = solids(in_side - part)  # the bottom shell's tongue stands above the parting plane, inside the core half
    core = solids(core + under) if under else core
    cavity = solids(solids(out_side - part) - under) if under else solids(out_side - part)

    # feed: edge gate into the back wall (y max) at mid x, runner and sprue in the cavity half
    s = 1 if upper else -1
    xm, yb = (bb.min.X + bb.max.X) / 2, bb.max.Y
    gate = Pos(xm, yb - 0.2, pz) * Box(mp.gate_w, mp.gate_len + 0.2, mp.gate_h, align=_ymin(s))
    runner = Pos(xm, yb + mp.gate_len, pz) * Box(mp.runner_w, mp.runner_len, mp.runner_h, align=_ymin(s))
    sy = yb + mp.gate_len + mp.runner_len - mp.runner_w / 2
    r0, r1 = mp.sprue_d[0] / 2, mp.sprue_d[1] / 2
    h = abs(z_out - pz)
    sprue = (
        Pos(xm, sy, pz) * Cone(r0, r1, h, align=C_C_MIN)
        if upper
        else Pos(xm, sy, z_out) * Cone(r1, r0, h, align=C_C_MIN)
    )
    feed = gate + runner + sprue
    cavity -= feed

    # vents on the cavity face opposite the gate, ejector pins through the core onto the rim, dowels at the corners
    vents = [
        Pos(x, y0, pz) * Box(mp.vent_w, bb.min.Y - y0 + 0.2, mp.vent_depth, align=_ymin(s))
        for x in (bb.min.X + 6, xm, bb.max.X - 6)
    ]
    cavity -= vents
    ejectors = ejector_points(part, pz, upper, mp.ejector_d)
    pins = [Pos(x, y, min(z_in, pz)) * Cylinder(mp.ejector_d / 2, abs(z_in - pz), align=C_C_MIN) for x, y in ejectors]
    core -= pins
    dowels = [
        Pos(x, y, min(z_in, z_out)) * Cylinder(mp.dowel_d / 2, abs(z_out - z_in), align=C_C_MIN)
        for x in (x0 + 5, x0 + w - 5)
        for y in (y0 + 5, y0 + l - 5)
    ]
    core, cavity = core - dowels - inserts, cavity - dowels - inserts
    return {
        "core": core,
        "cavity": cavity,
        "part": part,
        "feed": feed,
        "block": block,
        "ejectors": ejectors,
        "inserts": inserts,
        "pins": pins,
        "vents": vents,
        "dowels": dowels,
    }


def ejector_points(part, pz: float, upper: bool, d: float, n: int = 4, step: float = 1.5) -> list:
    """Up to n ejector positions where a full pin face (plus 0.3 mm) lands on part material at the parting plane,
    spread out by greedy max-min distance."""
    bb, r, probe = part.bounding_box(), d / 2 + 0.3, 0.3 if upper else -0.3
    ring = [(0, 0), (r, 0), (-r, 0), (0, r), (0, -r), (0.7 * r, 0.7 * r), (-0.7 * r, -0.7 * r)]
    xs = [bb.min.X + i * step for i in range(int(bb.size.X / step) + 1)]
    ys = [bb.min.Y + j * step for j in range(int(bb.size.Y / step) + 1)]
    ok = [(x, y) for x in xs for y in ys if all(part.is_inside((x + dx, y + dy, pz + probe)) for dx, dy in ring)]
    picked = []
    while ok and len(picked) < n:
        best = max(ok, key=lambda q: min((math.dist(q, c) for c in picked), default=math.dist(q, (bb.min.X, bb.min.Y))))
        picked.append(best)
        ok.remove(best)
    return picked


def _ymin(s: int):
    """Box alignment: start at y, grow along +z (s > 0) or -z from the anchor."""
    return (Align.CENTER, Align.MIN, Align.MIN if s > 0 else Align.MAX)


def vol(shape) -> float:
    """Volume of a boolean result; build123d returns None when the operands do not meet."""
    return shape.volume if shape else 0.0


def check(m: dict, name: str) -> str:
    core, cavity, part = m["core"], m["cavity"], m["part"]
    assert core.is_valid and cavity.is_valid, f"{name}: invalid mould half"
    overlap = vol(core & cavity)
    assert overlap < 1e-2, f"{name}: core and cavity overlap {overlap:.3f} mm3"
    for half in (core, cavity):
        clash = vol(half & part)
        assert clash < 1e-2, f"{name}: steel inside the part {clash:.3f} mm3"
    assert len(m["ejectors"]) >= 3, f"{name}: only {len(m['ejectors'])} ejector pins land on the part"
    filled = core.volume + cavity.volume + part.volume + sum(i.volume for i in m["inserts"])
    holes = sum(x.volume for x in m["pins"] + m["dowels"]) + m["feed"].volume + sum(v.volume for v in m["vents"])
    gap = m["block"].volume - filled - holes
    assert abs(gap) < 0.01 * part.volume, f"{name}: {gap:.1f} mm3 unaccounted (void or double-counted steel)"
    shot = part.volume + m["feed"].volume
    assert shot <= SHOT_MAX, f"{name}: shot {shot:.0f} mm3 over press capacity {SHOT_MAX:.0f}"
    return (
        f"{name}: shot {shot / 1000:.1f} cm3 (part {part.volume / 1000:.1f}), {len(m['ejectors'])} ejectors, "
        f"block {m['block'].bounding_box().size.X:.0f} x {m['block'].bounding_box().size.Y:.0f} x "
        f"{m['block'].bounding_box().size.Z:.0f} mm, unaccounted {gap:.1f} mm3"
    )


def build(mp: MouldParams = MouldParams(), designs=("d1", "d2")) -> dict:
    """Tools for each design's two shells; the top shell is insert moulded with the lead frame."""
    out = {}
    for name in designs:
        mod = {"d1": d1, "d2": d2}[name]
        p = mod.D2Params() if name == "d2" else Params()
        part = mod.build(p)
        top, bottom = mod.housing(p)
        inserts = [c for c in part.children if c.label.split("_")[0] in mod.INSERTS]
        out[f"{name}_top"] = mould(p, mp, top, True, inserts)
        out[f"{name}_bottom"] = mould(p, mp, bottom, False, [])
    return out


if __name__ == "__main__":
    out = OUT / "mould"
    out.mkdir(parents=True, exist_ok=True)
    for name, m in build().items():
        print(check(m, name))
        for half in ("core", "cavity"):
            export_step(m[half], out / f"{name}_{half}.step")
            export_stl(m[half], out / f"{name}_{half}.stl")
