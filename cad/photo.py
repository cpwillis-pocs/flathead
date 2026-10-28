# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Photoreal renders with Mitsuba 3 (path tracing). Usage: uv run --group render python cad/photo.py [shot ...]

Product-photography setup: seamless backdrop (cyclorama), large soft key, fill and rim lights, long lens. CAD is
tessellated to PLY (cad/out/photo, gitignored) and rendered to docs/images/*.jpg. Published JPEGs carry EXIF
attribution and two hidden marks (provenance.py). Uses the Apple GPU variant when present, else CPU. SPP sets
samples per pixel (default 256; 32 for quick previews).
"""

import copy
import itertools
import math
import os
import struct
import sys
from pathlib import Path

import box
import d1
import d2
import interface
import jig
import mitsuba as mi
import mould
import numpy as np
import props
from build123d import Align, Axis, Box, Compound, Pos, Rot, fillet
from fit import BRICKS, GPOS
from interface import OUT, Params
from PIL import Image, ImageDraw, ImageFont
from provenance import MARK_MIN, exif, mark_image, mark_score

for variant in ("metal_ad_rgb", "scalar_rgb"):
    try:
        mi.set_variant(variant)
        break
    except (ImportError, AttributeError):
        continue
T = mi.ScalarTransform4f
IMG = Path(__file__).parents[1] / "docs" / "images"
MESH = OUT / "photo"
SPP = int(os.environ.get("SPP", 256))
WIDTH = 1400  # published width, px


def plastic(rgb, alpha: float) -> dict:
    """Moulded plastic: diffuse base under a GGX clear coat (PC ior 1.58)."""
    return {
        "type": "roughplastic",
        "distribution": "ggx",
        "alpha": alpha,
        "int_ior": 1.58,
        "diffuse_reflectance": {"type": "rgb", "value": rgb},
    }


def metal(rgb, rough: float) -> dict:
    return {"type": "principled", "base_color": {"type": "rgb", "value": rgb}, "roughness": rough, "metallic": 1.0}


def matte(rgb) -> dict:
    return {"type": "diffuse", "reflectance": {"type": "rgb", "value": rgb}}


APPLE_WHITE = (0.86, 0.86, 0.85)  # linear diffuse under the clear coat: reads about sRGB 240 lit
EXPOSE = 0.88  # linear level the 99th-percentile pixel is scaled to, so white plastic reads white in every shot
MAT = {
    "pc": plastic(APPLE_WHITE, 0.10),  # FR PC, matched to the brick
    "charger": plastic(APPLE_WHITE, 0.10),
    "plate": plastic((0.93, 0.93, 0.92), 0.06),
    "nickel": metal((0.78, 0.76, 0.72), 0.16),  # Ni-plated brass pins (BOM FH-0010)
    "brass": metal((0.86, 0.68, 0.40), 0.24),
    "sleeve": plastic((0.10, 0.10, 0.11), 0.30),  # pin insulation, charcoal
    "insul": plastic((0.14, 0.14, 0.15), 0.40),
    "alu": metal((0.88, 0.89, 0.90), 0.38),  # bead-blasted tool aluminium
    "resin": plastic((0.40, 0.42, 0.45), 0.32),  # grey print resin
    "card": matte((0.90, 0.89, 0.87)),
    "ink": matte((0.05, 0.05, 0.06)),
    "rubber": plastic((0.90, 0.90, 0.90), 0.40),
    "sand": matte((0.80, 0.75, 0.68)),  # painted wall, warm; also the studio backdrop
    "stone": plastic((0.86, 0.86, 0.84), 0.28),  # engineered stone benchtop
}
METAL_PARTS = {"pin": "nickel", "sleeve": "sleeve", "link": "brass", "c7": "brass", "insul": "insul"}
METAL_PARTS |= {"lead": "insul", "core": "brass", "arm": "brass", "pad": "brass"}


def ply(shape, path: Path, tol: float = 0.01) -> Path:
    """Binary PLY of the shape's tessellation; vertices per face, so Mitsuba smooths within a face, not across."""
    verts, tris = shape.tessellate(tol, 0.08)
    v = np.array([(q.X, q.Y, q.Z) for q in verts], dtype="<f4")
    path.parent.mkdir(parents=True, exist_ok=True)
    head = (
        f"ply\nformat binary_little_endian 1.0\nelement vertex {len(v)}\nproperty float x\nproperty float y\n"
        f"property float z\nelement face {len(tris)}\nproperty list uchar int vertex_indices\nend_header\n"
    )
    with path.open("wb") as fh:
        fh.write(head.encode())
        fh.write(v.tobytes())
        fh.write(b"".join(struct.pack("<Biii", 3, *t) for t in tris))
    return path


def publish(src: Path, name: str, title: str, width: int = WIDTH, overlay=None, proj=None) -> None:
    """Downsample (also averages residual noise), add the overlay, hidden marks and EXIF; write
    docs/images/<name>.jpg. overlay(draw, size, proj): proj maps a 3D point to published pixels."""
    img = Image.open(src).convert("RGB")
    k = width / img.width
    img = img.resize((width, round(img.height * k)), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(img)
    if overlay:
        overlay(draw, img.size, lambda pt: tuple(k * c for c in proj(pt)))
    dst = IMG / f"{name}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    mark_image(img).save(dst, quality=88, optimize=True, progressive=True, exif=exif(title))
    score = min(mark_score(Image.open(dst)))
    assert score >= MARK_MIN, f"{name}: hidden mark too weak ({score:.2f})"


def projector(eye, target, fov: float, size):
    """World point to render pixels, matching Mitsuba's look_at (camera left = up x dir) and horizontal fov."""
    e, t = np.array(eye, float), np.array(target, float)
    d = (t - e) / np.linalg.norm(t - e)
    left = np.cross((0, 1, 0), d)
    left /= np.linalg.norm(left)
    up = np.cross(d, left)
    f = size[0] / 2 / math.tan(math.radians(fov) / 2)

    def proj(pt):
        v = np.array(tuple(pt), float) - e
        z = v @ d
        return size[0] / 2 - f * (v @ left) / z, size[1] / 2 - f * (v @ up) / z

    return proj


def softbox(origin, target, size, radiance) -> dict:
    return {
        "type": "rectangle",
        "to_world": T().look_at(origin=origin, target=target, up=(0, 1, 0)) @ T().scale(size),
        "emitter": {"type": "area", "radiance": {"type": "rgb", "value": radiance}},
    }


def studio(eye, target, span: float, key: float = 1.0) -> list:
    """Large key above left, fill right, rim behind: all out of frame (key and fill behind the camera)."""
    e, t = np.array(eye, float), np.array(target, float)
    fwd = (t - e) / np.linalg.norm(t - e)
    right = np.cross(fwd, (0, 1, 0))
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    k = e - fwd * span + up * 3.0 * span - right * 2.0 * span
    f = e - fwd * span + right * 3.0 * span + up * 0.8 * span
    rim = t + fwd * 2.5 * span + up * 3.5 * span
    return [
        softbox(tuple(k), target, (2.4 * span, 2.4 * span, 1), tuple(2.2 * key * c for c in (1.0, 0.99, 0.96))),
        softbox(tuple(f), target, (2.0 * span, 2.0 * span, 1), (0.6, 0.62, 0.66)),
        softbox(tuple(rim), target, (1.5 * span, 1.5 * span, 1), (1.0, 1.0, 1.0)),
    ]


def cyc(eye, target, floor_y: float = 0.0, back: float = 260.0):
    """Seamless sweep: floor at floor_y, coved up into a wall `back` mm behind the target, facing the camera."""
    w, h, r = 4000.0, 2000.0, 0.5 * back  # cove must stay behind the target
    floor = Pos(0, 0, back + 1 - w / 2) * Box(w, 2, w, align=(Align.CENTER, Align.MAX, Align.CENTER))
    wall = Pos(0, 0, back) * Box(w, h, 2, align=(Align.CENTER, Align.MIN, Align.MIN))
    sweep = floor + wall
    cove = [e for e in sweep.edges() if abs(e.center().Y) < 1e-6 and abs(e.center().Z - back) < 1e-6]
    sweep = fillet(cove, r)
    d = np.array(target, float) - np.array(eye, float)
    yaw = math.degrees(math.atan2(d[0], d[2]))
    return Pos(target[0], floor_y, target[2]) * Rot(0, yaw, 0) * sweep


def render(name: str, items: list, eye, target, fov: float, size=(2000, 1250), lights=None, env: float = 0.12, **pub):
    """items: (shape, material key). Path traces at size, publishes via publish()."""
    scene = {
        "type": "scene",
        "integrator": {"type": "path", "max_depth": 10},
        "sensor": {
            "type": "perspective",
            "fov": fov,
            "to_world": T().look_at(origin=eye, target=target, up=(0, 1, 0)),
            "film": {"type": "hdrfilm", "width": size[0], "height": size[1], "rfilter": {"type": "gaussian"}},
            "sampler": {"type": "independent", "sample_count": SPP},
        },
        "env": {"type": "constant", "radiance": {"type": "rgb", "value": (env, env, env * 1.02)}},
    }
    for i, (shape, mat) in enumerate(items):
        scene[f"s{i}"] = {"type": "ply", "filename": str(ply(shape, MESH / name / f"{i}.ply")), "bsdf": MAT[mat]}
    for i, light in enumerate(lights or []):
        scene[f"l{i}"] = light
    img = np.array(mi.render(mi.load_dict(scene)))
    img = mi.Bitmap(img * EXPOSE / max(np.percentile(img.max(axis=2), 99), 1e-6))  # same exposure every shot
    full = MESH / f"{name}.png"
    mi.util.write_bitmap(str(full), img, write_async=False)  # full-size sRGB master, gitignored
    publish(full, name, proj=projector(eye, target, fov, size), **pub)
    print(name)


def shoot(name: str, items: list, view, fov: float = 24, size=(2000, 1250), fill: float = 0.92, **kw):
    """Studio shot on the cyclorama, auto-framed: camera looks along -view at the group's bounding-box centre, just far
    enough that every box corner falls inside fill of the frame."""
    bb = Compound(children=[s for s, _ in items]).bounding_box()
    c = np.array([bb.center().X, bb.center().Y, bb.center().Z])
    back = np.array(view, float) / np.linalg.norm(view)  # target to camera
    right = np.cross((0, 1, 0), back)
    right /= np.linalg.norm(right)
    up = np.cross(back, right)
    th = math.tan(math.radians(fov) / 2) * fill
    tv = th * size[1] / size[0]
    corners = [np.array(v) - c for v in itertools.product(*zip(tuple(bb.min), tuple(bb.max), strict=True))]
    dist = max(max(abs(k @ right) / th, abs(k @ up) / tv) + k @ back for k in corners)
    eye, target = tuple(c + back * dist), tuple(c)
    span = bb.diagonal
    items = items + [(cyc(eye, target, 0.0, max(span * 1.5, 250.0)), "sand")]
    render(name, items, eye, target, fov, size, lights=studio(eye, target, span), **kw)


UPRIGHT = Rot(-90, 0, 0)  # part +z (pull axis, pins) to world +y, for table-top shots
FACING = Rot(0, 180, 0) * UPRIGHT  # pins up, C7 end toward the camera: the one orientation for part close-ups


def kind(label: str) -> str:
    return label.split("_")[0]


def parts(part, mats: dict) -> list:
    """(child, material) for the children whose label prefix is in mats."""
    return [
        (copy.copy(c), mats[kind(c.label)]) for c in part.children if kind(c.label) in mats
    ]  # copies: see renders.by


def floor_move(items: list, x: float = 0.0, z: float = 0.0, y: float = 0.0):
    """Translation resting a group's lowest point at height y (default the floor) with its centre at (x, z)."""
    bb = Compound(children=[copy.copy(s) for s, _ in items]).bounding_box()
    return Pos(x - bb.center().X, y - bb.min.Y, z - bb.center().Z)


def on_floor(items: list, x: float = 0.0, z: float = 0.0, y: float = 0.0) -> list:
    move = floor_move(items, x, z, y)
    return [(move * s, m) for s, m in items]


D1_MATS = {"housing": "pc", **METAL_PARTS}
D2_MATS = {"housing": "pc", "carrier": "pc", **METAL_PARTS}
SHELLS = {"housing": "pc", "carrier": "pc"}  # outside view: shells and carriers only, the lead frame is hidden


def on_brick(p, part, mats, b) -> list:
    """Part clicked into a generic brick, part frame."""
    return [(props.charger(p, b), "charger"), *parts(part, mats)]


def font(size: int):
    return ImageFont.load_default(size)


def kitchen(gap: float):
    """Sand wall and a stone benchtop under two outlets gap apart, as over a kitchen bench; part frame of the right
    outlet. Returns (items, bench top y)."""
    p, g = Params(), GPOS[1]
    o = props.outlet(p, g, 1)
    c = o["plate"].bounding_box()
    bench_y = c.min.Y - 110.0
    wall_z = p.t + props.PLATE_T
    wall = Pos(c.center().X, bench_y - 40, wall_z) * Box(3000, 1800, 10, align=(Align.CENTER, Align.MIN, Align.MIN))
    bench = Pos(c.center().X, bench_y, wall_z) * Box(3000, 36, 640, align=(Align.CENTER, Align.MAX, Align.MAX))
    bench = fillet(bench.edges().filter_by(Axis.X).group_by(Axis.Z)[0].group_by(Axis.Y)[-1], 3.0)  # eased front
    items = [(wall, "sand"), (bench, "stone")]
    for mv in (Pos(0, 0, 0), Pos(gap, 0, 0)):
        items += [(mv * o["plate"], "plate"), *[(mv * sw, "plate") for sw in o["switches"]]]
    return items, bench_y


def pair(p, b, g, gap: float, bench_y: float, cables: bool = True) -> tuple:
    """Ordinary plug and brick in the left outlet, Flathead on the same brick in the right: (items, stock bbox,
    Flathead brick bbox, stock standoff from the outlet face)."""
    left = Pos(gap, 0, 0)  # room left is +x
    stock = props.stock_plug(p, g, 1, 1, b)
    sb = (left * stock["brick"]).bounding_box()
    items = [(left * stock["head"], "charger"), (left * stock["brick"], "charger")]
    items += on_brick(p, d1.build(p), SHELLS, b)
    fb = props.charger(p, b).bounding_box()
    if cables:
        at = (left * Pos(*stock["port"])).position  # stock brick: port faces the room
        flat = props.port(p, b)  # Flathead: port on the room-right side face
        cords = props.cord(tuple(at), Rot(0, 0, 0), at.Y - bench_y), props.cord(flat, Rot(0, 90, 0), flat[1] - bench_y)
        for c in cords:
            items += [(c["shell"], "charger"), (c["lead"], "rubber")]
    return items, sb, fb, stock["standoff"]


INK = (38, 40, 44)  # overlay text, charcoal on the light scenes


def hero():
    """Marketing hero: an ordinary plug holds its brick off the wall; Flathead lets the same brick lie flat. D1 and
    D2 look the same plugged in, so one Flathead stands for both."""
    p, b, g, gap = Params(), BRICKS[0], GPOS[1], 260.0
    items, bench_y = kitchen(gap)
    units, sb, fb, _ = pair(p, b, g, gap, bench_y)
    items += units
    sx, _, sz = props.stock_plug(p, g, 1, 1, b)["port"]
    fx, _, fz = props.port(p, b)
    oc = props.outlet(p, g, 1)["plate"].bounding_box().center()
    target = (gap / 2 - 10, oc.Y - 20, p.t - 70)
    eye = (target[0] + 520, target[1] + 160, target[2] - 1250)
    lights = [
        softbox((target[0] + 1300, target[1] + 900, target[2] - 300), target, (1100, 1100, 1), (3.0, 2.95, 2.8)),
        softbox((target[0] - 900, target[1] + 300, target[2] - 900), target, (800, 800, 1), (0.7, 0.72, 0.78)),
    ]

    def overlay(draw, size, proj):
        w, h = size
        draw.text((0.06 * w, 0.09 * h), "Flathead", fill=INK, font=font(w // 18))
        draw.text((0.06 * w, 0.20 * h), "Side-Facing AU Plug for Apple Chargers", fill=(84, 86, 92), font=font(w // 48))
        for pt, text in (  # centred under each cable's foot on the bench, clear of cable and units
            ((gap + sx, bench_y, sz - 46), "Ordinary Plug"),
            ((fx - 46, bench_y, fz), "Flathead"),
        ):
            x, y = proj(pt)
            draw.text((x, y + 0.035 * h), text, fill=INK, font=font(w // 60), anchor="mt")

    title = "Ordinary Plug vs Flathead"
    render("hero", items, eye, target, 34, (2400, 1250), lights, 0.22, title=title, width=1800, overlay=overlay)


def dimension(draw, proj, a, b, off, text: str, w: int, label: int = 1, below: bool = False):
    """Technical dimension a to b (3D, along the depth), offset sideways by off (3D vector), with extension lines,
    arrowheads and a label beside the line (label +1: to its right as seen, -1: to its left); below: under b's end."""
    ink, lw = (52, 54, 60), max(2, w // 900)
    a2, b2 = tuple(np.add(a, off)), tuple(np.add(b, off))
    for p0, p1 in ((a, a2), (b, b2)):
        draw.line((*proj(p0), *proj(tuple(np.add(p1, np.multiply(off, 0.25))))), fill=ink, width=lw)
    (xa, ya), (xb, yb) = proj(a2), proj(b2)
    draw.line((xa, ya, xb, yb), fill=ink, width=lw)
    ux, uy = (xb - xa), (yb - ya)
    n = math.hypot(ux, uy)
    ux, uy, k = ux / n, uy / n, w / 110
    for (x, y), sgn in (((xa, ya), 1), ((xb, yb), -1)):
        tip = (x, y)
        base = (x + sgn * ux * k, y + sgn * uy * k)
        draw.polygon(
            [tip, (base[0] - uy * k * 0.3, base[1] + ux * k * 0.3), (base[0] + uy * k * 0.3, base[1] - ux * k * 0.3)],
            fill=ink,
        )
    mx, my = (xb, yb + k * 0.9) if below else ((xa + xb) / 2, (ya + yb) / 2)
    anchor = ("l" if label > 0 else "r") + ("t" if below else "m")
    draw.text((mx + label * k * 0.8, my), text, fill=INK, font=font(w // 55), anchor=anchor)


def slim():
    """Plan view from above: how far each brick stands out from the outlet, dimensioned like a drawing."""
    p, b, g, gap = Params(), BRICKS[0], GPOS[1], 260.0
    items, bench_y = kitchen(gap)
    units, sb, fb, standoff = pair(p, b, g, gap, bench_y, cables=False)
    items += units
    target = (gap / 2 + 15, sb.max.Y - 10, p.t - 55)
    eye = (target[0], target[1] + 1150, target[2] - 560)  # above, tilted toward the wall
    lights = [softbox((target[0] + 400, target[1] + 1200, target[2] - 600), target, (900, 900, 1), (3.0, 2.95, 2.8))]

    def overlay(draw, size, proj):  # both dimensions in the gap between the units, labels in the empty wall
        w, h = size
        draw.text((0.06 * w, 0.07 * h), "Out from the Outlet", fill=INK, font=font(w // 28))
        stock = ((sb.min.X, sb.max.Y + 1, p.t), (sb.min.X, sb.max.Y + 1, p.t - standoff))  # each on its own top
        dimension(draw, proj, *stock, (-16, 0, 0), f"Ordinary Plug {standoff:.0f} mm", w, 1)
        flat = ((fb.max.X, fb.max.Y + 1, p.t), (fb.max.X, fb.max.Y + 1, p.t - b.bt))
        dimension(draw, proj, *flat, (16, 0, 0), f"Flathead {b.bt:g} mm", w, -1, below=True)  # clear of the plate

    render(
        "slim",
        items,
        eye,
        target,
        22,
        (2400, 1250),
        lights,
        0.22,
        title="Depth from the Outlet",
        width=1800,
        overlay=overlay,
    )


def callouts(points: list):
    """Overlay: leader lines from 3D points to labels at image fractions."""

    def overlay(draw, size, proj):
        w, h = size
        for pt, (fx, fy), text in points:
            x, y = proj(pt)
            lx, ly = fx * w, fy * h
            draw.line((x, y, lx, ly), fill=(110, 112, 116), width=max(2, w // 700))
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(110, 112, 116))
            draw.text(
                (lx + (8 if fx > 0.5 else -8), ly),
                text,
                fill=(52, 54, 58),
                font=font(w // 55),
                anchor="lm" if fx > 0.5 else "rm",
            )

    return overlay


def anatomy():
    """Annotated D1: pins, shell, C7 connector, stud slot."""
    p = Params()
    turn = Rot(0, 150, 0) * UPRIGHT
    raw = [(turn * c, m) for c, m in parts(d1.build(p), D1_MATS)]
    move = floor_move(raw)
    items = [(move * s, m) for s, m in raw]
    a = interface.pin_angles(p)["A"]
    tip = (
        p.pin_cx + p.pin_r * math.cos(math.radians(a)),
        p.pin_cy + p.pin_r * math.sin(math.radians(a)),
        p.t + p.pin_len,
    )
    sleeve = (tip[0], tip[1], p.t + 4)
    c7 = (p.c7_x, -p.c7_depth, p.c7_z)
    slot = (0.0, 6.0, p.c7_z)
    shell = (p.w * 0.8, p.l * 0.5, 2.0)
    to = lambda q: (move * turn * Pos(*q)).position  # noqa: E731
    pts = [
        (to(tip), (0.70, 0.18), "AS/NZS 3112 Pins"),
        (to(sleeve), (0.70, 0.34), "Insulated Sleeves"),
        (to(slot), (0.28, 0.48), "Stud Slot: Slides onto the Brick"),
        (to(c7), (0.28, 0.70), "C7 Connector to the Brick"),
        (to(shell), (0.70, 0.80), "Flame-Retardant PC Shell"),
    ]
    shoot(
        "anatomy",
        items,
        (-0.35, 0.55, -0.8),
        size=(2000, 1300),
        fill=0.8,
        title="Flathead Anatomy",
        overlay=callouts(pts),
    )


CORD = {"shell": "charger", "lead": "rubber", "tongue": "nickel"}


def product(name: str, p, head, mats, title: str, view):
    """The three pieces apart: brick (empty notch, port face to the camera), its USB-C cord in front of the port, and
    the Flathead showing its stud slot and C7 boss."""
    b, turn = BRICKS[0], Rot(0, -62, 0) * UPRIGHT
    brick = [(turn * props.charger(p, b), "charger")]
    brick += [(turn * s, CORD[k]) for k, s in props.usb_cord(p, b, 35.0).items()]
    loose = on_floor([(Rot(0, 145, 0) * UPRIGHT * s, m) for s, m in parts(head, mats)], -95, 10)
    shoot(name, on_floor(brick, 0, 0) + loose, view, title=title)


def d1_product():
    p = Params()
    product("d1_product", p, d1.build(p), D1_MATS, "Flathead D1, Fixed Pins", (-0.4, 0.55, -0.75))


def d2_product():
    """D2 with its pins folded flat."""
    p = d2.D2Params()
    product("d2_product", p, d2.build(p, 180), D2_MATS, "Flathead D2, Folding Pins", (-0.4, 0.62, -0.7))


def d1_head():
    """D1 on its own, as the lead frames and fold sequence face."""
    shoot(
        "d1_head",
        on_floor([(FACING * c, m) for c, m in parts(d1.build(), D1_MATS)]),
        (0, 0.8, -0.6),
        size=(2400, 1000),
        title="Flathead D1 Head",
    )


def on_brick_shot(name: str, units: list, title: str):
    """Heads clicked onto bricks, left to right: the package as it plugs in."""
    b, items, x = BRICKS[0], [], 0.0
    for p, part, mats in units:
        unit = [(UPRIGHT * s, m) for s, m in on_brick(p, part, mats, b)]
        bb = Compound(children=[s for s, _ in unit]).bounding_box()
        items += on_floor(unit, x - bb.size.X / 2, 0)
        x -= bb.size.X + 30  # camera right is world -x
    shoot(name, items, (-0.4, 0.55, -0.75), title=title)


def d1_on_brick():
    p = Params()
    on_brick_shot("d1_on_brick", [(p, d1.build(p), D1_MATS)], "Flathead D1 on a Brick")


def d2_on_brick():
    """Pins folded, then open."""
    p = d2.D2Params()
    units = [(p, d2.build(p, deg), D2_MATS) for deg in (180, 0)]
    on_brick_shot("d2_on_brick", units, "Flathead D2 on a Brick, Folded and Open")


def d2_fold():
    """Closed, active pin open, both open, left to right: unfolding in order."""
    p = d2.D2Params()
    items, x = [], 0.0
    for deg in (180, 90, 0):
        unit = [(FACING * c, m) for c, m in parts(d2.build(p, deg), D2_MATS)]
        bb = Compound(children=[s for s, _ in unit]).bounding_box()
        items += on_floor(unit, x - bb.size.X / 2, 0)
        x -= bb.size.X + 22  # camera right is world -x
    shoot("d2_fold", items, (0, 0.8, -0.6), size=(2400, 1000), title="Flathead D2 Fold Sequence")


def exploded(name: str, part, mats, lift: dict, title: str):
    items = [(FACING * Pos(0, 0, lift.get(kind(c.label), lift.get(c.label, 0))) * c, m) for c, m in parts(part, mats)]
    shoot(name, on_floor(items), (-0.45, 0.35, -0.8), size=(1500, 1800), title=title)


def d1_exploded():
    exploded(
        "d1_exploded",
        d1.build(),
        D1_MATS,
        {"housing_top": 55, "housing_bottom": 0, "pin": 13, "sleeve": 13, "link": 13, "c7": 13, "insul": 13},
        "Flathead D1 Exploded",
    )


def d2_exploded():
    lift = {"housing_top": 32, "housing_bottom": 0, "pin": 48, "sleeve": 48, "carrier": 48, "arm": 48}
    lift |= dict.fromkeys(("link", "c7", "insul", "lead", "core", "pad"), 13)  # about 10 mm between layers
    exploded("d2_exploded", d2.build(d2.D2Params(), 0), D2_MATS, lift, "Flathead D2 Exploded")


def lead_frame(name: str, part, title: str):
    """Live parts with the housings removed, turned so the links face the camera."""
    metal = METAL_PARTS | {"carrier": "pc"}
    shoot(
        name,
        on_floor([(FACING * c, m) for c, m in parts(part, metal)]),
        (-0.15, 0.4, -0.9),
        size=(1600, 1250),
        title=title,
    )


def d1_lead():
    lead_frame("d1_lead", d1.build(), "D1 Lead Frame: Pins, Links in Insulating Tube, C7 Contacts")


def d2_lead():
    lead_frame("d2_lead", d2.build(d2.D2Params(), 0), "D2 Lead Frame: Pins in Hinged Carriers, Flexible Leads, Links")


def tool(name: str, design: str, title: str):
    """Open two-plate tool for a top shell, the halves side by side with their parting faces up: cavity (forms the pin
    face, with the insert pockets the pins stand in) and core (the shadow-method core that forms the inside)."""
    m = mould.build(designs=(design,))[f"{design}_top"]
    cavity = on_floor([(Rot(0, 0, 180) * FACING * m["cavity"], "alu")])
    cb = cavity[0][0].bounding_box()
    core = on_floor([(FACING * m["core"], "alu")], cb.min.X - 45, 0)  # camera right is world -x
    shoot(name, cavity + core, (0, 1.0, -0.45), size=(2000, 1150), title=title)


def d1_mould():
    tool("d1_mould", "d1", "D1 Top Shell Tool")


def d2_mould():
    tool("d2_mould", "d2", "D2 Top Shell Tool")


def assembly():
    """Weld nest (printed) holding the bottom shell, top shell lifted above it, ready for the horn."""
    p = Params()
    top, bottom = d1.housing(p)
    items = [(UPRIGHT * jig.nest(p), "resin"), (UPRIGHT * bottom, "pc"), (UPRIGHT * Pos(0, 0, 22) * top, "pc")]
    items += [(UPRIGHT * Pos(0, 0, 22) * c, m) for c, m in parts(d1.build(p), METAL_PARTS)]
    items = [(Rot(0, 180, 0) * s, m) for s, m in items]  # nest mark reads from the camera
    shoot("assembly", on_floor(items), (-0.45, 0.5, -0.8), title="D1 Weld Nest")


def carton(p, open_deg: float, x: float, z: float, plug: bool) -> list:
    c = box.carton(p, open_deg)
    items = [(c["tray"], "card"), (c["lid"], "card"), *[(i, "ink") for i in c["ink"]]]
    if plug:
        items += [(c["plug"] * s, m) for s, m in parts(d1.build(p), SHELLS | {"pin": "nickel", "sleeve": "sleeve"})]
        items.append((c["insert"], "card"))
    return on_floor([(Rot(0, 180, 0) * UPRIGHT * s, m) for s, m in items], x, z)


def packaging():
    """Boxed unit: one carton closed, one open with the plug seated in its card insert."""
    p = Params()
    items = carton(p, 0, 30, 25, False) + carton(p, 115, -30, -10, True)
    shoot("packaging", items, (-0.25, 0.55, -0.8), title="Flathead Boxed Unit")


SHOTS = {
    "hero": hero,
    "slim": slim,
    "anatomy": anatomy,
    "d1_product": d1_product,
    "d2_product": d2_product,
    "d1_head": d1_head,
    "d2_fold": d2_fold,
    "d1_on_brick": d1_on_brick,
    "d2_on_brick": d2_on_brick,
    "d1_exploded": d1_exploded,
    "d2_exploded": d2_exploded,
    "d1_lead": d1_lead,
    "d2_lead": d2_lead,
    "d1_mould": d1_mould,
    "d2_mould": d2_mould,
    "assembly": assembly,
    "packaging": packaging,
}

if __name__ == "__main__":
    for shot in sys.argv[1:] or SHOTS:
        SHOTS[shot]()
