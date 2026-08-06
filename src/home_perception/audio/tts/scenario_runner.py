"""感知场景运行器（Audio Synthetic Infrastructure / ``tts`` 包子模块）。

将声明式感知场景（``scenarios/audio/*.yaml``）接驳到真实感知管道，完成
**Phase B 测试语言闭环**：

    scenario.yaml  →  合成 WAV（base + effects）  →  AudioPipeline  →  observed kinds  →  == expected

设计要点：
- 场景即测试语言（spec-as-test）：``base.file`` 引用 ``tests/fixtures/audio/`` 黄金基线，
  ``effects`` 复用 :mod:`effects` 的增强原语，``expected.perception`` 声明期望的
  ``AudioPerceptionKind`` 字符串列表。测试直接写 ``assert run(scenario).events == scenario.expected``，
  无需为每种退化条件新增 WAV 入库。
- **精确相等是契约（strict）**：``run(scenario).events == scenario.expected`` 与 YAML 中
  ``expected`` 的书写顺序无关（两侧均在加载 / 运行时排序，且 ``events`` 会去重合并多个 VAD 段
  重复产出的同一 kind，成为「触发了哪些 kind」的集合视图）。这是 Phase B 的主测试形态。
- 合成阶段完全复用 :mod:`generator`（含 ``base_ref`` 解析 / WAV 编解码 / ``apply_effects``），
  本模块只负责「加载单文件场景」+「跑管道取 observed」+「以 ScenarioRun 返回」。``validate_scenario``
  仅为 CLI / 旧调用者保留的薄封装。
- 管道导入惰性化：``AudioPipeline`` 仅在 ``run`` 内 import，使本模块可被轻量单测导入，
  且不强制感知链在 import 期被拉起。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .generator import Scenario as GenScenario
from .generator import generate_scenario


@dataclass
class PerceptionScenario:
    """单条感知场景（对应 ``scenarios/audio/<name>.yaml`` 一个文件）。"""

    name: str
    base_file: str
    effects: list[dict] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)  # AudioPerceptionKind 字符串值

    def to_gen_scenario(self) -> GenScenario:
        """桥接到 ``generator.Scenario`` 以便复用合成逻辑。"""
        return GenScenario(
            id=self.name,
            base_ref=self.base_file,
            effects=list(self.effects),
        )


@dataclass
class ValidationResult:
    """场景校验结果。"""

    name: str
    observed: list[str]
    expected: list[str]
    ok: bool
    strict: bool

    def __str__(self) -> str:
        mode = "strict" if self.strict else "subset"
        status = "PASS" if self.ok else "FAIL"
        return (
            f"[{status}] {self.name} ({mode}) "
            f"observed={self.observed} expected={self.expected}"
        )


@dataclass
class ScenarioRun:
    """一次场景运行的结果（Phase B 测试语言的核心返回值）。

    - ``events``：observed 的 ``AudioPerceptionKind`` 字符串**升序去重**列表，
      即「本场景触发了哪些 kind」的集合视图（同一 kind 在多个 VAD 段重复出现会被合并）。
      因此 ``run(scenario).events == scenario.expected`` 与 YAML 中 ``expected`` 的书写顺序无关，
      也不受 VAD 分段数影响——spec 只需列出每种期望 kind 一次。
    - ``wav``：本次合成产出的 WAV 路径。默认 ``run`` 在临时目录内合成，目录随 run 结束清理，
      故不要在 run 外长期持有该路径（需持有时显式传入 ``work_dir``）。
    """

    events: list[str]
    wav: Path


def load_scenario(path: Path) -> PerceptionScenario:
    """解析单文件场景 yaml。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "name" not in data:
        raise ValueError(f"scenario {path} 缺少 name")
    base = data.get("base") or {}
    if "file" not in base:
        raise ValueError(f"scenario {data['name']!r} 缺少 base.file")
    exp = data.get("expected") or {}
    perc = exp.get("perception") or []
    # 归一化为升序列表：使 `run(scenario).events == scenario.expected` 与 YAML 书写顺序无关。
    expected = sorted(str(k) for k in perc)
    return PerceptionScenario(
        name=data["name"],
        base_file=base["file"],
        effects=list(data.get("effects") or []),
        expected=expected,
    )


