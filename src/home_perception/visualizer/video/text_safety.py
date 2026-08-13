"""ADR-0035 D3 · 文本安全工具（脱敏 + 控制字符净化 + CJK 宽度截断 · D3-4）。

D3-4 要求「渲染进帧的每一段文本都必须脱敏」。该纪律横跨三条通道：

1. ``storyboard/generator._build_narration``——由证据值填充字幕文案（上游）；
2. ``render/caption``——字幕条绘制（下游通道 A）；
3. ``render/overlay.shorten_label``——节点标签绘制（下游通道 B）。

三处若各自实现，规则必然漂移，D3-4 就只是纸面纪律。故**本模块是唯一脱敏实现处**，
上述三处一律调用本模块：

- ``desensitize``：抹除真实文件路径与长数字串（疑似设备序列号/身份证号）；
- ``sanitize_display_text``：净化换行与 C0/C1 控制字符（防恶意 label 破版/注入渲染指令）；
- ``display_width`` / ``truncate_display``：按「CJK 全角 = 2 单元」的显示宽度截断
  （``len()`` 对中文严重低估宽度，会导致字幕越界）。

本模块**零重依赖**（只用 ``re`` / ``unicodedata``），因此语义层（storyboard）引用它
不会反向拉入 PIL/cv2 渲染栈，层边界保持干净。

见设计文档 §6（D3-4 脱敏 + provenance 强制叠加）、§9 D3-7（中文叠加）、§8 验收 6。
"""

from __future__ import annotations

import re
import unicodedata

# ── 脱敏（D3-4）──
# Windows 盘符路径：``D:\dir\file.mp4`` / ``D:/dir/file.mp4``。
# 负向后顾 ``(?<![\w])`` 关键：否则 ``https://host`` 里的 ``s:/`` 会被当成盘符，
# 整条 URL 被吞成 [REDACTED_PATH]（旧实现的缺陷）。尾段要求非空，避免匹配裸盘符。
_WINDOWS_PATH_RE = re.compile(r"(?<!\w)[A-Za-z]:[\\/](?:[^\\/\s\"']+[\\/])*[^\\/\s\"']+")
# POSIX 绝对路径：至少两段（``/var/log`` 起），且前一个字符不得是 ``\w`` / ``:`` / ``/``——
# 该负向后顾把 URL 排除在外（``https://host/path`` 的三处 ``/`` 依次被 ``:``、``/``、``\w`` 挡住），
# 也把 ``1/2``、``事件/决策`` 这类非路径斜杠排除。
_POSIX_PATH_RE = re.compile(r"(?<![\w:/])/(?:[\w.\-~]+/)+[\w.\-~]*")
# 长数字串（疑似设备序列号 / 身份证号 / 手机号）。
_LONG_DIGITS_RE = re.compile(r"\b\d{8,}\b")

# ── 控制字符净化 ──
# 换行/制表等「排版类」空白 → 折叠为空格（保留可读性）。
_LINEBREAK_RE = re.compile(r"[\t\n\r\f\v\u2028\u2029]+")
# 其余 C0/C1 控制字符 + BiDi 覆盖字符 → 直接删除（防破版 / 视觉欺骗）。
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2066-\u2069]")
_MULTISPACE_RE = re.compile(r" {2,}")

REDACTED_PATH = "[REDACTED_PATH]"
REDACTED_ID = "[REDACTED_ID]"

# 字幕条默认显示宽度预算（单元数；中文 2 / 西文 1）。
DEFAULT_CAPTION_CELLS = 60


def desensitize(text: str) -> str:
    """脱敏：抹除真实文件路径与长数字序列（D3-4 / D7b）。

    只处理「泄漏源」两类模式，**不**动 URL——URL 不是本模块的脱敏目标，
    过宽的路径正则会把 ``https://…`` 一并吞掉（旧实现的缺陷）。
    """
    text = _WINDOWS_PATH_RE.sub(REDACTED_PATH, text)
    text = _POSIX_PATH_RE.sub(REDACTED_PATH, text)
    return _LONG_DIGITS_RE.sub(REDACTED_ID, text)


def sanitize_display_text(text: str) -> str:
    """净化为「可安全绘制的单行文本」：折叠换行、删控制字符、压缩连续空格。

    渲染层拿到的 label 可能来自 artifact（外部数据）。含 ``\\n`` 的 label 会让
    Pillow 多行绘制越出卡片；含 BiDi 覆盖字符可让显示顺序与真实内容不一致
    （视觉欺骗）。此处一律净化（fail-safe：净化而非报错，避免阻塞 pipeline）。
    """
    text = _LINEBREAK_RE.sub(" ", text)
    text = _CONTROL_RE.sub("", text)
    return _MULTISPACE_RE.sub(" ", text).strip()


def display_width(text: str) -> int:
    """显示宽度（单元数）：East Asian Wide/Fullwidth 记 2，组合记号记 0，其余记 1。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_display(text: str, max_cells: int = DEFAULT_CAPTION_CELLS, ellipsis: str = "…") -> str:
    """按显示宽度截断（CJK 全角计 2 单元），超出则以 ``ellipsis`` 结尾。

    旧实现按 ``len(text) > 80`` 截断，对中文实际宽度低估近一倍，1280px 宽字幕条
    仍可能越界。此处以显示单元为准。
    """
    if max_cells <= 0:
        return ""
    if display_width(text) <= max_cells:
        return text
    budget = max_cells - display_width(ellipsis)
    if budget <= 0:
        return ellipsis
    out: list[str] = []
    used = 0
    for ch in text:
        w = 0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ellipsis


def safe_display_text(
    text: str,
    max_cells: int = DEFAULT_CAPTION_CELLS,
) -> str:
    """一次完成「脱敏 → 净化 → 宽度截断」（渲染层统一入口）。"""
    return truncate_display(sanitize_display_text(desensitize(text)), max_cells)


__all__ = [
    "DEFAULT_CAPTION_CELLS",
    "REDACTED_ID",
    "REDACTED_PATH",
    "desensitize",
    "display_width",
    "safe_display_text",
    "sanitize_display_text",
    "truncate_display",
]
