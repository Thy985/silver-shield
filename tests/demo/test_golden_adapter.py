"""Unit tests for golden_adapter (Phase 1 of M1).

验证：
1. 4 case 都能 load_golden_scenario 成功
2. 资源路径解析（通用规则，不写特例）
3. start_time 解析（容忍 schema 不一致）
4. 不解析 manifest 数组（episodes/acts/variants 不被处理）
5. product_question → description 纯映射
6. audio 缺失时不抛（诚实的无音频场景）
7. 不存在的 case 抛 FileNotFoundError（fail-closed）
"""
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 仓库根 = tests/ 的祖父（不是父）
REPO_ROOT = Path(__file__).resolve().parents[2]

from silver_demo.golden_adapter import (
    GOLDEN_CASES,
    _resolve_golden_paths,
    list_golden_cases,
    load_golden_scenario,
)

# ===========================================================================
# 1. 4 case 都能 load 成功
# ===========================================================================


def test_all_four_golden_cases_load():
    """4 case 都能 load_golden_scenario 成功（Phase 1 核心验收）。"""
    assert len(GOLDEN_CASES) == 4
    for case in GOLDEN_CASES:
        sc = load_golden_scenario(case)
        assert sc.scenario_id == case
        assert sc.source == case
        assert sc.source_type == "video_file"
        assert sc.media_path is not None
        # 路径必须以 data/golden/{case}/ 开头（用 os.sep 兼容 Windows/Linux）
        expected_prefix = f"data{os.sep}golden{os.sep}{case}{os.sep}"
        assert sc.media_path.startswith(expected_prefix), (
            f"{case}: media_path {sc.media_path!r} should start with {expected_prefix!r}"
        )
        # 文件必须实际存在（fail-closed：找不到就抛）
        assert (REPO_ROOT / sc.media_path).is_file(), f"video missing: {sc.media_path}"


# ===========================================================================
# 2. 通用规则：case_start 优先级
# ===========================================================================


def test_start_time_from_case_start():
    """3 case 走顶层 case_start 路径（stranger_visit / repeated_visit / telephone_risk）。

    VM-9 + 现有 yaml 约定：所有 start_time 必须带 tz（gateway cold_start 做 aware 减法，
    naive 会抛 TypeError）。manifest schema 不一致（无 tz 字符串）由 adapter 兜底为 UTC。
    """
    for case in ("stranger_visit", "repeated_visit", "telephone_risk"):
        sc = load_golden_scenario(case)
        assert sc.start_time is not None
        assert sc.start_time.year == 2026
        # 关键：start_time 必须带 tz（不是 naive datetime）
        assert sc.start_time.tzinfo is not None, (
            f"{case}: start_time 是 naive datetime，会让 cold_start.recover 抛 TypeError"
        )
        # 兜底为 UTC（与现有 demo scenario 约定一致）
        assert sc.start_time.utcoffset().total_seconds() == 0, (
            f"{case}: 期望 UTC 兜底（offset=0），实际 {sc.start_time.utcoffset()}"
        )


def test_start_time_fallback_to_generated():
    """evidence_insufficient 缺 case_start，应回退到 generated 兜底（通用规则）。"""
    sc = load_golden_scenario("evidence_insufficient")
    assert sc.start_time.year == 2026
    assert sc.start_time.month == 8
    assert sc.start_time.day == 16
    # 兜底时间应该是 00:00:00（无具体时间）
    assert sc.start_time.hour == 0
    # tz 兜底：也必须带 UTC
    assert sc.start_time.tzinfo is not None
    assert sc.start_time.utcoffset().total_seconds() == 0


def test_start_time_yaml_roundtrip_preserves_tz():
    """Yaml round-trip 后 start_time 仍带 tz（这是 run_demo 写入临时 yaml 时的关键不变量）。"""
    for case in GOLDEN_CASES:
        sc = load_golden_scenario(case)
        d = sc.model_dump()
        # model_dump → yaml → model 验证
        yaml_str = yaml.safe_dump(d, allow_unicode=True, sort_keys=False)
        d2 = yaml.safe_load(yaml_str)
        # datetime 会被 yaml 转回 str，验证 UTC 后缀保留
        st_str = d2['start_time']
        if isinstance(st_str, str):
            # 应该有 +00:00 或 Z 后缀
            assert st_str.endswith(('+00:00', 'Z')), (
                f"{case}: yaml round-trip 丢失 tz 后缀：{st_str!r}"
            )
            from datetime import datetime
            parsed = datetime.fromisoformat(st_str)
            assert parsed.tzinfo is not None


# ===========================================================================
# 3. 通用规则：视频路径优先级
# ===========================================================================


