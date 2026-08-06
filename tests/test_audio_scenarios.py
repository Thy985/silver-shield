"""感知场景测试（Phase A 验证闭环 + Phase B 测试语言种子）。

消费 ``scenarios/audio/*.yaml``：合成 WAV（base + effects）→ 跑 AudioPipeline → 对比 expected。

- 每个场景文件即一条测试输入（Phase B「测试语言」雏形）。
- 默认子集语义：``observed ⊆ expected``；``strict`` 精确相等用于单 kind 场景。
- 跨包契约：场景声明的 expected 必须是合法 ``AudioPerceptionKind`` 值。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from home_perception.audio.event import AUDIO_PERCEPTION_KIND_VALUES
from home_perception.audio.tts.scenario_runner import (
    PerceptionScenario,
    ValidationResult,
    load_scenario,
    load_scenarios_dir,
    run_scenario,
    synthesize,
    validate_scenario,
)

REPO = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = REPO / "scenarios" / "audio"
FIXTURES = REPO / "tests" / "fixtures" / "audio"

pytestmark = pytest.mark.skipif(
    not SCENARIOS_DIR.is_dir(),
    reason="scenarios/audio 目录缺失（测试语言源文件未就位）",
)


def _scenarios() -> list:
    return load_scenarios_dir(SCENARIOS_DIR)


# ---------------------------------------------------------------------------
# 解析 / 契约
# ---------------------------------------------------------------------------


def test_load_all_scenarios_parses():
    scns = _scenarios()
    assert len(scns) >= 5
    names = {s.name for s in scns}
    # 5 个黄金基线 + 至少 1 个复合场景
    assert {"normal_speech", "rapid_speech", "raised_voice", "telephone", "crying"} <= names


def test_scenario_fields_parsed():
    scn = load_scenario(SCENARIOS_DIR / "elderly_distress.yaml")
    assert scn.name == "elderly_distress"
    assert scn.base_file == "normal_speech.wav"
    assert {"speech_rate", "noise"} <= {next(iter(e)) for e in scn.effects}
    assert scn.expected == ["audio_speech_rapid"]


def test_expected_kinds_are_valid_enum_values():
    """跨包契约：场景声明的 expected 必须是合法 AudioPerceptionKind。"""
    valid = set(AUDIO_PERCEPTION_KIND_VALUES)
    for scn in _scenarios():
        for k in scn.expected:
            assert k in valid, f"scenario {scn.name!r} 声明非法 kind {k!r}"


def test_negative_control_has_empty_expected():
    scn = load_scenario(SCENARIOS_DIR / "normal_speech.yaml")
    assert scn.expected == []
    assert scn.effects == []


# ---------------------------------------------------------------------------
# 验证闭环（Phase A）：scenario → wav → expected event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [s.name for s in _scenarios()],
    ids=[s.name for s in _scenarios()],
)
def test_scenario_validates_subset(name: str):
    """每个场景：observed ⊆ expected（当前 Tier0 每条段只产一个 kind）。"""
    scn = next(s for s in _scenarios() if s.name == name)
    res = validate_scenario(scn, FIXTURES)
    assert isinstance(res, ValidationResult)
    assert res.ok, str(res)


def test_elderly_distress_emits_rapid():
    """复合场景从 normal 基线 + effects 合成出 audio_speech_rapid。"""
    scn = load_scenario(SCENARIOS_DIR / "elderly_distress.yaml")
    res = validate_scenario(scn, FIXTURES)
    assert res.ok
    assert res.observed == ["audio_speech_rapid"]


def test_strict_equality_on_single_kind_scenario():
    """strict 模式：单 kind 场景 observed 精确等于 expected。"""
    scn = load_scenario(SCENARIOS_DIR / "rapid_speech.yaml")
    res = validate_scenario(scn, FIXTURES, strict=True)
    assert res.ok
    assert res.observed == ["audio_speech_rapid"]


def test_negative_control_produces_no_event():
    scn = load_scenario(SCENARIOS_DIR / "normal_speech.yaml")
    res = validate_scenario(scn, FIXTURES)
    assert res.observed == []
    assert res.ok


def test_strict_fails_when_expected_too_narrow():
    """strict 下若 expected 漏写实际 kind，应判 FAIL（演示 Phase B 精确语义）。"""
    scn = load_scenario(SCENARIOS_DIR / "rapid_speech.yaml")
    scn.expected = []  # 故意写窄
    res = validate_scenario(scn, FIXTURES, strict=True)
    assert not res.ok


# ---------------------------------------------------------------------------
# 桥接 / 失败路径（评审 T2 / T3）
# ---------------------------------------------------------------------------


def test_synthesize_writes_file(tmp_path):
    """核心桥接函数 synthesize 直接写出 WAV 且含非零能量。"""
    from home_perception.audio.source import FileAudioSource

    scn = load_scenario(SCENARIOS_DIR / "elderly_distress.yaml")
    out = synthesize(scn, tmp_path, FIXTURES)
    assert out.exists()
    assert out.suffix == ".wav"
    audio = FileAudioSource(str(out)).load()
    assert audio.samples.size > 0
    assert float(np.max(np.abs(audio.samples))) > 0.0


def test_unknown_effect_raises(tmp_path):
    """声明未注册 effect 名应 fail-fast（KeyError），而非静默忽略。"""
    scn = PerceptionScenario(
        name="bad_effect",
        base_file="normal_speech.wav",
        effects=[{"speech_reate": {}}],  # 拼写错误：应为 speech_rate
    )
    with pytest.raises(KeyError):
        run_scenario(scn, FIXTURES, work_dir=tmp_path)


def test_apply_effects_rejects_unknown_key():
    """底层 effects.apply_effects 对未知效果名显式 KeyError（反馈用户输入）。"""
    from home_perception.audio.tts.effects import apply_effects

    sr = 16000
    x = np.zeros((sr,), dtype=np.float32)
    with pytest.raises(KeyError):
        apply_effects(x, sr, [{"speech_reate": {}}])
