# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Stage 10: ultrasonic weld nest for the D1 and D2 shells (same outline). Usage: uv run python cad/jig.py

Bottom shell drops into the nest (drafted walls, so the pocket narrows downward: no undercut), top shell sits on its
tongue, the horn presses the wall face. The nest stops lip below the joint so the weld line stays clear and visible.
Pocket is the drafted outer scaled up (>= clr each side), with a relief for the C7 boss. Printed (resin or PC).
The horn needs reliefs for the pins: it bears on the wall face round them (welder vendor tooling, not modelled).
"""

from dataclasses import dataclass

import d1
import draft
from build123d import Box, Plane, Pos
from interface import MIN3, OUT, Params
from provenance import deboss, debossed, export_stl


@dataclass(frozen=True)
class NestParams:
    clr: float = 0.15  # pocket over the shell, each side; printed-fit allowance [U]
    wall: float = 6.0
    floor: float = 4.0
    lip: float = 1.5  # nest top below the parting plane


def nest(p: Params = Params(), n: NestParams = NestParams()):
    shell = draft.outer(p)  # convex, so scaling about its centre clears it everywhere
    bb = shell.bounding_box()
    c = bb.center()
    grow = 1 + 2 * n.clr / min(bb.size.X, bb.size.Y, bb.size.Z)  # uniform: exact (non-uniform scale goes B-spline)
    pocket = shell.scale(grow).translate(tuple(c * (1 - grow)))  # build123d scales about the world origin
    top = p.c7_z - n.lip
    half = p.c7_pitch / 2 + p.c7_r + n.clr  # open-topped relief for the C7 boss's lower half
    relief = Pos(p.c7_x - half, -p.c7_depth - n.wall - 1, p.c7_z - p.c7_r - n.clr) * Box(
        2 * half, p.c7_depth + n.wall + 1, top, align=MIN3
    )
    block = Pos(-n.wall, -p.c7_depth - n.wall, -n.floor) * Box(
        p.w + 2 * n.wall, p.l + p.c7_depth + 2 * n.wall, top + n.floor, align=MIN3
    )
    return block - pocket - relief - mark(p, n)


def mark(p: Params, n: NestParams):
    """AUTHOR on the nest's top face, on the back rim."""
    return deboss(Plane((p.w / 2, p.l + n.wall / 2, p.c7_z - n.lip), z_dir=(0, 0, 1)))


def check(p: Params = Params(), n: NestParams = NestParams()) -> str:
    jig = nest(p, n)
    _, bottom = d1.housing(p)
    clash = jig & bottom
    clash = clash.volume if clash else 0.0
    assert jig.is_valid and len(jig.solids()) == 1, "nest is not one valid solid"
    assert debossed(jig, mark(p, n), (0, 0, 1)), "provenance mark missing"
    assert clash < 1e-3, f"bottom shell clashes with the nest {clash:.3f} mm3"
    gap = jig.distance_to(bottom)
    assert 0 < gap <= 2 * n.clr, f"shell {gap:.2f} mm from the nest: loose or not seated"
    return f"weld nest: {jig.volume / 1000:.1f} cm3, shell clearance {gap:.2f} mm, joint {n.lip:g} mm above the nest"


if __name__ == "__main__":
    print(check())
    out = OUT / "jig"
    out.mkdir(parents=True, exist_ok=True)
    export_stl(nest(), out / "d1_weld_nest.stl")
