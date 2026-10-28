# SPDX-License-Identifier: CERN-OHL-P-2.0
"""Scene props for renders: generic charger brick, AU double outlet, stock-plug brick, cable, wall. Part frame:
plug face at z = t, wall side +z, room side -z, up +y. Generic shapes only: no Apple logo or trade dress.
"""

import math

from build123d import (
    Align,
    Axis,
    Box,
    Circle,
    Cylinder,
    Plane,
    Pos,
    Rectangle,
    Rot,
    Spline,
    extrude,
    fillet,
    sweep,
)
from fit import Brick, Gpo
from interface import MIN3, Params, pin, pin_angles

PLATE_T = 8.0  # outlet plate thickness, ASSUMED
SLOT = (7.0, 2.0)  # socket slot, ASSUMED (clearance over the 6.35 x 1.63 pin)
EARTH_R = 10.31  # earth pin centre from the plug midpoint, AS/NZS 3112 Fig 2.1 dim E [V]


def rounded_box(x: float, y: float, z: float, corner: float, edge: float):
    """Box, vertical corners rounded, then top and bottom edges rounded."""
    b = extrude(fillet(Rectangle(x, y).vertices(), corner), z)
    return fillet(b.edges().group_by(Axis.Z)[0] + b.edges().group_by(Axis.Z)[-1], edge)


def port(p: Params, b: Brick) -> tuple:
    """USB-C port centre: on the brick's side face opposite the notch's outer side, a third of the length in from the
    head's edge, slot long axis across the thickness: as on Apple's bricks, where it faces away from the stock pins."""
    return p.notch_w - b.bw, p.notch_l - b.bl / 3, p.t - b.bt / 2


def brick_body(p: Params, b: Brick):
    """Brick outline before the notch: its outer corner is the notch's outer corner (notch_w, notch_l)."""
    box = rounded_box(b.bw, b.bl, b.bt, p.corner_r, p.edge_r)
    return Pos(p.notch_w - b.bw / 2, p.notch_l - b.bl / 2, p.t - b.bt) * box


def charger(p: Params, b: Brick):
    """Generic brick lying flat behind the part: notch round the part, USB-C port on the far side face."""
    nw, nl = p.notch_w, p.notch_l
    body = brick_body(p, b) - Pos(-1, -1, p.t - b.bt - 1) * Box(nw + 1.2, nl + 1.2, b.bt + 2, align=MIN3)
    px, py, pz = port(p, b)
    return body - Pos(px - 1, py, pz) * Rot(0, 90, 0) * extrude(fillet(Rectangle(8.4, 2.6).vertices(), 1.2), 7)


def cord(at, turn, drop: float) -> dict:
    """USB-C cord plugged into a port at `at`: turn takes the local cord (out along -z, plug wide along x, up +y) to the
    port's facing; the cable curves down drop mm."""
    shell = Pos(0, 0, -22) * rounded_box(12.0, 6.5, 22.0, 2.5, 1.0)
    pts = [(0, 0, -22), (0, -drop * 0.25, -40), (0, -drop, -46)]
    path = Spline(*pts, tangents=((0, 0, -1), (0, -1, 0)))
    lead = sweep(Plane(origin=pts[0], z_dir=(0, 0, -1)) * Circle(2.0), path=path)
    place = Pos(*at) * turn
    return {"shell": place * shell, "lead": place * lead}


