"""感知场景运行器（Audio Synthetic Infrastructure / ``tts`` 包子模块）。

将声明式感知场景（``scenarios/audio/*.yaml``）接驳到真实感知管道，完成
**Phase A 验证闭环**：

    scenario.yaml  →  合成 WAV（base + effects）  →  AudioPipeline  →  observed kinds  →  对比 expected

设计要点：
- 场景是「测试语言」的载体：``base.file`` 引用 ``tests/fixtures/audio/`` 黄金基线，
  ``effects`` 复用 :mod:`effects` 的增强原语，``expected.perception`` 声明期望的
  ``AudioPerceptionKind`` 字符串列表。无需为每种退化条件新增 WAV 入库。
- 合成阶段完全复用 :mod:`generator`（含 ``base_ref`` 解析 / WAV 编解码 / ``apply_effects``），
  本模块只负责「加载单文件场景」+「跑管道取 observed」+「校验」。
- 校验语义：
  - 默认（Phase A）：``observed ⊆ expected``（子集）。当前 Tier0 规则每条语音段只产一个 kind，
    故 ``expected`` 写成多值列表表示「可能出现其中任意一个均算通过」，兼容用户 Phase B 示例。
  - ``strict=True``（Phase B 就绪）：``observed == expected``（精确相等）。
- 管道导入惰性化：``AudioPipeline`` 仅在 ``run_scenario`` 内 import，使本模块可被轻量单测导入，
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
    return PerceptionScenario(
        name=data["name"],
        base_file=base["file"],
        effects=list(data.get("effects") or []),
        expected=[str(k) for k in perc],
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

    events = AudioPipeline.from_defaults(str(wav_path)).run_path(str(wav_path))
    return [e.kind.value for e in events]


def run_scenario(
    scn: PerceptionScenario,
    fixtures_root: Path,
    work_dir: Path | None = None,
) -> list[str]:
    """合成场景到临时（或指定）目录，跑管道返回 observed kinds。"""
    own_td = False
    if work_dir is None:
        td = tempfile.TemporaryDirectory()
        work_dir = Path(td.name)
        own_td = True
    else:
        td = None
        work_dir = Path(work_dir)
    try:
        wav = synthesize(scn, work_dir, fixtures_root)
        return _observed_kinds(wav)
    finally:
        if own_td:
            td.cleanup()


def validate_scenario(
    scn: PerceptionScenario,
    fixtures_root: Path,
    strict: bool = False,
) -> ValidationResult:
    """校验单条场景：``observed ⊆ expected``（默认）或 ``observed == expected``（strict）。"""
    observed = run_scenario(scn, fixtures_root)
    exp = list(scn.expected)
    if strict:
        ok = sorted(observed) == sorted(exp)
    else:
        # 子集语义：每个 observed 都必须在 expected 中；observed 为空当且仅当 expected 也为空。
        ok = all(o in exp for o in observed) and (len(observed) > 0 or len(exp) == 0)
    return ValidationResult(
        name=scn.name, observed=observed, expected=exp, ok=ok, strict=strict
    )
