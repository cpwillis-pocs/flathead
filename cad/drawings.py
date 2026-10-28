# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Stage 3 outputs: part drawings (docs/drawings) and REQ-ELE-007 section diagrams (docs/electrical).

Drawings: third-angle projection (AS 1100), front = wall face viewed from the pins, A3, overall and pin-position dims.
Sections: cut through the closest live-to-accessible pair found by the ELE-007 screen, with that distance marked.
Usage: uv run python cad/drawings.py
"""

import datetime
import re
from pathlib import Path

import d1
import d2
import draft
from build123d import (
    Compound,
    Draft,
    Edge,
    ExportSVG,
    ExtensionLine,
    LineType,
    PageSize,
    Plane,
    Pos,
    Rectangle,
    TechnicalDrawing,
    Text,
    Vector,
)
from electrical import POLES, accessible
from interface import FONT, Params
from provenance import stamp_svg

DOCS = Path(__file__).parents[1] / "docs"

_make_text = Compound.make_text.__func__


@classmethod
def _bundled_font_text(cls, *args, font_path=None, **kw):
    """All text, title blocks and dimensions included, in the bundled CC0 font (no system Arial outlines)."""
    return _make_text(cls, *args, font_path=font_path or FONT, **kw)


Compound.make_text = _bundled_font_text
SCALE = 2.0
ISSUED = datetime.date(2026, 10, 26)  # title block date, fixed so reruns do not diff
DRAFT = Draft(font_size=3.5, decimal_precision=1, display_units=False)
VIEWS = {  # name: (eye direction, up, sheet centre at 1:1 sheet mm)
    "front": ((0, 0, 1), (0, 1, 0), (-85, 0)),
    "right": ((1, 0, 0), (0, 1, 0), (25, 0)),  # third angle: right view to the right
    "top": ((0, 1, 0), (0, 0, -1), (-85, 85)),  # third angle: top view above
    "iso": ((1, 1, 1), (0, 0, 1), (110, 85)),
}


def on_white(path: Path) -> None:
    """White background so black lines stay visible in dark themes."""
    bg = r'\1<rect x="-10000" y="-10000" width="20000" height="20000" fill="white"/>'
    path.write_text(re.sub(r"(<svg[^>]*>)", bg, path.read_text(), count=1))


def finish_svg(path: Path, title: str) -> None:
    """Stamp attribution (provenance.stamp_svg), round coordinates to 0.01 (halves file size), then sort elements
    inside each group: OCC returns edges in varying order, so reruns diff without this."""
    stamp_svg(path, title)
    out, block = [], []
    for line in path.read_text().splitlines():
        if line.lstrip().startswith(("<path", "<line", "<circle", "<ellipse", "<polyline", "<polygon")):
            block.append(re.sub(r"(\.\d\d)\d+", r"\1", line))
            continue
        out += sorted(block) + [line]
        block = []
    path.write_text("\n".join(out + sorted(block)) + "\n")


def view(shape, eye, up, centre, scale: float = SCALE):
    """Orthographic view of shape, scaled and centred on the sheet.
    Returns (visible, hidden, to) where to(u, v) maps view coords (part coords minus the look-at point) to sheet."""
    c = shape.bounding_box().center()
    vis, hid = shape.project_to_viewport(tuple(c + Pos(*eye).position * 500), viewport_up=up, look_at=tuple(c))
    bc = Compound(children=list(vis)).bounding_box().center()
    shift = Pos(centre[0] - bc.X * scale, centre[1] - bc.Y * scale)

    def to(u: float, v: float) -> tuple[float, float]:
        return centre[0] + (u - bc.X) * scale, centre[1] + (v - bc.Y) * scale

    return [shift * e.scale(scale) for e in vis], [shift * e.scale(scale) for e in hid], to, c


def sheet(title: str, number: str, scale: float = SCALE) -> TechnicalDrawing:
    return TechnicalDrawing(
        designed_by="cpwillis",
        design_date=ISSUED,
        page_size=PageSize.A3,
        title=title,
        sub_title=f"Flathead POC, Scale {scale:g}:1, Dims ASSUMED",
        drawing_number=number,
        sheet_number=1,
        drawing_scale=1 / scale,
        nominal_text_size=5,
    )


def dims_front(p: Params, view_to, c) -> list:
    """Overall width/length and pin midpoint position; front view u, v are part x, y."""
    to = lambda x, y: view_to(x - c.X, y - c.Y)  # noqa: E731
    return [
        ExtensionLine([to(0, 0), to(p.w, 0)], 22, DRAFT, label=f"{p.w:g}"),  # clears the C7 boss
        ExtensionLine([to(0, 0), to(0, p.l)], -10, DRAFT, label=f"{p.l:g}"),
        ExtensionLine([to(0, p.l), to(p.pin_cx, p.l)], -8, DRAFT, label=f"{p.pin_cx:g}"),
        ExtensionLine([to(p.w, 0), to(p.w, p.pin_cy)], 8, DRAFT, label=f"{p.pin_cy:g}"),
    ]


def drawing(shape, p: Params, title: str, number: str, path: Path, dims: bool = True, scale: float = SCALE):
    """A3 sheet, third angle; dims: the body's overall and pin-midpoint dimensions (off for small parts)."""
    svg = ExportSVG(scale=1.0)
    svg.add_layer("border", line_weight=0.5)
    svg.add_layer("visible", line_weight=0.35)
    svg.add_layer("hidden", line_weight=0.18, line_type=LineType.ISO_DASH)
    svg.add_layer("dims", line_weight=0.18)
    svg.add_shape(sheet(title, number, scale), layer="border")
    for name, (eye, up, centre) in VIEWS.items():
        vis, hid, to, c = view(shape, eye, up, centre, scale)
        svg.add_shape(vis, layer="visible")
        if name != "iso":
            svg.add_shape(hid, layer="hidden")
        if name == "front" and dims:
            for d in dims_front(p, to, c):
                svg.add_shape(d, layer="dims")
        if name == "right" and dims:  # u = -z, v = y
            ends = [to(-(z - c.Z), -c.Y) for z in (p.t, 0)]
            svg.add_shape(ExtensionLine(ends, 22, DRAFT, label=f"{p.t:g}"), layer="dims")
    path.parent.mkdir(parents=True, exist_ok=True)
    svg.write(path)
    finish_svg(path, f"{number} {title}")


