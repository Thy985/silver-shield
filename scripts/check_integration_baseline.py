"""ADR-0034 Phase C · loop 指纹基线漂移检测（DoD C4）。

两枚闭环指纹回答「用什么标准评价（expectation_fingerprint）+ 这次怎么跑的
（loop_fingerprint）」。基线（``loop_fingerprints.json``）是"上一次 Owner 评审认可的
指纹快照"——它把"运行血缘"变成**可比较、可审计**的对象。

本脚本比较当前运行（``run_integration_validation.py`` 产出的 ``adr0034_fingerprints.json``）
与仓库内基线，按 **bump 标记** 分类裁决：

| 情形 | 裁决 |
|---|---|
| 无基线文件（首次） | 通过（提示用 ``--init-baseline`` 生成）——不存在可比对象 |
| 指纹全部一致 | 通过（"无漂移"） |
| 有漂移 + PR 注明 ``integration-baseline-bump`` | 通过（有意识变更；基线须在 PR 内一并更新 + Owner 评审） |
| 有漂移 + 缺标记 | **拦截**——"Fingerprint drift without baseline update"（改了一行 policy / 期望版本却让指纹变，必须显式声明，否则合并就是盲合） |
| 基线文件被 PR 修改（增/删/改）却缺标记 | **拦截**（防绕过，类比 ADR-0033 ``check_baseline_bump.py``） |

退出码：0 = 合规；1 = 漂移 / 基线变更缺标记（CI 拦截）；2 = 输入 / 环境错误。
依赖零第三方库（仅标准库），与 ``scripts/check_baseline_bump.py`` 同款轻量风格。
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

# PR 描述 / 提交信息须包含此标记，声明「本次指纹基线变更经 Owner 评审」（DoD C4）。
BUMP_MARKER = "integration-baseline-bump"
# 基线 JSON 落点（相对仓库根；与 run_integration_validation.py 的指纹产物对称）。
BASELINES_REL = "src/home_perception/integration/fixtures/baselines"
BASELINE_FILENAME = "loop_fingerprints.json"


def _norm_rel(path: str) -> str:
    """归一化为 POSIX 风格相对路径（跨平台一致比对）。"""
    return path.replace("\\", "/").strip()


# ---------------------------------------------------------------------------
# 纯函数（可单测、可变异验证；不依赖 git / 网络）
# ---------------------------------------------------------------------------


def load_fingerprints(path: str | Path) -> dict[str, dict[str, str]]:
    """读指纹 JSON 的 ``scenarios`` 字段（{scenario_id: {expectation, loop}_fingerprint}）。

    文件缺失抛 ``FileNotFoundError``；JSON 非法抛 ``ValueError``（json.loads）；
    结构类型不对抛 ``TypeError``（fail-closed）。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"指纹文件不存在：{p}")
    data = json.loads(p.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, dict):
        raise TypeError(f"{p} 缺合法的 scenarios 字典字段（fail-closed）")
    out: dict[str, dict[str, str]] = {}
    for sid, fp in scenarios.items():
        if not isinstance(fp, dict) or not isinstance(sid, str):
            raise TypeError(f"{p} 的 scenarios[{sid!r}] 结构非法（fail-closed）")
        out[sid] = {str(k): str(v) for k, v in fp.items()}
    return out


def drift_details(
    current: dict[str, dict[str, str]],
    baseline: dict[str, dict[str, str]],
) -> dict[str, list[str]]:
    """逐场景比较两枚指纹，返回漂移详情 {scenario_id: [变更的指纹字段]}。

    只比较**指纹值**，不比较生成时间 / provenance（那些是元数据，不是标准血缘）。
    场景集合变化（新增 / 消失）也算漂移——基线必须跟上场景集。
    """
    details: dict[str, list[str]] = {}
    for sid, cur_fp in sorted(current.items()):
        base_fp = baseline.get(sid)
        if base_fp is None:
            details[sid] = ["(新增场景，基线无此指纹)"]
            continue
        for key in ("expectation_fingerprint", "loop_fingerprint"):
            if cur_fp.get(key) != base_fp.get(key):
                details.setdefault(sid, []).append(key)
    for sid in sorted(set(baseline) - set(current)):
        details[sid] = ["(场景已移除，基线残留)"]
    return details


def has_bump_marker(text: str, marker: str = BUMP_MARKER) -> bool:
    """标记文本是否含 bump 标记（大小写不敏感，容忍 PR 描述排版）。"""
    if not text:
        return False
    return marker.lower() in text.lower()