def load_scenarios_dir(directory: Path) -> list[PerceptionScenario]:
    """加载目录下全部 ``*.yaml`` 场景（按文件名排序，确定性）。"""
    directory = Path(directory)
    out: list[PerceptionScenario] = []
    for p in sorted(directory.glob("*.yaml")):
        out.append(load_scenario(p))
    return out


def synthesize(
    scn: PerceptionScenario,
    out_dir: Path,
    fixtures_root: Path,
) -> Path:
    """合成场景 WAV（base + effects），返回写出路径。复用 ``generator.generate_scenario``。"""
    return generate_scenario(
        scn.to_gen_scenario(),
        Path(out_dir),
        fixtures_root=Path(fixtures_root),
    )


def _observed_kinds(wav_path: Path) -> list[str]:
    """跑真实感知管道，返回 observed ``AudioPerceptionKind`` 字符串值列表。"""
    from ..pipeline import AudioPipeline  # 惰性 import：避免 import 期拉起感知链

    # 仅构造一次 FileAudioSource（from_defaults 配置 pipeline.source），直接喂给 run，
    # 避免 run_path 再建一个 source 造成的重复构造与意图不清（评审 B1）。
    pipeline = AudioPipeline.from_defaults(str(wav_path))
    events = pipeline.run(pipeline.source)
    return [e.kind.value for e in events]


def _default_fixtures_root() -> Path:
    """从本模块位置推导 ``tests/fixtures/audio``（不依赖 cwd），使 ``run(scenario)`` 无需传 fixtures_root。"""
    # src/home_perception/audio/tts/scenario_runner.py -> parents[4] = 仓库根
    return Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "audio"


def run(
    scn: PerceptionScenario,
    fixtures_root: Path | None = None,
    work_dir: Path | None = None,
) -> ScenarioRun:
    """合成场景并跑真实感知管道，返回 :class:`ScenarioRun`。

    Phase B「场景即测试语言」的单一入口：测试直接写
    ``assert run(scenario).events == scenario.expected``。

    :param scn: 声明式场景（来自 ``scenarios/audio/*.yaml``）。
    :param fixtures_root: 用于解析 ``base.file``（golden fixtures）。缺省时从模块位置推导。
    :param work_dir: 指定合成输出目录（便于调试 / 复核 WAV）；缺省用临时目录，run 结束即清理。
    """
    root = Path(fixtures_root) if fixtures_root is not None else _default_fixtures_root()

    def _do(out_dir: Path) -> ScenarioRun:
        wav = synthesize(scn, out_dir, root)
        observed = _observed_kinds(wav)
        # 升序去重：合并多个 VAD 段重复产出的同一 kind，使 events 成为「触发了哪些 kind」的集合视图。
        return ScenarioRun(events=sorted(set(observed)), wav=wav)

    if work_dir is None:
        # WAV 必须在临时目录清理前完整载入内存：AudioPipeline.run 走离线 EnergyVAD，
        # 一次性 source.load() 读完整 buffer（评审 B2；未来若扩展 streaming 须重审此处）。
        with tempfile.TemporaryDirectory() as td:
            return _do(Path(td))
    return _do(Path(work_dir))


# 向后兼容别名：旧代码 / CLI 可能仍引用 run_scenario（位置参数 fixtures_root 仍支持）。
run_scenario = run


def validate_scenario(
    scn: PerceptionScenario,
    fixtures_root: Path | None = None,
    strict: bool = True,
) -> ValidationResult:
    """校验单条场景：精确相等（Phase B 契约 ``observed == expected``）。

    ``strict`` 仅用于结果记录（CLI 展示），默认即精确相等——Phase B 不再提供子集语义，
    因为场景即规格，``expected`` 必须精确对齐管道实际产出。
    """
    result = run(scn, fixtures_root)
    exp = list(scn.expected)
    ok = result.events == exp
    return ValidationResult(
        name=scn.name, observed=result.events, expected=exp, ok=ok, strict=strict
    )
