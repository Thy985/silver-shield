"""ADR-0032 Slice E 接线契约测试。

覆盖依赖倒置钩子的三层不变式：

1. **注册表语义**（E1–E4）：内建类型不可劫持、重复注册需显式 ``replace``、
   ``unregister`` 可清理、``registered_source_types`` 只读快照。
2. **零行为变化**（E5–E6）：不注册时 ``build_frame_source`` 分发与今天完全一致；
   注册只影响新 ``source_type``。
3. **依赖方向**（E7–E9）：``silver_demo`` 不 import ``validation``，
   ``demo_adapter`` 不 import ``silver_demo``，且 demo_adapter 保持 torch-free。

依赖方向断言用 AST（``_ast_contract``）而非源码子串扫描——后者会被文档字符串里
出现的模块名误伤（本文件与 ``demo_adapter`` 的文档里都大量提及 ``silver_demo``）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from _ast_contract import assert_no_dependency, imported_modules

ROOT = Path(__file__).resolve().parents[2]
SILVER_DEMO_SRC = ROOT / "src" / "silver_demo"

FIXTURE_SCENARIO = (
    "src/home_perception/validation/fixtures/scenarios/perception/torchfree_visit.yaml"
)


# ============================================================================
# 共用 stub / fixture
# ============================================================================


class _StubScenarioConfig:
    """鸭子类型 ScenarioConfig（避免为纯注册表测试拉起 pydantic 模型）。"""

    def __init__(self, source_type: str, synthetic: dict | None = None) -> None:
        self.scenario_id = "stub_scenario"
        self.source = "stub_source"
        self.source_type = source_type
        self.media_path = None
        self.fps_target = 4
        self.synthetic = synthetic


@pytest.fixture
def sources_mod():
    """导入 silver_demo.sources 并在用例后清空外部注册表（防跨用例污染）。"""
    from silver_demo import sources

    before = set(sources.registered_source_types())
    yield sources
    for st in set(sources.registered_source_types()) - before:
        sources.unregister_frame_source(st)


# ============================================================================
# E1–E4：注册表语义
# ============================================================================


@pytest.mark.parametrize("builtin", ["video_file", "caviar_jpg"])
def test_adr0032_e1_builtin_source_types_cannot_be_hijacked(sources_mod, builtin) -> None:
    """内建 source_type 禁止被注册覆盖（即使显式 replace=True 也不行）。"""

    def _evil(scenario, hp_settings):  # pragma: no cover - 不应被调用
        raise AssertionError("内建帧源被劫持")

    with pytest.raises(ValueError, match="内建类型"):
        sources_mod.register_frame_source(builtin, _evil)
    # replace=True 同样不能突破——这是安全边界，不是便利开关
    with pytest.raises(ValueError, match="内建类型"):
        sources_mod.register_frame_source(builtin, _evil, replace=True)
    assert builtin not in sources_mod.registered_source_types()


def test_adr0032_e2_duplicate_registration_requires_replace(sources_mod) -> None:
    """重复注册同一外部类型默认拒绝；显式 replace=True 才顶替。"""

    def _a(scenario, hp_settings):
        return "A"

    def _b(scenario, hp_settings):
        return "B"

    sources_mod.register_frame_source("unit_test_src", _a)
    with pytest.raises(ValueError, match="已注册"):
        sources_mod.register_frame_source("unit_test_src", _b)

    # 未被静默顶替：仍是 _a
    cfg = _StubScenarioConfig("unit_test_src")
    assert sources_mod.build_frame_source(cfg, hp_settings=None) == "A"

    sources_mod.register_frame_source("unit_test_src", _b, replace=True)
    assert sources_mod.build_frame_source(cfg, hp_settings=None) == "B"


def test_adr0032_e3_unregister_is_idempotent(sources_mod) -> None:
    """unregister 可清理，且对不存在的类型幂等（便于测试 teardown）。"""

    def _a(scenario, hp_settings):
        return "A"

    sources_mod.register_frame_source("unit_test_src", _a)
    assert "unit_test_src" in sources_mod.registered_source_types()

    sources_mod.unregister_frame_source("unit_test_src")
    assert "unit_test_src" not in sources_mod.registered_source_types()
    # 幂等：重复注销不抛
    sources_mod.unregister_frame_source("unit_test_src")
    sources_mod.unregister_frame_source("never_registered")


def test_adr0032_e4_registered_source_types_is_readonly_snapshot(sources_mod) -> None:
    """registered_source_types 返回快照，改它不影响内部状态。"""

    def _a(scenario, hp_settings):
        return "A"

    sources_mod.register_frame_source("unit_test_src", _a)
    snap = sources_mod.registered_source_types()
    assert isinstance(snap, frozenset)

    # 快照是「当时」的值：之后注销不改变已取到的 snap
    sources_mod.unregister_frame_source("unit_test_src")
    assert "unit_test_src" in snap
    assert "unit_test_src" not in sources_mod.registered_source_types()


# ============================================================================
# E5–E6：零行为变化
# ============================================================================


def test_adr0032_e5_unknown_source_type_still_falls_back_to_caviar(sources_mod) -> None:
    """未注册的未知 source_type 仍走 CAVIAR 兜底——与接缝引入前行为一致。

    这条锁的是「纯加法」承诺：注册表只新增分支，不改既有分发语义。
    """

    class _StubHP:
        class runtime:  # 仿 Settings 命名空间
            caviar_base_dir = str(ROOT / "tests" / "fixtures" / "doorway")
            frame_glob = "frame_*.jpg"

    cfg = _StubScenarioConfig("some_unknown_type")
    cfg.source = "one_leave_reenter"
    src = sources_mod.build_frame_source(cfg, _StubHP())
    assert type(src).__name__ == "CaviarJpgFrameSource"


def test_adr0032_e6_registration_does_not_disturb_builtin_dispatch(sources_mod, tmp_path) -> None:
    """注册外部类型后，内建 video_file 分发不受影响。"""

    def _a(scenario, hp_settings):  # pragma: no cover - 不应被 video_file 命中
        raise AssertionError("外部 builder 抢走了 video_file")

    sources_mod.register_frame_source("unit_test_src", _a)

    mp4 = tmp_path / "x.mp4"
    mp4.write_bytes(b"not-a-real-mp4")
    cfg = _StubScenarioConfig("video_file")
    cfg.media_path = str(mp4)
    src = sources_mod.build_frame_source(cfg, hp_settings=None)
    assert type(src).__name__ == "VideoFileFrameSource"


# ============================================================================
# E7–E9：依赖方向
# ============================================================================


def test_adr0032_e7_silver_demo_does_not_import_validation() -> None:
    """silver_demo 全包不得直接 import home_perception.validation。

    这正是选用依赖倒置而非放宽白名单的目的；与
    ``tests/demo/test_freeze_boundary.py`` 互为双保险（那边守白名单全集，
    这边专钉 validation 这一条，失败信息更直指 Slice E）。
    """
    offenders: list[str] = []
    for py in sorted(SILVER_DEMO_SRC.rglob("*.py")):
        mods = imported_modules(py.read_text(encoding="utf-8"))
        hits = {m for m in mods if m.startswith("home_perception.validation")}
        if hits:
            offenders.append(f"{py.relative_to(ROOT)}: {sorted(hits)}")
    assert not offenders, (
        "silver_demo 不得直接依赖 validation（应经 register_frame_source 钩子）\n  - "
        + "\n  - ".join(offenders)
    )


def test_adr0032_e8_demo_adapter_does_not_import_silver_demo() -> None:
    """demo_adapter 不得 import silver_demo（避免 home_perception 反向依赖 demo）。"""
    from home_perception.validation import demo_adapter

    assert_no_dependency(
        demo_adapter,
        forbidden_modules=["silver_demo"],
        forbidden_names=["ScenarioConfig", "DemoFrameSource", "build_frame_source"],
    )


def test_adr0032_e9_demo_adapter_is_torch_free() -> None:
    """导入 demo_adapter 不得拉起 torch / ultralytics（守 run_demo 的预检次序）。

    必须在**全新解释器**里验证：本测试进程可能已因其他用例加载过 torch。
    """
    code = (
        "import sys\n"
        f"sys.path.insert(0, r'{ROOT / 'src'}')\n"
        "import home_perception.validation.demo_adapter\n"
        "assert 'torch' not in sys.modules, 'demo_adapter 拉起了 torch'\n"
        "assert 'ultralytics' not in sys.modules, 'demo_adapter 拉起了 ultralytics'\n"
        "print('OK')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        check=False,
    )
    assert proc.returncode == 0, f"demo_adapter torch-free 断言失败：\n{proc.stderr}"
    assert "OK" in proc.stdout


# ============================================================================
# E10–E12：合成源端到端
# ============================================================================


def test_adr0032_e10_synthetic_source_yields_frames(sources_mod) -> None:
    """经注册钩子接线后，synthetic 源端到端产出 (timestamp, frame)。"""
    from home_perception.validation.demo_adapter import install_into

    install_into(sources_mod.register_frame_source)
    assert "synthetic" in sources_mod.registered_source_types()

    cfg = _StubScenarioConfig("synthetic", synthetic={"scenario_path": FIXTURE_SCENARIO})
    src = sources_mod.build_frame_source(cfg, hp_settings=None)

    assert src.frame_count > 0
    frames = list(src)
    assert len(frames) == src.frame_count

    ts, frame = frames[0]
    assert isinstance(ts, float)
    # BGR 三通道图像
    assert frame.ndim == 3
    assert frame.shape[2] == 3

    # 支持 loop 重放：再次迭代仍产出同样帧数
    assert len(list(src)) == src.frame_count


def test_adr0032_e11_synthetic_missing_config_raises_actionable_error(sources_mod) -> None:
    """synthetic 配置缺失时报可操作错误，而非静默降级或 AttributeError。"""
    from home_perception.validation.demo_adapter import build_synthetic_frame_source

    with pytest.raises(ValueError, match="ScenarioConfig.synthetic"):
        build_synthetic_frame_source(_StubScenarioConfig("synthetic"), None)

    with pytest.raises(ValueError, match="scenario_path"):
        build_synthetic_frame_source(
            _StubScenarioConfig("synthetic", synthetic={"mode": "frames"}), None
        )

    with pytest.raises(FileNotFoundError):
        build_synthetic_frame_source(
            _StubScenarioConfig("synthetic", synthetic={"scenario_path": "no/such/file.yaml"}),
            None,
        )


def test_adr0032_e12_scenario_config_accepts_synthetic_field() -> None:
    """真实 ScenarioConfig 接受 optional synthetic 字段，且默认 None（既有 YAML 不受影响）。"""
    from datetime import UTC, datetime

    from silver_demo.scenarios import ScenarioConfig

    base = ScenarioConfig(
        scenario_id="s", source="src", start_time=datetime(2026, 7, 19, tzinfo=UTC)
    )
    assert base.synthetic is None

    with_syn = ScenarioConfig(
        scenario_id="s",
        source="src",
        source_type="synthetic",
        start_time=datetime(2026, 7, 19, tzinfo=UTC),
        synthetic={"scenario_path": FIXTURE_SCENARIO},
    )
    assert with_syn.synthetic == {"scenario_path": FIXTURE_SCENARIO}
