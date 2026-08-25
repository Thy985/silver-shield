"""场景级 ``audio_replay_path`` 合约测试（synthetic_replay · Owner 裁决 2026-08-24 · torch-free）。

Product Story 音频事实源切换（裁决出处
``docs/reports/RISK-MIX-SIXTUPLE-VERIFICATION-2026-08-24.md`` §6）：语义事实源 =
validation fixture ``audio:`` 声明式注入（经 ``audio_replay_path``），mix.wav 降级为
浏览器播放介质、不再承担语义判定。本测试锁定四层合约：

1. **字段合约**：``ScenarioConfig.audio_replay_path`` 默认 None（旧场景零影响）；
2. **场景接线**：product_story_risk / product_story_benign 两 yaml 必须配置
   ``audio_replay_path``（防后续改 yaml 漏检，与 test_scenario_audio_evidence_override 同型）；
3. **fixture 编译**：``_load_audio_events_from_fixture`` 与 validation compiler 同款映射
   （event_id/kind/score/confidence/labels/source_segment_ids 透传；非法 kind fail-closed）;
4. **回落路径**：未配置 replay 时组装层仍走 FileAudioSource+AudioPipeline 原路，
   且 audio.enabled=False 时诚实返回空。
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DEMO_PATH = REPO_ROOT / "scripts" / "run_demo.py"


def _load_run_demo():
    """以独立模块名加载 scripts/run_demo.py（顶层仅标准库依赖，torch 懒加载）。"""
    spec = importlib.util.spec_from_file_location("run_demo_under_test", RUN_DEMO_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_scenario(**kwargs):
    from silver_demo.scenarios import ScenarioConfig

    base = {
        "scenario_id": "t",
        "source": "t",
        "start_time": datetime(2026, 7, 22, tzinfo=UTC),
    }
    base.update(kwargs)
    return ScenarioConfig(**base)


# ============================================================================
# 1. 字段合约
# ============================================================================


def test_scenario_config_audio_replay_path_defaults_to_none():
    """audio_replay_path 默认 None：既有场景（无该字段）行为零变化。"""
    sc = _make_scenario()
    assert sc.audio_replay_path is None


def test_scenario_config_accepts_audio_replay_path():
    rel = "src/home_perception/validation/fixtures/scenarios/product_story/call_connected_normal_001.yaml"
    sc = _make_scenario(audio_replay_path=rel)
    assert sc.audio_replay_path == rel


# ============================================================================
# 2. 场景接线（防 yaml 漏检）
# ============================================================================


@pytest.mark.parametrize(
    ("name", "fixture_tail"),
    [
        (
            "product_story_risk.yaml",
            "telephone_risk_multimodal_001.yaml",
        ),
        (
            "product_story_benign.yaml",
            "call_connected_normal_001.yaml",
        ),
    ],
)
def test_product_story_yaml_wires_audio_replay(name: str, fixture_tail: str):
    """两个 Product Story 场景必须指向对应 fixture 的 audio_replay_path。"""
    from silver_demo.scenarios import load_scenario

    path = REPO_ROOT / "config" / "demo" / "scenarios" / name
    sc = load_scenario(path)
    assert sc.audio_replay_path is not None, f"{name} 缺 audio_replay_path（synthetic_replay 事实源）"
    assert Path(sc.audio_replay_path).name == fixture_tail
    # 播放介质与语义事实源双字段并存（mix.wav 仅播放，不承担语义判定）
    assert sc.audio_path, f"{name} 缺 audio_path（浏览器播放介质）"


# ============================================================================
# 3. fixture 编译映射
# ============================================================================


def test_load_audio_events_from_fixture_maps_specs_faithfully():
    mod = _load_run_demo()
    fixture = (
        REPO_ROOT
        / "src/home_perception/validation/fixtures/scenarios/product_story/telephone_risk_multimodal_001.yaml"
    )
    events = mod._load_audio_events_from_fixture(str(fixture))

    assert len(events) == 8
    first, last = events[0], events[-1]
    # 首枚（US_dial_tone 区间）：与 fixture 声明逐键一致
    assert first["event_id"] == "telephone_risk_multimodal_001-aev-000"
    assert first["kind"] == "audio_telephone_persistent"
    assert first["score"] == pytest.approx(0.981)
    assert first["confidence"] == pytest.approx(0.95)
    assert first["labels"] == ["telephone", "dial_tone"]
    assert first["source_segment_ids"] == ["telephone__dial-tone__US_dial_tone"]
    # 尾枚（LBJ 区间）
    assert last["event_id"] == "telephone_risk_multimodal_001-aev-007"
    assert last["labels"] == ["telephone", "speech"]
    # 全部 event_id 场景内唯一（网关幂等去重主键前提）
    ids = [e["event_id"] for e in events]
    assert len(set(ids)) == len(ids)


def test_load_audio_events_from_fixture_rejects_invalid_kind(tmp_path: Path):
    """非法 kind fail-closed 抛错（不产出静默错误事件）。"""
    real = (
        REPO_ROOT
        / "src/home_perception/validation/fixtures/scenarios/product_story/call_connected_normal_001.yaml"
    )
    data = yaml.safe_load(real.read_text(encoding="utf-8"))
    data["audio"][0]["kind"] = "audio_hallucination_xyz"
    bad = tmp_path / "bad_fixture.yaml"
    bad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    mod = _load_run_demo()
    with pytest.raises(ValueError, match="audio_hallucination_xyz"):
        mod._load_audio_events_from_fixture(str(bad))


# ============================================================================
# 4. 回落路径与门控
# ============================================================================


def test_build_live_audio_events_falls_back_to_pipeline(monkeypatch: pytest.MonkeyPatch):
    """无 audio_replay_path 时回落既有 AudioPipeline 原路（to_dict 列表）。"""
    calls: dict[str, object] = {}

    class _FakeEvent:
        def to_dict(self) -> dict:
            return {"event_id": "rt-0"}

    class _FakePipeline:
        @classmethod
        def from_audio_config(cls, audio_cfg, source):
            calls["from_audio_config"] = (audio_cfg, source)
            return cls()

        def run(self, source):
            calls["ran"] = source
            return [_FakeEvent()]

    monkeypatch.setattr(
        "home_perception.audio.pipeline.AudioPipeline", _FakePipeline, raising=True
    )

    mod = _load_run_demo()
    hp = SimpleNamespace(audio=SimpleNamespace(enabled=True))
    scn = SimpleNamespace(audio_replay_path=None, audio_path="dataset/x/mix.wav")
    out = mod._build_live_audio_events(hp, scn)

    assert out == [{"event_id": "rt-0"}]
    assert "from_audio_config" in calls and "ran" in calls


def test_build_live_audio_events_disabled_returns_empty(monkeypatch: pytest.MonkeyPatch):
    """audio.enabled=False：诚实空列表，且不得触碰 AudioPipeline。"""
    monkeypatch.setattr(
        "home_perception.audio.pipeline.AudioPipeline",
        SimpleNamespace(from_audio_config=None),
        raising=True,
    )
    mod = _load_run_demo()
    hp = SimpleNamespace(audio=SimpleNamespace(enabled=False))
    scn = SimpleNamespace(audio_replay_path="some.yaml", audio_path="x.wav")
    assert mod._build_live_audio_events(hp, scn) == []


def test_build_live_audio_events_replay_branch_skips_pipeline(monkeypatch: pytest.MonkeyPatch):
    """命中 replay 分支：不经 AudioPipeline 推理（mix.wav 不再承担语义判定）。"""

    def _boom(*_a, **_k):  # pragma: no cover - 触发即失败
        raise AssertionError("replay 分支不得实例化 AudioPipeline")

    monkeypatch.setattr("home_perception.audio.pipeline.AudioPipeline", _boom, raising=True)

    mod = _load_run_demo()
    hp = SimpleNamespace(audio=SimpleNamespace(enabled=True))
    scn = SimpleNamespace(
        audio_replay_path=(
            "src/home_perception/validation/fixtures/scenarios/product_story/"
            "call_connected_normal_001.yaml"
        ),
        audio_path="x.wav",
    )
    events = mod._build_live_audio_events(hp, scn)

    assert len(events) == 8
    assert all(e["kind"] == "audio_telephone_persistent" for e in events)