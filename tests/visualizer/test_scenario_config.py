"""Phase 2: 场景布局配置单元测试。

覆盖：
- ``get_scenario_surfaces(scenario_id)`` 返回正确 Surface 集合
- ``has_audio_surface()`` 判断场景是否可见音频 Surface
- ``has_memory_surface()`` 判断场景是否可见 Memory Context
- ``render_scenario_surface_banner()`` HTML 输出正确
- 未知场景返回默认最小集（fail-closed）
- 音频 Surface 隔离：cctv 必须隐藏，telephone_risk 必须包含
"""

from __future__ import annotations

from home_perception.visualizer.viewer.scenario_config import (
    ScenarioSurface,
    get_scenario_surfaces,
    has_audio_surface,
    has_memory_surface,
    render_scenario_surface_banner,
)

# ============================================================================
# get_scenario_surfaces 契约
# ============================================================================


class TestGetScenarioSurfaces:
    """场景 Surface 集合判定测试。"""

    def test_telephone_risk_has_audio_surfaces(self):
        """telephone_risk 必须包含 L1 / L2 音频 Surface。"""
        surfaces = get_scenario_surfaces("telephone_risk")
        assert ScenarioSurface.L1_AUDIO_PERCEPTION in surfaces
        assert ScenarioSurface.L2_ACOUSTIC_STATE in surfaces

    def test_cctv_surveillance_no_audio_surfaces(self):
        """cctv_surveillance 必须隐藏所有音频 Surface（AC-12）。"""
        surfaces = get_scenario_surfaces("cctv_surveillance")
        assert ScenarioSurface.L1_AUDIO_PERCEPTION not in surfaces
        assert ScenarioSurface.L2_ACOUSTIC_STATE not in surfaces

    def test_repeated_visit_no_memory_yet(self):
        """repeated_visit 暂不包含 L6（Phase 3 阻塞）。"""
        surfaces = get_scenario_surfaces("repeated_visit")
        assert ScenarioSurface.L6_MEMORY_CONTEXT not in surfaces

    def test_unknown_scenario_returns_default_minimal(self):
        """未知场景返回默认最小集（fail-closed）。"""
        surfaces = get_scenario_surfaces("unknown_scenario_xxx")
        assert surfaces == {
            ScenarioSurface.L0_AUDIO_HEALTH,
            ScenarioSurface.L3_PERCEPTION_STREAM,
            ScenarioSurface.L4_RISK_SIGNALS,
            ScenarioSurface.L5_PROVENANCE,
        }

    def test_telephone_risk_has_all_core_surfaces(self):
        """telephone_risk 必须包含全部核心 Surface（L0-L5）。"""
        surfaces = get_scenario_surfaces("telephone_risk")
        for core in (
            ScenarioSurface.L0_AUDIO_HEALTH,
            ScenarioSurface.L1_AUDIO_PERCEPTION,
            ScenarioSurface.L2_ACOUSTIC_STATE,
            ScenarioSurface.L3_PERCEPTION_STREAM,
            ScenarioSurface.L4_RISK_SIGNALS,
            ScenarioSurface.L5_PROVENANCE,
        ):
            assert core in surfaces, f"缺少核心 Surface: {core}"

    def test_cctv_surveillance_has_core_surfaces(self):
        """cctv_surveillance 必须包含全部核心 Surface（无 L1/L2）。"""
        surfaces = get_scenario_surfaces("cctv_surveillance")
        for core in (
            ScenarioSurface.L0_AUDIO_HEALTH,
            ScenarioSurface.L3_PERCEPTION_STREAM,
            ScenarioSurface.L4_RISK_SIGNALS,
            ScenarioSurface.L5_PROVENANCE,
        ):
            assert core in surfaces, f"缺少核心 Surface: {core}"
        # 验证无音频 Surface
        assert ScenarioSurface.L1_AUDIO_PERCEPTION not in surfaces
        assert ScenarioSurface.L2_ACOUSTIC_STATE not in surfaces


# ============================================================================
# has_audio_surface 契约
# ============================================================================


