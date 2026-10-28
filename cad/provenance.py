# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Attribution carried by every output, so the origin stays traceable when files travel without the NOTICE.

CERN-OHL-P-2.0 section 4 is the legal hook (products give access to the NOTICE); these marks are the evidence:
- SVG: title, desc, RDF dc:creator/dc:rights, role/aria, cpwillis- id prefix, stroke colour fingerprint, 2 % hidden text
- STL/STEP: header author and rights; JPEG: EXIF artist/copyright plus two hidden marks a high-pass filter recovers
- Parts: AUTHOR debossed into the moulded and printed parts (deboss()), so tools and parts inherit it
tests/test_checks.py fails if any mark goes missing.
"""

import hashlib
import re
import subprocess
from pathlib import Path

from build123d import Face, GeomType, Plane, Text, Wire, extrude
from build123d import export_step as _export_step
from build123d import export_stl as _export_stl
from interface import FONT

AUTHOR = "cpwillis"
ID = f"{AUTHOR}-"  # SVG element id prefix
YEAR = 2026
WORDMARK = "flathead"  # small deboss on the room-facing face, like a brand mark on the brick
URL = "https://github.com/cpwillis-pocs/flathead"
RIGHTS = f"(c) {YEAR} {AUTHOR}. CERN-OHL-P-2.0: products and derivatives carry the NOTICE crediting {AUTHOR}, {URL}"
INK = "rgb(2,1,0)"  # line colour fingerprint: near black, one channel nudged
PAPER = "#fffefe"  # background fingerprint
MARK_LIFT = 3.0  # hidden image marks: grey levels added in the glyphs, survives JPEG q88
MARK_MIN = 0.3  # mark_score threshold, grey levels
DOCS = Path(__file__).parents[1] / "docs"
SUMS = DOCS / "SHA256SUMS"


def polygon(wire, step: float = 0.04):
    """Wire as a polygon: line edges keep their ends, curves sampled every step."""
    pts = []
    for e in wire.order_edges():
        n = 1 if e.geom_type == GeomType.LINE else max(4, int(e.length / step))
        for q in (e.position_at(i / n) for i in range(n)):
            if not pts or (q - pts[-1]).length > step / 2:  # drop near-duplicates at edge joins: they break offsets
                pts.append(q)
    if (pts[0] - pts[-1]).length <= step / 2:
        pts.pop()
    return Wire.make_polygon(pts, close=True)


def deboss(plane: Plane, text: str = AUTHOR, size: float = 2.2, depth: float = 0.15, taper: float = 3.0):
    """Cutting tool for text sunk into a face; plane origin on the face, z_dir out of it. Tapered so it draws; glyphs
    go polygonal first (tapered offsets of font splines fail in OCC), trying coarser sampling until the offset holds."""
    out = None
    for f in (plane * Text(text, size, font_path=FONT)).faces():
        for step in (0.04, 0.06, 0.08, 0.12):
            g = Face(polygon(f.outer_wire(), step), [polygon(w, step) for w in f.inner_wires()])
            try:
                cut = extrude(g, amount=depth, dir=-plane.z_dir, taper=taper)
                break
            except RuntimeError:
                continue
        else:
            raise RuntimeError(f"cannot taper a glyph of {text!r}")
        out = cut if out is None else out + cut
    return out


def debossed(solid, tool, normal) -> bool:
    """Mark present: tool space empty in the solid, with material right behind it (not cut into air)."""
    inside = solid & tool
    behind = solid & tool.translate(tuple(-0.1 * v for v in normal))
    return (inside.volume if inside else 0.0) < 1e-6 and (behind.volume if behind else 0.0) > 0.4 * tool.volume


def front(x: float, y: float, size: float = 2.6):
    """Wordmark tool for the room-facing face z = 0, reading from the room (-z)."""
    return deboss(Plane((x, y, 0), x_dir=(-1, 0, 0), z_dir=(0, 0, -1)), WORDMARK, size)


def export_step(shape, path: Path) -> None:
    _export_step(shape, path)
    s = path.read_text()
    s = s.replace("FILE_DESCRIPTION(('Open CASCADE Model')", f"FILE_DESCRIPTION(('Flathead by {AUTHOR}', '{RIGHTS}')")
    s = s.replace("('Author')", f"('{AUTHOR}')").replace("(\n    'Open CASCADE')", f"('{URL}')")
    path.write_text(s)


def export_stl(shape, path: Path) -> None:
    _export_stl(shape, path)
    data = bytearray(path.read_bytes())
    data[:80] = f"Flathead {path.stem} (c) {YEAR} {AUTHOR} CERN-OHL-P-2.0 {URL}".encode()[:80].ljust(80, b"\0")
    path.write_bytes(bytes(data))


def stamp_svg(path: Path, title: str) -> None:
    """Metadata, accessibility, fingerprint and hidden mark into an SVG written by ExportSVG."""
    s = path.read_text()
    s = re.sub(rf'id="(?!{ID})([^"]+)"', rf'id="{ID}\1"', s)
    s = s.replace("rgb(0,0,0)", INK).replace('fill="white"', f'fill="{PAPER}"')
    x0, y0, w, h = (float(v) for v in re.search(r'viewBox="([^"]+)"', s).group(1).split())
    head = (
        f'<title id="{ID}title">{title}</title>\n'
        f"<desc>Flathead by {AUTHOR}. © {YEAR} {AUTHOR}. CERN-OHL-P-2.0, keep the NOTICE crediting {AUTHOR}.</desc>\n"
        '<metadata><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/"><rdf:Description rdf:about="">'
        f"<dc:title>{title}</dc:title><dc:creator>{AUTHOR}</dc:creator><dc:rights>{RIGHTS}</dc:rights>"
        f"<dc:source>{URL}</dc:source></rdf:Description></rdf:RDF></metadata>\n"
    )
    s = re.sub(r"<svg ([^>]*)>", rf'<svg \1 role="img" aria-labelledby="{ID}title">\n{head}', s, count=1)
    mark = (  # last, so it draws over any background
        f'<text id="{ID}mark" x="{x0 + w / 2:.1f}" y="{y0 + h * 0.9:.1f}" font-size="{h / 40:.1f}" '
        f'text-anchor="middle" fill="{INK}" fill-opacity="0.02" opacity="0.02">{AUTHOR}</text>\n'  # ink at 2 % on white
    )
    s = s.replace("</svg>", f"{mark}</svg>")
    path.write_text(s)


def svg_marked(path: Path) -> bool:
    s = path.read_text()
    return all(k in s for k in (f"<dc:creator>{AUTHOR}</dc:creator>", f'aria-labelledby="{ID}title"', INK, f"{ID}mark"))


def mark_spots(w: int, h: int) -> list[tuple[int, int, int]]:
    """(x, y, font px) of the two hidden marks: lower left and upper right, so one crop cannot remove both."""
    size = max(12, w // 45)
    return [(int(w * 0.06), int(h * 0.86), size), (int(w * 0.70), int(h * 0.08), size)]


def mark_image(img):
    """Two hidden AUTHOR marks: MARK_LIFT grey levels added inside the glyphs, the same faint step on any
    background (a white blend shows on dark paint). img: PIL RGB image."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    layer = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(layer)
    for x, y, size in mark_spots(*img.size):
        draw.text((x, y), AUTHOR, fill=255, font=ImageFont.load_default(size))
    lift = (np.asarray(layer, float) / 255 * MARK_LIFT)[..., None]
    return Image.fromarray(np.clip(np.asarray(img, float) + lift, 0, 255).astype("uint8"))


