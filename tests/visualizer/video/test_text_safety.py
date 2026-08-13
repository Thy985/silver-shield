"""ADR-0035 D3 · text_safety 单测（评审缺口 #3 · D3-4 脱敏唯一实现处）。

覆盖 desensitize（路径+长数字，URL 与 1/2 不被吞）/ sanitize_display_text（控制字符净化）
/ display_width（CJK=2）/ truncate_display（按显示宽度截断）/ safe_display_text（串联）。
"""

from __future__ import annotations

from home_perception.visualizer.video.text_safety import (
    DEFAULT_CAPTION_CELLS,
    REDACTED_ID,
    REDACTED_PATH,
    desensitize,
    display_width,
    safe_display_text,
    sanitize_display_text,
    truncate_display,
)


def test_desensitize_windows_path():
    assert REDACTED_PATH in desensitize("D:/dir/clip.mp4")


def test_desensitize_posix_path():
    assert REDACTED_PATH in desensitize("/var/log/syslog")


def test_desensitize_url_preserved():
    # 负向后顾关键：URL 不得被当成 Windows 盘符吞掉（旧实现缺陷）。
    assert desensitize("https://example.com/a/b") == "https://example.com/a/b"
    assert desensitize("http://127.0.0.1:8080/x") == "http://127.0.0.1:8080/x"


def test_desensitize_slash_not_path():
    assert desensitize("1/2") == "1/2"
    assert desensitize("事件/决策/动作") == "事件/决策/动作"


def test_desensitize_long_digits():
    assert REDACTED_ID in desensitize("123456789012")
    # 短数字串不脱敏
    assert desensitize("1234") == "1234"


def test_sanitize_newline_and_control():
    out = sanitize_display_text("恶意\nlabel\u202ereversed\x07bell   多空格 ")
    assert out == "恶意 labelreversedbell 多空格"


def test_display_width_cjk():
    assert display_width("中文abc") == 7
    assert display_width("abcdef") == 6


def test_truncate_display_cjk():
    # 每「中文」=4 单元；max 20 → 预算 19 → 4 对(16)+省略号
    assert truncate_display("中文" * 40, 20) == "中文中文中文中文中…"


def test_truncate_display_short_kept():
    assert truncate_display("short", 20) == "short"


def test_truncate_display_zero_max():
    assert truncate_display("中文", 0) == ""


def test_safe_display_text_chain():
    out = safe_display_text("D:/a.mp4 123456789012", DEFAULT_CAPTION_CELLS)
    assert REDACTED_PATH in out
    assert REDACTED_ID in out