def check_drift_policy(
    current: dict[str, dict[str, str]],
    baseline: dict[str, dict[str, str]] | None,
    marker_text: str,
    *,
    marker: str = BUMP_MARKER,
) -> tuple[bool, str]:
    """裁决漂移治理（纯函数）。

    Args:
        current: 本次运行的指纹（scenario_id → 两枚指纹）。
        baseline: 仓库基线；``None`` = 无基线文件（首次运行）。
        marker_text: PR 描述 + 提交信息拼接文本（CI 用 gh pr view 收集）。

    Returns:
        ``(ok, hint)``：
        - 无基线 → ``(True, "首次运行无基线…")``（不存在可比对象，不拦）；
        - 无漂移 → ``(True, "指纹与基线一致…")``；
        - 有漂移 + 标记 → ``(True, "漂移已注明 {marker}")``；
        - 有漂移 + 缺标记 → ``(False, "Fingerprint drift without baseline update")``。
    """
    if baseline is None:
        return True, (
            "首次运行无基线文件，跳过漂移判定；"
            f"确认基线后用 --init-baseline 生成 {BASELINE_FILENAME}"
        )
    details = drift_details(current, baseline)
    if not details:
        return True, f"指纹与基线一致（{len(current)} 场景），无漂移"
    if has_bump_marker(marker_text, marker):
        return True, (
            f"检测到指纹漂移（{len(details)} 场景），已在 PR 注明标记 {marker!r}"
        )
    return False, (
        f"Fingerprint drift without baseline update：{len(details)} 个场景指纹漂移，"
        f"但 PR 描述/提交信息缺少必需标记 {marker!r}\n"
        + "\n".join(f"  - {sid}: {', '.join(fields)}" for sid, fields in details.items())
    )


def baselines_changed(
    changed_files: list[str], baselines_rel: str = BASELINES_REL
) -> bool:
    """变更文件列表中是否包含基线 JSON（新增 / 修改 / 删除均算）。

    仅看路径前缀（目录含子目录），不关心增删改——删基线同样危险，须评审。
    """
    rel = _norm_rel(baselines_rel).rstrip("/")
    for f in changed_files:
        nf = _norm_rel(f)
        if nf == rel or nf.startswith(rel + "/"):
            return True
    return False


def check_baseline_file_policy(
    changed_files: list[str],
    marker_text: str,
    *,
    baselines_rel: str = BASELINES_REL,
    marker: str = BUMP_MARKER,
) -> tuple[bool, str]:
    """基线**文件**变更治理（防绕过漂移判定：改基线文件本身也须标记）。"""
    if not baselines_changed(changed_files, baselines_rel):
        return True, "未改动基线文件，无需标记"
    if has_bump_marker(marker_text, marker):
        return True, f"基线文件变更已在 PR 注明标记 {marker!r}"
    return False, (
        f"检测到基线文件变更（{baselines_rel}/），但 PR 描述/提交信息缺少必需标记 "
        f"{marker!r}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _changed_files_since(base: str) -> list[str]:
    """``git diff --name-only <base>...HEAD``（失败返回空列表，由调用方裁决）。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return []
        return [line for line in out.stdout.splitlines() if line.strip()]
    except OSError:
        return []


def _write_baseline(current: dict[str, dict[str, str]], path: str | Path) -> None:
    """首次生成基线（--init-baseline）：按 scenario_id 排序，确定性落盘。"""
    p = Path(path)
    if not p.parent.exists():
        raise ValueError(f"基线父目录不存在，拒绝自动创建以防路径穿越：{p.parent}")
    payload = {
        "generated_at": "",
        "note": "ADR-0034 loop 指纹基线（DoD C4）。变更须 PR 注明 integration-baseline-bump。",
        "scenarios": {sid: current[sid] for sid in sorted(current)},
    }
    p.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ADR-0034 loop 指纹基线漂移检测（DoD C4）"
    )
    parser.add_argument(
        "--current",
        type=Path,
        required=True,
        help="本次运行指纹汇总（run_integration_validation.py 的 adr0034_fingerprints.json）",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help=f"仓库基线（{BASELINE_FILENAME}）；不存在视为首次运行",
    )
    parser.add_argument("--marker-file", type=Path, help="PR 描述+提交信息文本（CI 收集）")
    parser.add_argument(
        "--marker-text",
        help="直接传标记文本（替代 --marker-file；本地调试用）",
    )
    parser.add_argument(
        "--base",
        default="origin/main",
        help="git 基线（用于检测基线文件变更；默认 origin/main）",
    )
    parser.add_argument(
        "--init-baseline",
        action="store_true",
        help="用 current 生成基线文件（首次 / 显式 bump 后更新）并跳过漂移判定",
    )
    args = parser.parse_args(argv)

    try:
        current = load_fingerprints(args.current)
    except (FileNotFoundError, ValueError, TypeError) as exc:
        print(f"[ERROR] {exc}")
        return 2

    if args.init_baseline:
        try:
            _write_baseline(current, args.baseline)
        except ValueError as exc:
            print(f"[ERROR] {exc}")
            return 2
        print(f"[BASELINE] 已生成基线：{args.baseline}（{len(current)} 场景）")
        return 0

    marker_text = ""
    if args.marker_text:
        marker_text = args.marker_text
    elif args.marker_file is not None:
        p = Path(args.marker_file)
        if p.exists():
            marker_text = p.read_text(encoding="utf-8")

    baseline: dict[str, dict[str, str]] | None = None
    if Path(args.baseline).exists():
        try:
            baseline = load_fingerprints(args.baseline)
        except (ValueError, TypeError) as exc:
            print(f"[ERROR] 基线文件非法：{exc}")
            return 2

    ok, hint = check_drift_policy(current, baseline, marker_text)
    if ok:
        print(f"[OK] {hint}")
    else:
        print(f"[FAIL] {hint}")

    # 基线文件变更治理（防绕过；git 不可用时跳过该项，漂移判定仍生效）
    changed = _changed_files_since(args.base)
    if changed:
        file_ok, file_hint = check_baseline_file_policy(changed, marker_text)
        if not file_ok:
            print(f"[FAIL] {file_hint}")
            return 1
        print(f"[OK] {file_hint}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
