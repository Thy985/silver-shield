"""CI 测试入口（单一事实来源，避免测试路径散落进 workflow YAML）。

设计原则（对应本次 CI 治理「测试入口脚本」要求）：
- YAML 只负责「调哪个入口 + 上传什么产物」，不写具体 `pytest <长路径列表>`；
- 测试路径的归属（contract / unit / integration）集中在本文件，改测试布局只动这里；
- 每个 tier 统一产出 JUnit XML + coverage XML 到 ``artifacts/``，供 CI 上传留痕
  （对应「CI 必须有 Artifact 能力」：失败后能下载证据）。

Tier 语义：
- ``contract`` ：接口 / 状态机 / schema / 配置等契约测试（torch-free，最轻）。
- ``unit``     ：组件单测（DecisionEngine / Memory / Policy / Tracker / Rule / Warning …），
                不依赖 AI 栈（torch-free），证明「单测不绑 torch」。
- ``integration`` ：**真实闭环**测试（Scenario→Tracker→Decision→Policy→Action 端到端），
                走 detections 通道（合成 detector，零模型），不拉真实 YOLO；但确实跑通了
                整条决策链路，证明「闭环必须有人跑」（ADR-0034 前置禁令）。
                **torch-free**：仅跑不依赖 torch 的闭环用例（合成 detector 路径），
                不触发任何 YOLO/torch 加载。需要全量 AI 栈的闭环测试见 ``all`` / ci-runtime。
- ``benchmark`` ：ADR-0033 回归门禁用例（Gate / AB-Runner / Metrics / Bump / 音频 recorder）。
                **需要 torch**（baseline 指纹 provenance.runtime_dependencies 含 torch 2.11.0，
                须一致才过 4/7 守恒），由 ci-benchmark 装 requirements-ci-ai.txt 后运行；
                另由 ci-benchmark 单独跑 ``scripts/run_benchmark.py --gate`` 做端到端冒烟。
- ``all``      ：完整套件（默认 pytest discovery，含需要 AI 栈 / 真实 YOLO 的测试），
                需先装 requirements-ci-ai.txt，仅 ci-runtime（main / dispatch）运行。

用法：
    python scripts/run_tests.py --tier contract
    python scripts/run_tests.py --tier unit --artifacts-dir artifacts
    python scripts/run_tests.py --tier integration -q
    python scripts/run_tests.py --tier benchmark
    python scripts/run_tests.py --list
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# —— 测试路径归属（单一事实来源）——
# contract / unit 必须 torch-free（CI 用 requirements-ci.txt 安装，无 torch/ultralytics）。
# integration 走 detections 通道（合成 detector，零模型），同样 torch-free。
TIERS: dict[str, list[str]] = {
    "contract": [
        "tests/contract/test_config_contract.py",
        "tests/contract/test_state_machine_contract.py",
        "tests/contract/test_schema_contract.py",
        "tests/contract/test_interface_contract.py",
        "tests/contract/test_input_attack_contract.py",
        "tests/demo/test_dashboard_p0_11_4.py",
        "tests/demo/test_dashboard_state_layer.py",
        "tests/demo/test_dashboard_video_input.py",
        "tests/demo/test_freeze_boundary.py",
    ],
    "unit": [
        # analysis：决策引擎 / 策略 / 行为 / 信号（纯 python）
        "tests/analysis/test_behavior_builder.py",
        "tests/analysis/test_behavior_state.py",
        "tests/analysis/test_decision_contract.py",
        "tests/analysis/test_decision_engine_trace.py",
        "tests/analysis/test_decision_sink.py",
        "tests/analysis/test_decision_trace.py",
        "tests/analysis/test_evaluator_snapshot_roundtrip.py",
        "tests/analysis/test_realtime_evaluator.py",
        "tests/analysis/test_recent_behavior_eviction.py",
        "tests/analysis/test_recent_behavior_store.py",
        "tests/analysis/test_signal_adapter.py",
        # memory：存储 / 回放 / 跨模态 / 消费者 / 评估（纯 python）
        "tests/memory/test_cold_start_recovery.py",
        "tests/memory/test_cross_modal_explainer.py",
        "tests/memory/test_cross_modal_link.py",
        "tests/memory/test_cross_modal_runtime.py",
        "tests/memory/test_episode_builder.py",
        "tests/memory/test_evidence_item.py",
        "tests/memory/test_memory_evaluation.py",
        "tests/memory/test_memory_query.py",
        "tests/memory/test_memory_replay.py",
        "tests/memory/test_memory_replay_audio.py",
        "tests/memory/test_memory_replay_cross_modal.py",
        "tests/memory/test_memory_replay_dataset.py",
        "tests/memory/test_policy_abc.py",
        "tests/memory/test_records.py",
        "tests/memory/test_short_term_policy.py",
        "tests/memory/test_snapshot_persistence.py",
        "tests/memory/test_store.py",
        "tests/memory/consumer/test_aggregation.py",
        "tests/memory/consumer/test_audio_patterns.py",
        "tests/memory/consumer/test_context.py",
        "tests/memory/consumer/test_invariants.py",
        "tests/memory/consumer/test_orchestrator.py",
        "tests/memory/consumer/test_reasoning.py",
        "tests/memory/consumer/test_retrieval.py",
        "tests/memory/evaluation/test_ab_runner.py",
        "tests/memory/evaluation/test_e1_ab.py",
        "tests/memory/evaluation/test_ground_truth.py",
        "tests/memory/evaluation/test_metrics.py",
        "tests/memory/evaluation/test_report.py",
        "tests/memory/evaluation/test_temporal.py",
        # 根级组件单测（action / event / feature / rule / tracker / warning / risk）
        "tests/test_action.py",
        "tests/test_config.py",
        "tests/test_event.py",
        "tests/test_feature.py",
        "tests/test_rule.py",
        "tests/test_tracker.py",
        "tests/test_warning.py",
        "tests/test_risksignal_contract.py",
        # 音频单测（numpy 确定性合成，零模型）
        "tests/test_audio_perception.py",
        "tests/test_audio_scenarios.py",
        "tests/test_audio_synthetic.py",
        "tests/test_audio_tier1.py",
        "tests/test_audio_units.py",
        # ADR-0034 Phase C DoD 治理纯函数（torch-free，无 cv2 依赖）：
        # - test_integration_baseline_check.py：loop 指纹基线漂移/文件变更策略 22 项（DoD C4 守卫本体）；
        # - test_run_integration_validation_helpers.py：_runtime_provenance 等脚本助手 4 项（DoD C7）。
        "tests/evaluation/test_integration_baseline_check.py",
        "tests/evaluation/test_run_integration_validation_helpers.py",
        # ADR-0035 D1 Evidence Explorer（纯 stdlib：json/html/AST 扫描，无 cv2/torch）：
        # loader fail-closed 投影契约 / renderer 确定性+脱敏+四视图 / AST 零 import 生产类。
        "tests/visualizer/test_loader.py",
        "tests/visualizer/test_renderer.py",
        "tests/visualizer/test_ast_contract.py",
        "tests/visualizer/test_cli.py",
    ],
    "integration": [
        # 运行时闭环（合成 detector 零模型，**torch-free**）：证明 Scenario→Tracker→
        # Decision→Policy→Action 真实跑通，满足「闭环必须有人跑」（ADR-0034 前置禁令）。
        "tests/test_integration.py",
        "tests/runtime/test_memory_closure_slice_b.py",
        "tests/runtime/test_memory_consumer_hook.py",
        "tests/runtime/test_memory_e2e_closed_loop.py",
        "tests/runtime/test_pipeline_cold_start.py",
        "tests/runtime/test_pipeline_memory_consumer.py",
        "tests/runtime/test_pipeline_memory_stage_f.py",
        "tests/runtime/test_runtime_cross_modal_e2e.py",
        # 音频 recorder（import 链 transitively 需 cv2，已在 requirements-ci.txt 钉死，
        # 不依赖 torch）
        "tests/runtime/test_audio_session_recorder.py",
        # ADR-0034 Phase A–C 验收测试（DoD C6 failure injection / C3 severity / C8 生产边界）：
        # 合成 detector 零模型 + torch-free（无 torch import，直接与传递均无）；e2e 需要 cv2
        # （requirements-ci.txt 已钉死 opencv-python==4.13.0.92）。含：
        # - test_integration_failure_contract.py：F2/F3/F5 注入 + 互不干扰 + observability 正例
        #   （DoD C6：防止未来有人改 Validator 恒 passed 而 CI 仍绿）；
        # - test_adr0034_phase_c_gate.py：severity 分级 / F6 不可降级 / 指纹联动（DoD C3）；
        # - test_adr0034_phase_a.py：含 test_t2_production_does_not_import_loop_package
        #   （DoD C8：loop 进不了生产 = ADR-0033 D8 豁免的依赖前提）。
        "tests/integration/test_adr0034_phase_a.py",
        "tests/integration/test_adr0034_phase_b1.py",
        "tests/integration/test_adr0034_phase_b2.py",
        "tests/integration/test_adr0034_phase_b2_audio.py",
        "tests/integration/test_adr0034_phase_b3_fingerprint.py",
        "tests/integration/test_adr0034_phase_c_gate.py",
        "tests/integration/test_integration_failure_contract.py",
    ],
    "benchmark": [
        # ADR-0033 回归门禁（**需要 torch**：baseline 指纹含 torch 2.11.0，须守恒 4/7）。
        # 由 ci-benchmark 装 requirements-ci-ai.txt 后运行；另由该 workflow 单独跑
        # scripts/run_benchmark.py --gate 做端到端冒烟。
        "tests/evaluation/test_benchmark_ab_runner.py",
        "tests/evaluation/test_benchmark_gate.py",
        "tests/evaluation/test_benchmark_harness.py",
        "tests/evaluation/test_benchmark_metrics.py",
        "tests/evaluation/test_baseline_bump_check.py",
        "tests/runtime/test_audio_session_recorder.py",
    ],
}


def _default_artifacts_dir() -> Path:
    # 仓库根（脚本在 scripts/ 下，上一层即根）
    return Path(__file__).resolve().parents[1] / "artifacts"


def _build_pytest_args(tier: str, artifacts_dir: Path, extra: list[str]) -> list[str]:
    if tier == "all":
        targets: list[str] = []  # 空 = 跑 pytest 默认 discovery（全量，含 AI 栈测试）
    else:
        targets = TIERS.get(tier, [])
        if not targets:
            raise SystemExit(f"[run_tests] 未知 tier: {tier}（可用: {', '.join(TIERS)} / all）")

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", "pytest", *targets]
    # 产物：JUnit（CI 上传留痕）+ coverage XML（失败可溯源覆盖）
    args += [
        f"--junitxml={artifacts_dir / f'junit-{tier}.xml'}",
        "--cov=home_perception",
        f"--cov-report=xml:{artifacts_dir / f'coverage-{tier}.xml'}",
        "--cov-report=term-missing",
    ]
    args += extra
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SilverShield CI 测试入口")
    parser.add_argument(
        "--tier",
        required=False,
        default="all",
        choices=["contract", "unit", "integration", "benchmark", "all"],
        help="测试分层：contract/unit/integration（torch-free）/ benchmark（需 torch）/ all（全量，需 AI 栈）；--list 时可选",
    )
    parser.add_argument(
        "--artifacts-dir",
        default=str(_default_artifacts_dir()),
        help="JUnit / coverage 产物落盘目录（默认 <repo>/artifacts）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="仅打印各 tier 的测试路径后退出（不执行）",
    )
    # 其余未知参数透传给 pytest（如 -q / -k / --no-cov）
    parsed, extra = parser.parse_known_args(argv)

    if parsed.list:
        for t, paths in TIERS.items():
            print(f"# tier: {t} ({len(paths)} files)")
            for p in paths:
                print(f"  {p}")
        return 0

    args = _build_pytest_args(parsed.tier, Path(parsed.artifacts_dir), extra)
    print(f"[run_tests] tier={parsed.tier} -> {' '.join(args)}", flush=True)
    result = subprocess.run(args, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