def closest(live: dict, faces: list):
    """Closest (distance, part name, point on live part, point on accessible face) over all live parts."""
    best = None
    for pole, parts in live.items():
        for name, s in parts:
            for f in faces:
                d, a, b = s.distance_to_with_closest_points(f)
                if best is None or d < best[0]:
                    best = (d, f"{pole} {name}", a, b)
    return best


def section(shell, live: dict, faces: list, title: str, path: Path):
    """Cut through the closest live point, distance marked: y-z plane viewed from +x, or x-z viewed from -y when the
    path runs mostly along x (a y-z cut would hide it)."""
    d, name, a, b = closest(live, faces)
    v = b - a
    n, xd = (Vector(1, 0, 0), (0, 1, 0)) if abs(v.X) <= max(abs(v.Y), abs(v.Z)) else (Vector(0, -1, 0), (1, 0, 0))
    into = -0.05 if v.dot(n) > 0 else 0.05  # 0.05 into the live part: a cut on its tangent face is empty
    cut = Plane(a + n * into, x_dir=xd, z_dir=n) * Rectangle(400, 400)
    solids = [shell] + [s for parts in live.values() for _, s in parts]
    cuts = [c for c in (s & cut for s in solids) if c.area > 0]
    bp = b - n * v.dot(n)  # projected onto the cut plane
    mark = Edge.make_line(a, bp)
    label = Plane((a + bp) / 2 + Vector(*xd) * 2.5, x_dir=xd, z_dir=n) * Text(f"{d:.2f} mm", 1.5, font_path=FONT)
    svg = ExportSVG(scale=6.0)
    svg.add_layer("body", line_weight=0.25)
    svg.add_layer("live", line_weight=0.5)
    svg.add_layer("measure", line_weight=0.35, line_type=LineType.ISO_DASH_DOT)
    eye = a + n * 500
    for layer, shapes in (("body", cuts[:1]), ("live", cuts[1:]), ("measure", [mark, label])):
        if not shapes:
            continue
        vis, _ = Compound(children=shapes).project_to_viewport(eye, viewport_up=(0, 0, 1), look_at=(a.X, a.Y, a.Z))
        svg.add_shape(vis, layer=layer)
    path.parent.mkdir(parents=True, exist_ok=True)
    svg.write(path)
    finish_svg(path, f"{title} clearance section")
    return f"{title}: closest {name} {d:.2f} mm at x={a.X:.2f}"


if __name__ == "__main__":
    p1, p2 = Params(), d2.D2Params()
    part1, part2 = d1.build(p1), d2.build(p2)
    shell = lambda part, n: next(c for c in part.children if c.label == n)  # noqa: E731
    sheets = [
        (shell(part1, "housing_top"), p1, "D1 Housing Top", "FH-0001", True),
        (shell(part1, "housing_bottom"), p1, "D1 Housing Bottom", "FH-0002", True),
        (shell(part2, "housing_top"), p2, "D2 Housing Top", "FH-1001", True),
        (shell(part2, "housing_bottom"), p2, "D2 Housing Bottom", "FH-1002", True),
        (d2.carrier_local(p2, p2.top), p2, "D2 Pin Carrier, Top Pin", "FH-1003", False, 5.0),
    ]
    for shape, p, title, number, dims, *scale in sheets:
        name = f"{number}_{title.lower().replace(',', '').replace(' ', '_')}.svg"
        drawing(shape, p, title, number, DOCS / "drawings" / name, dims, *scale)
    live1 = {pole: [("link", c) for c in part1.children if c.label == f"link_{pole}"] for pole in POLES}
    shells1 = Compound(children=[shell(part1, "housing_top"), shell(part1, "housing_bottom")])
    shells2 = Compound(children=[shell(part2, "housing_top"), shell(part2, "housing_bottom")])
    faces1, faces2 = accessible(p1, base=draft.body(p1)), accessible(p2, d2.channel_faces(p2), base=draft.body(p2))
    print(section(shells1, live1, faces1, "D1", DOCS / "electrical" / "d1_clearance_section.svg"))
    print(section(shells2, d2.live(part2), faces2, "D2", DOCS / "electrical" / "d2_clearance_section.svg"))