def mark_score(img) -> list[float]:
    """Per mark: mean high-pass response inside the glyphs minus round them, grey levels (>= MARK_MIN: present)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    grey = img.convert("L")
    hp = np.asarray(grey, float) - np.asarray(grey.filter(ImageFilter.GaussianBlur(4)), float)
    out = []
    for x, y, size in mark_spots(*img.size):
        glyph = Image.new("L", img.size, 0)
        ImageDraw.Draw(glyph).text((x, y), AUTHOR, fill=255, font=ImageFont.load_default(size))
        g = np.asarray(glyph) > 128
        box = np.zeros_like(g)
        ys, xs = np.nonzero(g)
        box[ys.min() - size : ys.max() + size, xs.min() - size : xs.max() + size] = True
        out.append(float(hp[g].mean() - hp[box & ~g].mean()))
    return out


def exif(title: str):
    from PIL import Image

    e = Image.Exif()
    e[0x010E] = f"{title}. Flathead by {AUTHOR}, {URL}"  # ImageDescription
    e[0x013B] = AUTHOR  # Artist
    e[0x8298] = RIGHTS  # Copyright
    return e


def sums() -> str:
    """sha256sum-format manifest of the assets under docs/ (verify: cd docs && shasum -a 256 -c SHA256SUMS)."""
    files = sorted(f for f in DOCS.rglob("*") if f.is_file() and not f.name.startswith(("SHA256SUMS", ".")))
    return "".join(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(DOCS)}\n" for f in files)


if __name__ == "__main__":  # after regenerating assets: manifest, then a detached signature with the commit key
    SUMS.write_text(sums())
    subprocess.run(["gpg", "--armor", "--detach-sign", "--yes", "-o", f"{SUMS}.asc", str(SUMS)], check=True)
    print(f"{SUMS.name}: {len(SUMS.read_text().splitlines())} files, signed")
