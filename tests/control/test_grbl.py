"""Unit tests for the GRBL protocol parser."""
from __future__ import annotations

from cnc_inlay.control.grbl import (
    MachineState,
    Position,
    ResponseKind,
    alarm_text,
    cmd_goto_zero_xy,
    cmd_jog,
    cmd_set_work_zero_all,
    cmd_set_work_zero_xy,
    cmd_set_work_zero_z,
    error_text,
    parse_response,
    parse_status,
)


# ---------------- parse_status ----------------


def test_parse_status_idle_basic():
    s = parse_status("<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    assert s is not None
    assert s.state == MachineState.IDLE
    assert s.mpos == Position(0, 0, 0)
    assert s.feed == 0
    assert s.spindle == 0


def test_parse_status_run_with_wco_and_speeds():
    s = parse_status(
        "<Run|MPos:10.500,5.250,-1.000|FS:600,10000|WCO:5.000,5.000,0.000>"
    )
    assert s is not None
    assert s.state == MachineState.RUN
    assert s.mpos == Position(10.5, 5.25, -1.0)
    assert s.wco == Position(5.0, 5.0, 0.0)
    eff = s.effective_wpos
    assert eff is not None
    assert eff == Position(5.5, 0.25, -1.0)
    assert s.feed == 600
    assert s.spindle == 10000


def test_parse_status_wpos_direct_takes_precedence():
    s = parse_status("<Idle|WPos:1.000,2.000,3.000|FS:0,0>")
    assert s is not None
    assert s.effective_wpos == Position(1, 2, 3)


def test_parse_status_hold_substate():
    s = parse_status("<Hold:0|MPos:1.0,2.0,3.0|FS:0,0>")
    assert s is not None
    assert s.state == MachineState.HOLD


def test_parse_status_jog():
    s = parse_status("<Jog|MPos:1.234,2.345,3.456|FS:1000,0>")
    assert s is not None
    assert s.state == MachineState.JOG
    assert s.mpos is not None
    assert abs(s.mpos.x - 1.234) < 1e-6


def test_parse_status_alarm():
    s = parse_status("<Alarm|MPos:0,0,0|FS:0,0>")
    assert s is not None
    assert s.state == MachineState.ALARM


def test_parse_status_ignores_non_status_lines():
    assert parse_status("ok") is None
    assert parse_status("error:5") is None
    assert parse_status("") is None
    assert parse_status("<unterminated") is None


def test_parse_status_extended_fields_dont_break_parser():
    s = parse_status(
        "<Run|MPos:10.0,20.0,-3.0|FS:600,12000|Bf:15,128|Ln:42|Ov:100,100,100|A:M>"
    )
    assert s is not None
    assert s.state == MachineState.RUN
    assert s.feed == 600


# ---------------- parse_response ----------------


def test_parse_response_ok():
    r = parse_response("ok")
    assert r.kind == ResponseKind.OK


def test_parse_response_ok_with_whitespace():
    r = parse_response("  ok\r\n")
    assert r.kind == ResponseKind.OK


def test_parse_response_error_known_code():
    r = parse_response("error:1")
    assert r.kind == ResponseKind.ERROR
    assert r.code == 1
    assert "expected" in error_text(1).lower()


def test_parse_response_alarm():
    r = parse_response("ALARM:2")
    assert r.kind == ResponseKind.ALARM
    assert r.code == 2
    assert "soft limit" in alarm_text(2).lower()


def test_parse_response_welcome():
    r = parse_response("Grbl 1.1h ['$' for help]")
    assert r.kind == ResponseKind.WELCOME


def test_parse_response_message_block():
    r = parse_response("[MSG:Reset to continue]")
    assert r.kind == ResponseKind.MESSAGE


def test_parse_response_setting_dollar():
    r = parse_response("$10=2")
    assert r.kind == ResponseKind.SETTING


def test_parse_response_other_falls_through():
    r = parse_response("some line we don't recognise")
    assert r.kind == ResponseKind.OTHER


# ---------------- command builders ----------------


def test_cmd_set_work_zero_all():
    assert cmd_set_work_zero_all() == "G10 L20 P1 X0 Y0 Z0"


def test_cmd_set_work_zero_xy():
    assert cmd_set_work_zero_xy() == "G10 L20 P1 X0 Y0"


def test_cmd_set_work_zero_z():
    assert cmd_set_work_zero_z() == "G10 L20 P1 Z0"


def test_cmd_goto_zero_xy():
    assert cmd_goto_zero_xy() == "G90 G0 X0 Y0"


def test_cmd_jog_positive():
    assert cmd_jog("X", 10.0, 1000) == "$J=G91 G21 X10.000 F1000"


def test_cmd_jog_negative_z():
    assert cmd_jog("Z", -1.5, 200) == "$J=G91 G21 Z-1.500 F200"


def test_cmd_jog_handles_float_feed():
    assert cmd_jog("Y", 5.0, 250.5) == "$J=G91 G21 Y5.000 F250.5"
