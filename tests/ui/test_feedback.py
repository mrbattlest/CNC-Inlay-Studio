"""Smoke tests for the area-feedback snippet builder."""
from __future__ import annotations

from shapely.geometry import box as shapely_box

from cnc_inlay.ui.feedback import FeedbackContext, build_feedback_snippet


def _ctx(**over) -> FeedbackContext:
    defaults = dict(
        project_path=None,
        app_version="9.9.9",
        tab_name="Pocket",
        view_mode="2D",
        design_w_mm=100.0,
        design_h_mm=50.0,
        design_rotation_quarters=0,
        pocket_depth_mm=3.0,
        glue_gap_mm=0.3,
        xy_tolerance_mm=0.0,
        pass_depth_mm=0.6,
        skip_pocket_vbit=True,
        pocket_material="Maple",
        plug_material="Walnut",
        endmill_name="1.0 mm endmill",
        vbit_name=None,
        pocket_roughing_bits=[],
        plug_roughing_bits=[],
    )
    defaults.update(over)
    return FeedbackContext(**defaults)


def test_snippet_includes_static_context_and_rect():
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(10.0, 5.0, 20.0, 15.0),
    )
    assert "## CNC Inlay Studio — area feedback" in snip
    assert "App version: 9.9.9" in snip
    assert "Tab / view:  Pocket  (2D)" in snip
    assert "x=[10.00, 20.00]" in snip
    assert "y=[5.00, 15.00]" in snip
    assert "skip_pocket_vbit: True" in snip


def test_snippet_includes_comment_when_provided():
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(0.0, 0.0, 10.0, 10.0),
        comment="The right ear is unmilled.",
    )
    assert "### Observation" in snip
    assert "right ear is unmilled" in snip


def test_snippet_omits_observation_section_when_comment_empty():
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(0.0, 0.0, 10.0, 10.0), comment="   ",
    )
    assert "### Observation" not in snip


def test_snippet_reports_local_feature_width_when_design_provided():
    # 20mm × 20mm square design; selection centred on it.
    design = shapely_box(-10.0, -10.0, 10.0, 10.0)
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(-5.0, -5.0, 5.0, 5.0), design_geom=design,
    )
    assert "Design half-width inside selection" in snip


def test_snippet_summarises_bit_chains_in_rect():
    # Two bits: one chain that runs through the selection, one outside.
    bit_chains = [
        ("dummy_bit_obj_in", [[(2.0, 2.0, -1.0), (3.0, 3.0, -1.0)]], "Roughing", 0.0),
        ("dummy_bit_obj_out", [[(50.0, 50.0, -1.0), (60.0, 60.0, -1.0)]], "Finishing", 0.0),
    ]
    # Pass dict form (matches deserialised cached_result).
    dict_chains = [
        {"bit_name": "in-rect", "chains": [[(2.0, 2.0, -1.0), (3.0, 3.0, -1.0)]]},
        {"bit_name": "out-of-rect", "chains": [[(50.0, 50.0, -1.0)]]},
    ]
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(0.0, 0.0, 10.0, 10.0),
        side_bit_chains=dict_chains,
    )
    assert "Toolpaths touching this area" in snip
    assert "in-rect" in snip
    assert "out-of-rect" not in snip


def test_snippet_reports_uncut_region_when_no_chains_touch_rect():
    snip = build_feedback_snippet(
        _ctx(), rect_mm=(0.0, 0.0, 10.0, 10.0),
        side_bit_chains=[{"bit_name": "x", "chains": [[(50.0, 50.0, -1.0)]]}],
    )
    assert "No toolpath chains touch this area" in snip