class TestHasAudioSurface:
    """音频 Surface 可见性测试。"""

    def test_telephone_risk_has_audio(self):
        """telephone_risk → 有音频 Surface。"""
        assert has_audio_surface("telephone_risk") is True

    def test_cctv_surveillance_no_audio(self):
        """cctv_surveillance → 无音频 Surface。"""
        assert has_audio_surface("cctv_surveillance") is False

    def test_repeated_visit_no_audio(self):
        """repeated_visit → 无音频 Surface（当前 Phase 2 实现）。"""
        assert has_audio_surface("repeated_visit") is False

    def test_unknown_scenario_no_audio(self):
        """未知场景 → 无音频 Surface（默认最小集）。"""
        assert has_audio_surface("unknown") is False


# ============================================================================
# has_memory_surface 契约
# ============================================================================


class TestHasMemorySurface:
    """Memory Context 可见性测试。"""

    def test_repeated_visit_no_memory_yet(self):
        """repeated_visit → 暂不支持 L6（Phase 3 阻塞）。"""
        assert has_memory_surface("repeated_visit") is False

    def test_telephone_risk_no_memory(self):
        """telephone_risk → 无 Memory Surface。"""
        assert has_memory_surface("telephone_risk") is False

    def test_unknown_scenario_no_memory(self):
        """未知场景 → 无 Memory Surface。"""
        assert has_memory_surface("unknown") is False


# ============================================================================
# render_scenario_surface_banner 契约
# ============================================================================


class TestRenderScenarioSurfaceBanner:
    """场景 Surface Banner HTML 输出测试。"""

    def test_banner_contains_scenario_id(self):
        """banner 必须包含场景 ID 属性。"""
        html = render_scenario_surface_banner("telephone_risk")
        assert 'data-scenario="telephone_risk"' in html

    def test_banner_contains_surface_count(self):
        """banner 必须包含 Surface 数量。"""
        html = render_scenario_surface_banner("telephone_risk")
        assert "data-surfaces=\"6\"" in html  # telephone_risk 有 6 个 Surface

    def test_banner_contains_surface_labels(self):
        """banner 必须列出所有 Surface 标签。"""
        html = render_scenario_surface_banner("telephone_risk")
        assert "L0_AUDIO_HEALTH" in html
        assert "L1_AUDIO_PERCEPTION" in html
        assert "L2_ACOUSTIC_STATE" in html
        assert "L3_PERCEPTION_STREAM" in html
        assert "L4_RISK_SIGNALS" in html
        assert "L5_PROVENANCE" in html

    def test_cctv_banner_no_audio_labels(self):
        """cctv banner 不得包含音频 Surface 标签。"""
        html = render_scenario_surface_banner("cctv_surveillance")
        assert "L1_AUDIO_PERCEPTION" not in html
        assert "L2_ACOUSTIC_STATE" not in html

    def test_unknown_scenario_minimal_banner(self):
        """未知场景 banner 只列出 4 个默认 Surface。"""
        html = render_scenario_surface_banner("unknown")
        assert "data-surfaces=\"4\"" in html


# ============================================================================
# 铁律测试：音频 Surface 隔离
# ============================================================================


class TestAudioSurfaceIsolation:
    """铁律：音频 Surface 必须严格隔离（cctv 零可见）。"""

    def test_cctv_has_zero_audio_evidence(self):
        """cctv 场景 audio_surface_count = 0。"""
        surfaces = get_scenario_surfaces("cctv_surveillance")
        audio_count = sum(
            1 for s in surfaces if s in (ScenarioSurface.L1_AUDIO_PERCEPTION, ScenarioSurface.L2_ACOUSTIC_STATE)
        )
        assert audio_count == 0, f"cctv 不得有音频 Surface，实际: {audio_count}"

    def test_telephone_risk_has_both_audio_surfaces(self):
        """telephone_risk 必须同时有 L1 + L2。"""
        surfaces = get_scenario_surfaces("telephone_risk")
        assert ScenarioSurface.L1_AUDIO_PERCEPTION in surfaces
        assert ScenarioSurface.L2_ACOUSTIC_STATE in surfaces