def usb_cord(p: Params, b: Brick, gap: float, bend=((40, 0), (75, -30), (85, -80))) -> dict:
    """Loose USB-C cord on the floor by a flat brick (part frame, brick back on the floor): plug gap mm from the port,
    pointing at it; cable along bend (offsets away from the port, across) on the floor."""
    px, py, _ = port(p, b)
    floor, x0 = p.t - b.bt, px - gap
    shell = Pos(x0 - 11, py, floor) * rounded_box(22.0, 12.0, 6.5, 3.0, 1.2)
    tongue = Pos(x0, py, floor + 3.25) * Box(6.6, 8.25, 2.4, align=(Align.MIN, Align.CENTER, Align.CENTER))
    tail = x0 - 22
    pts = [(tail, py, floor + 3.25), (tail - 10, py, floor + 2)] + [(tail - a, py + c, floor + 2) for a, c in bend]
    end = tuple(c - d for c, d in zip(pts[-1], pts[-2], strict=True))  # run out straight, no hook at the end
    path = Spline(*pts, tangents=((-1, 0, 0), end))
    lead = sweep(Plane(origin=pts[0], z_dir=(-1, 0, 0)) * Circle(2.0), path=path)
    return {"shell": shell, "lead": lead, "tongue": tongue}


def outlet(p: Params, g: Gpo, k: int) -> dict:
    """AU double outlet with the part plugged into socket k: plate (slots cut), switches, wall behind."""
    cx = ((g.sockets - 1) / 2 - k) * g.pitch
    plate = Pos(p.pin_cx - cx, p.pin_cy - g.socket_y, p.t) * rounded_box(g.plate_w, g.plate_h, PLATE_T, 4.0, 2.0)
    switches = []
    for j in range(g.sockets):
        sx = p.pin_cx - (j - k) * g.pitch
        plate -= Pos(sx, p.pin_cy, p.t) * Cylinder(17.5, 0.4, align=(Align.CENTER, Align.CENTER, Align.MIN))
        for a in pin_angles(p).values():
            r = math.radians(a)
            px, py = sx + p.pin_r * math.cos(r), p.pin_cy + p.pin_r * math.sin(r)
            plate -= Pos(px, py, p.t) * Rot(0, 0, a + 90) * Box(*SLOT, 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        earth = Box(SLOT[1], SLOT[0], 6, align=(Align.CENTER, Align.CENTER, Align.MIN))
        plate -= Pos(sx, p.pin_cy - EARTH_R, p.t) * earth
        if g.switch:
            rocker = rounded_box(g.switch_w, g.switch_h, 3.0, 1.0, 0.6)
            switches.append(Pos(sx, p.pin_cy + g.switch_dy, p.t - 2.5) * Rot(-6, 0, 0) * rocker)
    wall = Pos(p.pin_cx - cx, p.pin_cy - g.socket_y - 40, p.t + PLATE_T) * Box(
        2400, 1600, 10, align=(Align.CENTER, Align.CENTER, Align.MIN)
    )
    return {"plate": plate, "switches": switches, "wall": wall}


def stock_plug(p: Params, g: Gpo, j: int, k: int, b: Brick) -> dict:
    """Brick on its stock duckhead in socket j (part in socket k): the duckhead fills the same corner notch, pins out of
    its outer side face, so the brick stands off the wall and hangs below the head; port faces the room."""
    dx = -(j - k) * g.pitch
    pins = []
    for a in pin_angles(p).values():
        sleeve, contact = pin(p, a)
        pins += [Pos(dx, 0, 0) * sleeve, Pos(dx, 0, 0) * contact]
    nw, nl = p.notch_w, p.notch_l
    duck = brick_body(p, b) & (Pos(-0.3, -0.3, p.t - b.bt - 1) * Box(nw + 0.3, nl + 0.3, b.bt + 2, align=MIN3))
    # charger frame to wall: the stock pins leave the notch's outer side face (x = notch_w), across the slide, so that
    # face goes on the plate, the brick stands off by its width and hangs below the head
    turn = Rot(0, -90, 0)
    face = (turn * Pos(nw, nl / 2, p.t - b.bt / 2)).position
    move = Pos(p.pin_cx + dx - face.X, p.pin_cy - face.Y, p.t - face.Z)
    at = (move * turn * Pos(*port(p, b))).position
    head, brick = move * turn * duck, move * turn * charger(p, b)
    return {"head": head, "brick": brick, "pins": pins, "standoff": b.bw, "port": tuple(at)}
