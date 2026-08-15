"""ADR-0036 P0-4.1 · Trusted Case Factory 编排测试（hermetic，不跑真实闭环 pipeline）。

通过 monkeypatch 两个重型依赖，验证 Factory 自身的编排契约：
- ``run_integration_validation.main``（torch/cv2 闭环）替换为轻量桩，仅产出合法 canonical 树；
- ``run_case_viewer.main``（CLI 入口）替换为薄封装，**仍调用真实 ``render_case_viewer``**，
  保证「CI descriptor（generated_by="ci"）→ 真实渲染器 → case_viewer.html 含可信徽章」链路被覆盖。

验证项：
- 产出自描述包（manifest / provenance / fingerprints / gate / case_viewer / canonical）；
- manifest.generated_by == "ci"、case_ids 非空、contents.canonical_reports 完整、bundle hash 64 位；
- provenance 记录运行血缘；gate.json 由 summary 聚合得出 passed=True；
- 真实渲染产出 HTML 含 CI 受控生成徽章（P0-4.2 端到端闭环）。

不依赖真实模型/运行时，CI 与本地一致、快速、可复现。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_SID = "sw_t1"
_STAGES = ["perception", "decision", "notification", "memory", "cross_modal", "observability"]


def _write_d3a_canonical(out: Path, sid: str, *, gate_passed: bool = True, gate_degraded: bool = False) -> None:
    """在 ``out`` 直接写出 D3A 形状的合法 artifact 树（loader 投影契约对齐）。"""
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{sid}.canonical.json").write_text(
        json.dumps(
            {
                "ok": True,
                "mode": "frames",
                "n_frames": 10,
                "scenario_fingerprint": "fp_test_001",
                "stages": [{"name": s, "passed": True, "failure_code": None} for s in _STAGES],
                "artifacts": {
                    "counts": {
                        "perception_events": 1,
                        "warnings": 1,
                        "commands": 1,
                        "sink_commands": 1,
                        "decision_traces": 1,
                        "episodes": 1,
                        "cross_modal_links": 0,
                    },
                    "event_types": ["abnormal_dwell"],
                    "risk_levels": ["LOW"],
                    "recommended_actions": ["NOTIFY_FAMILY"],
                    "command_types": ["LOG_ONLY"],
                    "trace_outcome_kinds": ["WARN"],
                    "suppress_reasons": [],
                    "episode_action_command_types": ["LOG_ONLY"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / f"{sid}.gate.json").write_text(
        json.dumps(
            {
                "verdicts": [
                    {"name": s, "passed": True, "severity": "blocking", "failure_code": None}
                    for s in _STAGES
                ],
                "passed": gate_passed,
                "degraded": gate_degraded,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / f"{sid}.fingerprints.json").write_text(
        json.dumps(
            {"expectation_fingerprint": "e1", "loop_fingerprint": "l1"},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "adr0034_summary.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-08-15T00:00:00Z",
                "scenarios_dir": str(out),
                "scenarios": [
                    {
                        "scenario_id": sid,
                        "path": "src/.../x.yaml",
                        "ok": True,
                        "failure_codes": [],
                        "gate": {
                            "passed": gate_passed,
                            "degraded": gate_degraded,
                            "path": str(out / f"{sid}.gate.json"),
                        },
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out / "adr0034_fingerprints.json").write_text(
        json.dumps(
            {"scenarios": [{sid: {"expectation_fingerprint": "e1", "loop_fingerprint": "l1"}}]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _fake_iv_main(argv: list[str]) -> int:
    """轻量桩：替代重型闭环 pipeline，仅产出合法 canonical 树（P0-4.1 编排验证用）。"""
    od = Path(argv[argv.index("--out-dir") + 1])
    _write_d3a_canonical(od, _SID)
    return 0


def _fake_cv_main(argv: list[str]) -> int:
    """薄封装：替代 CLI 入口，但调用真实 ``render_case_viewer``（保留 P0-4.2 徽章链路覆盖）。

    不依赖 CLI 模块的重导入路径，规避 pytest-asyncio AUTO 模式下的进程内死锁；
    真实渲染器已被 P0-4.2 单测覆盖，此处验证 Factory 把 CI descriptor 正确喂给渲染器。
    复用真实 CLI 的 fail-closed 契约：投影/编排违规 → 返回 1（不抛，交由 Factory 传播退出码）。
    """
    from home_perception.visualizer.viewer import (
        EvidenceProjectionError,
        load_case_presentation,
        render_case_viewer,
    )

    arts = argv[argv.index("--artifacts") + 1]
    desc = argv[argv.index("--descriptor") + 1]
    out = argv[argv.index("--output") + 1]
    try:
        projection, descriptor = load_case_presentation(arts, descriptor_path=desc)
    except (FileNotFoundError, EvidenceProjectionError, ValueError):
        return 1
    html = render_case_viewer(
        projection, descriptor, media_base_dir=arts, media_base_url=""
    )
    Path(out).write_text(html, encoding="utf-8")
    return 0


def _load_factory():
    spec = importlib.util.spec_from_file_location(
        "build_trusted_case_test",
        str(_REPO_ROOT / "scripts" / "build_trusted_case.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def patched_factory(monkeypatch):
    """把重型闭环与运行血缘、以及 CLI 入口替换为确定性桩，并加载 Factory 模块（脚本，非包）。"""
    import scripts.run_integration_validation as _iv_mod

    monkeypatch.setattr(_iv_mod, "main", _fake_iv_main)
    monkeypatch.setattr(
        _iv_mod,
        "_runtime_provenance",
        lambda: {
            "code_version": "testcafe",
            "python": "3.13",
            "numpy": "n/a",
            "opencv-python": "n/a",
            "torch": "n/a",
        },
    )
    import scripts.run_case_viewer as _cv_mod

    monkeypatch.setattr(_cv_mod, "main", _fake_cv_main)
    return _load_factory()


def test_factory_produces_self_describing_package(tmp_path, patched_factory):
    """P0-4.1：Factory 产出完整自描述包，且 manifest / provenance / gate / CI 徽章齐备。"""
    out = tmp_path / "demo"
    rc = patched_factory.main(["--out-dir", str(out)])
    assert rc == 0, "Factory 应成功返回 0"

    # 核心产物齐全
    for name in (
        "manifest.json",
        "provenance.json",
        "fingerprints.json",
        "gate.json",
        "case_viewer.html",
        "case_descriptor.ci.json",
    ):
        assert (out / name).exists(), f"缺产物 {name}"

    # canonical 子目录 + 单场景 canonical 报告
    canon = out / "canonical"
    assert canon.is_dir()
    assert (canon / f"{_SID}.canonical.json").exists(), "缺 canonical 报告"

    # manifest 契约
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["generated_by"] == "ci"
    assert manifest["renderer_version"] == "1.0.0"
    assert manifest["case_ids"] == [_SID]
    assert manifest["scenario_count"] == 1
    assert manifest["gate_passed"] is True
    assert len(manifest["artifact_bundle_hash"]) == 64, "包完整性哈希须为 64 位 sha256"
    contents = manifest["contents"]
    assert contents["canonical_reports"][_SID].endswith(f"{_SID}.canonical.json")
    assert contents["gate"] == "gate.json"
    assert contents["provenance"] == "provenance.json"
    assert contents["fingerprints"] == "fingerprints.json"

    # provenance 运行血缘
    prov = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert prov["generated_by"] == "ci"
    assert prov["code_version"] == "testcafe"
    assert prov["renderer_version"] == "1.0.0"

    # gate 聚合（由 summary 真实派生）
    gate = json.loads((out / "gate.json").read_text(encoding="utf-8"))
    assert gate["passed"] is True and gate["degraded"] is False
    assert gate["scenarios"][0]["scenario_id"] == _SID

    # 真实渲染路径产出的 HTML 含 CI 受控生成徽章（P0-4.2 端到端闭环）
    html = (out / "case_viewer.html").read_text(encoding="utf-8")
    assert "本案例由 CI 受控生成" in html
    assert "ci-badge" in html


def test_factory_propagates_gate_failure_exit_code(tmp_path, monkeypatch):
    """P0-4.1：闭环门禁 blocking 失败时，即便产出 demo 包，工厂退出码仍非 0（CI 拦合并）。

    真实场景中门禁失败（iv_rc != 0）**仍会写出 canonical artifact**（否则无法排障），
    故 Factory 继续渲染、写 manifest，最终返回 iv_rc（非 0）。本桩模拟该真实路径：
    先写出合法 canonical 树，再返回 1（门禁失败）。
    """

    def _fake_iv_fail(argv: list[str]) -> int:
        od = Path(argv[argv.index("--out-dir") + 1])
        _write_d3a_canonical(od, _SID, gate_passed=False, gate_degraded=True)
        return 1  # 模拟门禁 blocking 失败

    import scripts.run_integration_validation as _iv_mod

    monkeypatch.setattr(_iv_mod, "main", _fake_iv_fail)
    monkeypatch.setattr(
        _iv_mod,
        "_runtime_provenance",
        lambda: {"code_version": "x", "python": "3.13", "numpy": "n/a",
                 "opencv-python": "n/a", "torch": "n/a"},
    )
    import scripts.run_case_viewer as _cv_mod

    monkeypatch.setattr(_cv_mod, "main", _fake_cv_main)
    mod = _load_factory()
    out = tmp_path / "demo_fail"
    rc = mod.main(["--out-dir", str(out)])
    assert rc != 0, "门禁失败须传播为非 0 退出码"
    # 但 demo 包仍产出供排障（manifest 含 gate_passed=False 信号）
    assert (out / "manifest.json").exists()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["gate_passed"] is False
