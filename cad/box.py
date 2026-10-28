# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Stage 13: demo box, reverse tuck end carton sized from the D1 part. Usage: uv run python cad/box.py

Writes docs/images/box_dieline.svg at 1:1: print on A4 card, cut solid lines, fold dashed. Inside: the plug pins up,
C7 boss along the depth. Panels: glue flap, side, front, side, back; top tuck on the front, bottom tuck on the back.
"""

from pathlib import Path

import d1
from build123d import Align, Box, Edge, ExportSVG, LineType, Plane, Polygon, Pos, Rectangle, Rot, Text, Unit, extrude
from drawings import finish_svg, on_white
from interface import FONT, Params
from provenance import AUTHOR

PATH = Path(__file__).parents[1] / "docs" / "images" / "box_dieline.svg"
A4 = (210.0, 297.0)
MARGIN = 10.0  # printer margin
CLR = 1.0  # part to inside wall, each side
BOARD = 0.4  # card thickness, added per panel


def inside(p: Params) -> tuple[float, float, float]:
    """(length, depth, height) of the box interior: part x, y, z plus clearance."""
    s = d1.build(p).bounding_box().size
    return s.X + 2 * CLR, s.Y + 2 * CLR, s.Z + 2 * CLR


def dieline(p: Params = Params(), glue: float = 12.0, tuck: float = 12.0, dust: float = 0.45):
    """(cut face, fold edges, print) of the flat carton, panel heights along y. Polygons wind CCW so they fuse."""
    lb, db, h = (v + BOARD for v in inside(p))
    xs = [0, glue, glue + db, glue + db + lb, glue + 2 * db + lb, glue + 2 * db + 2 * lb]
    box = lambda x0, x1, y0, y1: Pos((x0 + x1) / 2, (y0 + y1) / 2) * Rectangle(x1 - x0, y1 - y0)  # noqa: E731
    ch = 3.0  # chamfer on flap corners
    faces = [
        Polygon((glue, h), (0, h - 4), (0, 4), (glue, 0), align=None),  # glue flap
        box(xs[1], xs[5], 0, h),  # side, front, side, back
        box(xs[2], xs[3], h, h + db),  # top lid on the front
        Polygon((xs[2], h + db), (xs[3], h + db), (xs[3] - ch, h + db + tuck), (xs[2] + ch, h + db + tuck), align=None),
        box(xs[4], xs[5], -db, 0),  # bottom lid on the back
        Polygon((xs[4] + ch, -db - tuck), (xs[5] - ch, -db - tuck), (xs[5], -db), (xs[4], -db), align=None),
    ]
    for x0, x1 in ((xs[1], xs[2]), (xs[3], xs[4])):  # dust flaps on both sides, top and bottom
        d = dust * lb
        faces.append(Polygon((x0, h), (x1, h), (x1, h + d - ch), (x1 - ch, h + d), (x0, h + d), align=None))
        faces.append(Polygon((x0, -d), (x1 - ch, -d), (x1, -d + ch), (x1, 0), (x0, 0), align=None))
    cut = faces[0]
    for f in faces[1:]:
        cut = cut + f
    folds = [Edge.make_line((x, 0), (x, h)) for x in xs[1:5]]
    folds += [Edge.make_line((x0, y), (x1, y)) for x0, x1, y in ((xs[1], xs[4], h), (xs[2], xs[3], h + db))]
    folds += [
        Edge.make_line((x0, y), (x1, y)) for x0, x1, y in ((xs[1], xs[2], 0), (xs[3], xs[5], 0), (xs[4], xs[5], -db))
    ]
    label = [
        Pos((xs[2] + xs[3]) / 2, h * 0.62) * Text("Flathead", 6, font_path=FONT),
        Pos((xs[2] + xs[3]) / 2, h * 0.42) * Text("POC Prototype", 3, font_path=FONT),
        Pos((xs[2] + xs[3]) / 2, h * 0.30) * Text("Not for Use on Mains", 3, font_path=FONT),
    ]
    bx = (xs[4] + xs[5]) / 2  # back panel: credit, as the NOTICE asks of products
    for frac, line, size in (
        (0.62, f"Design {AUTHOR}", 2.4),
        (0.52, "CERN-OHL-P-2.0", 2.0),
        (0.40, "github.com/", 1.6),
    ):
        label.append(Pos(bx, h * frac) * Text(line, size, font_path=FONT))
    label.append(Pos(bx, h * 0.34) * Text("cpwillis-pocs/flathead", 1.6, font_path=FONT))
    return cut, folds, label


def carton(p: Params = Params(), open_deg: float = 115.0) -> dict:
    """Folded carton for renders, z up, centred on x/y: tray, lid hinged at the back top edge, front print as ink.
    The plug sits inside pins up."""
    lb, db, h = inside(p)
    t = BOARD
    tray = Pos(0, 0, -t) * Box(lb + 2 * t, db + 2 * t, h + t, align=(Align.CENTER, Align.CENTER, Align.MIN))
    tray -= Box(lb, db, h + 1, align=(Align.CENTER, Align.CENTER, Align.MIN))
    lid = Box(lb + 2 * t, db + 2 * t, t, align=(Align.CENTER, Align.MAX, Align.MIN))  # hinge on the x axis
    lid += Pos(0, -db - 2 * t, 0) * Box(lb, t, 12, align=(Align.CENTER, Align.MIN, Align.MAX))  # tuck flap
    lid = Pos(0, db / 2 + t, h) * Rot(-open_deg, 0, 0) * lid
    ink = [
        extrude(
            Plane((0, -db / 2 - t, h * f), x_dir=(1, 0, 0), z_dir=(0, -1, 0)) * Text(line, size, font_path=FONT), 0.05
        )
        for f, line, size in ((0.62, "Flathead", 6), (0.42, "POC Prototype", 3), (0.30, "Not for Use on Mains", 3))
    ]
    s = d1.build(p).bounding_box()
    plug = Pos(-s.center().X, -s.center().Y, 0)
    seat = Box(s.size.X + 0.6, s.size.Y + 0.6, 20, align=(Align.CENTER, Align.CENTER, Align.MIN))
    insert = Box(lb - 0.2, db - 0.2, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)) - seat  # card cradle
    return {"tray": tray, "lid": lid, "ink": ink, "plug": plug, "insert": insert}


def check(p: Params = Params()) -> str:
    cut, _, _ = dieline(p)
    bb = cut.bounding_box()
    assert len(cut.faces()) == 1, "dieline is not one piece"
    assert A4[0] - 2 * MARGIN >= bb.size.X and A4[1] - 2 * MARGIN >= bb.size.Y, "dieline does not fit A4"
    lb, db, h = inside(p)
    return f"box: inside {lb:.1f} x {db:.1f} x {h:.1f} mm, flat {bb.size.X:.0f} x {bb.size.Y:.0f} mm on A4"


def write(p: Params = Params()) -> None:
    cut, folds, label = dieline(p)
    svg = ExportSVG(unit=Unit.MM, margin=MARGIN)
    svg.add_layer("cut", line_weight=0.3)
    svg.add_layer("fold", line_weight=0.3, line_type=LineType.ISO_DASH)
    svg.add_layer("print", line_weight=0.15)
    svg.add_shape(cut.faces()[0].outer_wire(), layer="cut")
    svg.add_shape(folds, layer="fold")
    svg.add_shape(label, layer="print")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    svg.write(PATH)
    on_white(PATH)
    finish_svg(PATH, "Flathead Demo Box Dieline")


if __name__ == "__main__":
    print(check())
    write()
