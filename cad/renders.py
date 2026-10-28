# SPDX-License-Identifier: CERN-OHL-P-2.0
"""README line art: technical renders of the CAD into docs/images (SVG, white background).
Usage: uv run python cad/renders.py"""

import copy
from pathlib import Path

import d1
import d2
from build123d import Compound, ExportSVG, LineType, Vector
from drawings import finish_svg, on_white
from interface import Params

IMG = Path(__file__).parents[1] / "docs" / "images"
ISO = (-1.0, -1.4, 1.1)  # eye direction for iso views
FAINT = (0.8, 0.8, 0.8)  # mould-mark hairline, light grey


def svg(path: Path, layers: list[tuple], eye=ISO, up=(0, 0, 1), scale=4.0):
    """layers: (shapes, line weight, dashed[, rgb]). One shared camera so layers line up."""
    allshapes = Compound(children=[s for shapes, *_ in layers for s in shapes])
    c = allshapes.bounding_box().center()
    origin = tuple(c + Vector(*eye) * 400)
    out = ExportSVG(scale=scale)
    for i, (shapes, weight, dashed, *rgb) in enumerate(layers):
        name = f"l{i}"
        line = LineType.ISO_DOT if dashed else LineType.CONTINUOUS
        out.add_layer(name, line_color=rgb[0] if rgb else (0.0, 0.0, 0.0), line_weight=weight, line_type=line)
        vis, _ = Compound(children=list(shapes)).project_to_viewport(origin, viewport_up=up, look_at=tuple(c))
        out.add_shape(vis, layer=name)
    path.parent.mkdir(parents=True, exist_ok=True)
    out.write(path)
    on_white(path)
    finish_svg(path, path.stem.replace("_", " ").title())


def by(part, *labels):
    """Copies of the matching children: a Compound built from the originals would re-parent them out of part."""
    return [copy.copy(c) for c in part.children if c.label.split("_")[0] in labels or c.label in labels]


def main():
    """Technical line art; photoreal shots live in photo.py."""
    p1, p2 = Params(), d2.D2Params()
    face = {"eye": (0, 0, 1), "up": (0, 1, 0)}
    for name, mod, p, part, extra in (
        ("d1_wall_face", d1, p1, d1.build(p1), ("sleeve", "pin")),
        ("d2_wall_face", d2, p2, d2.build(p2, 0), ("carrier", "sleeve", "pin")),
    ):  # mould mark as a faint hairline, as it reads on the white part: present, not prominent
        mark = [t for t, _ in mod.marks(p)[0]]
        lines = [*mod.housing(p, marked=False), *by(part, *extra)]
        svg(IMG / f"{name}.svg", [(lines, 0.35, False), (mark, 0.08, False, FAINT)], **face)


if __name__ == "__main__":
    main()
    print("\n".join(sorted(f.name for f in IMG.glob("*.svg"))))
