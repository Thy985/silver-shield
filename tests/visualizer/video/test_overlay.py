"""ADR-0035 D3 · overlay 单测（评审缺口 #4 · provenance 强制 + 脱敏）。

provenance_text 是角标唯一格式化处（编译器断言与渲染共用）；shorten_label 须净化+脱敏+截断。
"""

from __future__ import annotations

from home_perception.visualizer.video.render.overlay import (
    WATERMARK_TEXT,
    provenance_text,
    shorten_label,
)
from home_perception.visualizer.video.text_safety import REDACTED_PATH, display_width


def test_watermark_constant():
    assert WATERMARK_TEXT == "程序化合成 · 非真实录像"


def test_provenance_text_contains_scenario_id():
    text = provenance_text("sw_x", 0, "fingerprintABCDEF12")
    assert "sw_x" in text
    assert "seed=0" in text
    assert text.startswith("sw_x · seed=0 · fingerprint")


def test_shorten_label_desensitizes_path():
    out = shorten_label({"label": "D:/secret/x.mp4"}, "ref")
    assert REDACTED_PATH in out


def test_shorten_label_falls_back_to_ref_suffix():
    assert shorten_label(None, "scn#0") == "0"


def test_shorten_label_truncates_long_cjk():
    out = shorten_label({"label": "中" * 60}, "r")
    assert out.endswith("…")
    assert display_width(out) <= 28


def test_shorten_label_sanitizes_control_chars():
    out = shorten_label({"label": "正常\n标签\x07x"}, "r")
    assert "\n" not in out
    assert "\x07" not in out