def test_video_path_priority_demo_over_final():
    """视频路径优先 {case}_demo.mp4（多幕预拼接）→ {case}_final.mp4 兜底。

    实际数据：3 case 有 demo.mp4（repeated_visit / telephone_risk / evidence_insufficient），
    stranger_visit 只有 _final.mp4（没有 _demo.mp4）。
    """
    # 3 case 走 demo.mp4 优先级
    for case in ("repeated_visit", "telephone_risk", "evidence_insufficient"):
        media_path, _ = _resolve_golden_paths(case)
        assert media_path.name == f"{case}_demo.mp4", (
            f"{case}: 期望 demo.mp4，实际 {media_path.name}"
        )
    # stranger_visit fallback 到 _final.mp4（无 demo.mp4）
    media_path, _ = _resolve_golden_paths("stranger_visit")
    assert media_path.name == "stranger_visit_final.mp4", (
        f"stranger_visit: 期望 final.mp4（无 demo.mp4 兜底），实际 {media_path.name}"
    )


def test_video_path_in_repo_relative():
    """media_path 相对仓库根（与现有 ScenarioConfig 字段约定一致）。"""
    for case in GOLDEN_CASES:
        sc = load_golden_scenario(case)
        # 路径不应以 / 或 C:\ 开头
        assert not sc.media_path.startswith("/"), f"absolute path: {sc.media_path}"
        assert sc.media_path[1:3] != ":\\", f"Windows abs: {sc.media_path}"


# ===========================================================================
# 4. 通用规则：音频路径
# ===========================================================================


def test_audio_path_present_for_telephone():
    """telephone_risk 应有 audio（4 case 中唯一声学相关）。"""
    sc = load_golden_scenario("telephone_risk")
    assert sc.audio_path is not None
    assert "telephone_risk" in sc.audio_path


def test_audio_path_or_none():
    """其他 3 case 音频可能缺失（不抛，诚实的无音频）。"""
    for case in ("stranger_visit", "repeated_visit", "evidence_insufficient"):
        sc = load_golden_scenario(case)
        # 音频可能存在或不存在，但不应抛
        if sc.audio_path:
            assert "golden" in sc.audio_path


# ===========================================================================
# 5. 纯映射：product_question → description
# ===========================================================================


def test_description_from_product_question():
    """description 来自 manifest.product_question（纯映射，不硬编码）。"""
    expected_questions = {
        "stranger_visit":       "什么情况开始值得关注？",
        "repeated_visit":       "系统真的记得过去吗？",
        "telephone_risk":       "多模态为什么更有用？",
        "evidence_insufficient": "为什么没有误报？",
    }
    for case, expected_q in expected_questions.items():
        sc = load_golden_scenario(case)
        assert sc.description == expected_q, (
            f"{case}: description mismatch, got {sc.description!r}"
        )


# ===========================================================================
# 6. 纯映射：loop / fps_target / frame_interval 是固定值（这是"运行参数"，不是 manifest 字段）
# ===========================================================================


def test_runtime_params_are_fixed():
    """运行参数（loop/fps_target/frame_interval）固定为 0.5/8/True。"""
    for case in GOLDEN_CASES:
        sc = load_golden_scenario(case)
        assert sc.frame_interval_s == 0.5
        assert sc.fps_target == 8
        assert sc.loop is True


# ===========================================================================
# 7. fail-closed：未知 case 抛错
# ===========================================================================


def test_unknown_case_raises():
    """不存在的 case 抛 FileNotFoundError（不静默接受）。"""
    with pytest.raises((FileNotFoundError, ValueError)):
        load_golden_scenario("nonexistent_case_xyz")


# ===========================================================================
# 8. 不解析 manifest 数组（纯映射守卫）
# ===========================================================================


def test_does_not_parse_episodes():
    """repeated_visit 的 episodes[].memory_ref 不会被自动注入到 config（由 Phase 2 单独处理）。"""
    sc = load_golden_scenario("repeated_visit")
    # GoldenScenarioConfig 不应有 episodes / memory_ref 字段（纯字段映射）
    assert not hasattr(sc, "episodes")
    assert not hasattr(sc, "memory_ref")
    assert not hasattr(sc, "prior_episodes")


def test_does_not_parse_acoustic_progression():
    """telephone_risk 的 acoustic_progression 不会被自动注入。"""
    sc = load_golden_scenario("telephone_risk")
    assert not hasattr(sc, "acoustic_progression")
    assert not hasattr(sc, "audio_progression")


def test_does_not_parse_variants():
    """telephone_risk 的 variants 不会被自动选择（adapter 默认 case_b 路径）。"""
    sc = load_golden_scenario("telephone_risk")
    assert not hasattr(sc, "variants")
    # 但 media_path 实际指向 case_b（这是通过路径兜底表实现的，不是解析 variants）
    assert "case_b_mix" in sc.audio_path or "telephone_risk_demo" in sc.media_path


# ===========================================================================
# 9. 辅助：list_golden_cases 实际扫描
# ===========================================================================


def test_list_golden_cases_matches_hardcoded():
    """文件系统扫描应与 GOLDEN_CASES 一致（4 case）。"""
    actual = set(list_golden_cases())
    expected = set(GOLDEN_CASES)
    assert actual == expected, f"actual={actual}, expected={expected}"
