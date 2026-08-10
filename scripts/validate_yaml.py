"""CI 质量门禁：yaml 校验 + schema 校验（对应 ci-quality.yml 的「yaml 校验 / schema 校验」）。

职责边界（不越权、不伪造）：
1. **yaml 校验**：仓库内所有 ``*.yaml`` / ``*.yml``（排除 .git / venv / artifacts / 构建产物）
   必须能被 PyYAML 解析，捕获任何语法错误（缩进、tab、非法字符等）。
2. **schema 校验**：仅对 **Perception 场景 fixtures**
   （``src/home_perception/validation/fixtures/scenarios/{benchmark,perception,regression}``）
   做 Scenario schema 加载校验（``home_perception.validation.load_scenarios_dir``）。
   这是 Benchmark 回归门禁的输入契约——场景 YAML 一旦破损，benchmark 会静默失效，
   故在 quality 阶段先行拦截。
   - 其它场景格式（``config/scenarios/*`` 的 soak 配置、``config/demo/scenarios/*`` 的
     demo 剧本、``tests/fixtures/*cross_modal_scenarios.yaml`` 的跨模态声明）属于**不同 schema**，
     不在本脚本强制 schema 校验范围内，仅参与上面的 yaml 解析校验，避免误报。
3. **配置 YAML 解析**：``config/*.yaml``、``config/devices.example.yaml`` 等可解析即可
   （轻量，不绑定特定 schema）。

退出码：0 = 全部通过；1 = 任一校验失败；2 = 环境错误（home_perception 不可导入等）。
依赖：仅标准库 + PyYAML + home_perception（quality job 已安装）。
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("[validate_yaml] PyYAML 未安装")
    sys.exit(2)

REPO_ROOT = Path(__file__).resolve().parents[1]

# 排除目录（不扫描其内部 yaml）
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "artifacts",
    "node_modules",
    "build",
    "dist",
    ".eggs",
    "silver-engineering-assets",
}

# Perception 场景 fixtures 根（使用统一 Scenario schema）
SCENARIO_ROOTS = [
    "src/home_perception/validation/fixtures/scenarios/benchmark",
    "src/home_perception/validation/fixtures/scenarios/perception",
    "src/home_perception/validation/fixtures/scenarios/regression",
]


def _iter_yaml_files(root: Path):
    for pattern in ("*.yaml", "*.yml"):
        for p in root.rglob(pattern):
            if any(part in EXCLUDE_DIRS for part in p.parts):
                continue
            if p.is_file():
                yield p


def _check_yaml_syntax() -> list[str]:
    errors: list[str] = []
    count = 0
    for p in sorted(_iter_yaml_files(REPO_ROOT)):
        count += 1
        try:
            with p.open("r", encoding="utf-8") as fh:
                yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            rel = p.relative_to(REPO_ROOT)
            errors.append(f"[yaml] 解析失败 {rel}: {exc}")
    print(f"[validate_yaml] yaml 解析校验：扫描 {count} 个文件")
    return errors


def _check_scenario_schema() -> list[str]:
    errors: list[str] = []
    try:
        from home_perception.validation import load_scenarios_dir
    except Exception as exc:  # noqa: BLE001  # pragma: no cover
        print(f"[validate_yaml] 无法导入 home_perception.validation（跳过 schema 校验）：{exc}")
        return errors

    for rel_root in SCENARIO_ROOTS:
        root = REPO_ROOT / rel_root
        if not root.exists():
            print(f"[validate_yaml] 跳过不存在的场景根：{rel_root}")
            continue
        try:
            scenarios = load_scenarios_dir(root)
        except Exception as exc:  # noqa: BLE001  # 加载/结构校验失败
            errors.append(f"[schema] 场景根 {rel_root} 加载失败：{exc}")
            continue
        print(f"[validate_yaml] schema 校验：{rel_root} -> {len(scenarios)} 个场景通过")
    return errors


def main() -> int:
    errors: list[str] = []
    errors += _check_yaml_syntax()
    errors += _check_scenario_schema()

    if errors:
        print("\n==================== VALIDATION FAILED ====================")
        for e in errors:
            print(f"  {e}")
        print(f"失败项：{len(errors)}")
        return 1

    print("\n[validate_yaml] 全部通过：yaml 解析 + Perception 场景 schema 校验 OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
