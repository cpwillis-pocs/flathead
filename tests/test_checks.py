# SPDX-License-Identifier: Apache-2.0
"""Every CAD check as a test, plus negative cases for the key checks. Full solid modelling: minutes."""

from dataclasses import replace

import box
import d1
import d2
import dfm
import jig
import mould
import provenance
import pytest
from build123d import Box, Pos, Vector
from electrical import through_solid
from fit import check_fit
from interface import Params


def test_d1_checks():
    d1.check()


def test_d2_checks():
    d2.check()


@pytest.mark.parametrize("mod", [d1, d2], ids=["d1", "d2"])
def test_shells_mouldable(mod):
    part = mod.build()
    inserts = [c for c in part.children if c.label.split("_")[0] in mod.INSERTS]
    for shell in (c for c in part.children if c.label.startswith("housing")):
        r = dfm.analyse(shell, inserts if shell.label == "housing_top" else [])
        assert not r["none"] and not r["undercuts"], shell.label


def test_moulds():
    for name, m in mould.build().items():
        mould.check(m, name)


def test_weld_nest():
    jig.check()


def test_box_fits_a4():
    box.check()


def test_fit_rejects_pins_too_low():
    with pytest.raises(AssertionError, match="REQ-FIT-002"):
        check_fit(replace(Params(), pin_cy=9.0))  # brick top 19.3 above the pins: covers the switch


def test_clearance_rejects_short_contact():
    with pytest.raises(AssertionError, match="REQ-ELE-007"):
        d1.check(replace(Params(), c7_contact_back=3.0))


def test_through_solid_needs_unbroken_plastic():
    block = Box(10, 10, 10)  # x -5..5
    assert abs(through_solid(block, Vector(0, 0, 0), Vector(5, 0, 0)) - 5.0) < 1e-9  # embedded: all plastic
    gap = block - Pos(2, 0, 0) * Box(1, 4, 4)  # air pocket on the path
    assert through_solid(gap, Vector(0, 0, 0), Vector(5, 0, 0)) is None


def test_svgs_carry_attribution():
    svgs = sorted(provenance.DOCS.rglob("*.svg"))
    assert svgs and [s.name for s in svgs if not provenance.svg_marked(s)] == []


def test_jpegs_carry_attribution():
    from PIL import Image

    for path in sorted((provenance.DOCS / "images").glob("*.jpg")):
        img = Image.open(path)
        assert img.getexif().get(0x013B) == provenance.AUTHOR, f"{path.name}: EXIF artist"
        assert min(provenance.mark_score(img)) >= provenance.MARK_MIN, f"{path.name}: hidden mark"


def test_exports_carry_attribution(tmp_path):
    provenance.export_stl(Box(1, 1, 1), tmp_path / "x.stl")
    provenance.export_step(Box(1, 1, 1), tmp_path / "x.step")
    assert provenance.AUTHOR.encode() in (tmp_path / "x.stl").read_bytes()[:80]
    assert f"('{provenance.AUTHOR}')" in (tmp_path / "x.step").read_text()[:2000]


def test_docs_match_signed_manifest():
    assert provenance.SUMS.read_text() == provenance.sums(), "assets changed: uv run python cad/provenance.py"


def test_contact_break_rejects_short_arm():
    with pytest.raises(AssertionError, match="REQ-FLD-004"):
        d2.contact_break(replace(d2.D2Params(), arm_len=1.4))  # folded, the arm clears its pad by only ~1 mm
