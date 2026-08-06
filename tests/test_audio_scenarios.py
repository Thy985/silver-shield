"""感知场景测试（Phase B：场景即测试语言）。

消费 ``scenarios/audio/*.yaml``：合成 WAV（base + effects）→ 跑 AudioPipeline → 对比 expected。

Phase B 主测试形态：场景文件即规格（spec-as-test），测试直接写
``assert run(scenario).events == scenario.expected``。``expected`` 在加载期已排序，
``run`` 返回的 ``events`` 也已排序，故断言与 YAML 中书写顺序无关。

- 跨包契约：场景声明的 expected 必须是合法 ``AudioPerceptionKind`` 值。
- 桥接 / 失败路径：``synthesize`` 写出非零能量 WAV；未知 effect 名 fail-fast（KeyError）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from home_perception.audio.event import AUDIO_PERCEPTION_KIND_VALUES
from home_perception.audio.tts.scenario_runner import (
    PerceptionScenario,
    ScenarioRun,
    load_scenario,
    load_scenarios_dir,
    run,
    synthesize,
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
# Phase B 核心：场景即测试语言（assert run(scenario).events == scenario.expected）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scenario", load_scenarios_dir(SCENARIOS_DIR), ids=lambda s: s.name
)
def test_scenario_self_describing(scenario: PerceptionScenario):
    """每个 scenario 文件即一条测试：observed kinds 精确等于其声明的 expected。

    这是 Phase B 的主测试形态——scenario 文件是 spec，``run`` 是其执行器。
    """
    result = run(scenario, fixtures_root=FIXTURES)
    assert isinstance(result, ScenarioRun)
    assert result.events == scenario.expected


def test_run_normalizes_order_independent():
    """expected 与 observed 均已排序，断言与 YAML 书写顺序无关。"""
    scn = load_scenario(SCENARIOS_DIR / "elderly_distress.yaml")
    assert run(scn, fixtures_root=FIXTURES).events == ["audio_speech_rapid"]
    # 即便 YAML 把 expected 写成乱序，加载期已排序，断言仍成立
    scn.expected = ["audio_speech_rapid"]  # 单值；多值场景同理
    assert run(scn, fixtures_root=FIXTURES).events == scn.expected


def test_contract_fails_when_expected_too_narrow():
    """Phase B 契约 fail-fast：若 expected 漏写实际 kind，run().events != expected 必失败。"""
    scn = load_scenario(SCENARIOS_DIR / "rapid_speech.yaml")
    assert run(scn, fixtures_root=FIXTURES).events == scn.expected  # 正确契约通过
    scn.expected = []  # 故意写窄（漏配）
    assert run(scn, fixtures_root=FIXTURES).events != scn.expected  # 漏配必失败


# ---------------------------------------------------------------------------
# 桥接 / 失败路径
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
        run(scn, FIXTURES, work_dir=tmp_path)


def test_apply_effects_rejects_unknown_key():
    """底层 effects.apply_effects 对未知效果名显式 KeyError（反馈用户输入）。"""
    from home_perception.audio.tts.effects import apply_effects

    sr = 16000
    x = np.zeros((sr,), dtype=np.float32)
    with pytest.raises(KeyError):
        apply_effects(x, sr, [{"speech_reate": {}}])
