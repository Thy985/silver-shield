"""ADR-0033 Phase 3 基线 bump 治理（D7 + §6 基线治理）。

基线 JSON 变更（``evaluation/fixtures/baselines/*.json`` 新增 / 修改）属**显式、Owner 评审动作**
（ADR-0033 D7）：任何改动基线的 PR **必须**在 PR 描述（或提交信息）中注明标记
``benchmark-baseline-bump``，否则 CI ``baseline-bump-check`` job 拦截。

本脚本分两层：
- **纯函数**（可单测、可变异验证）：``requires_bump_marker`` / ``has_bump_marker`` /
  ``check_bump_policy`` —— 只处理「变更文件列表 + 标记文本」两组输入，不依赖 git / 网络；
- **CLI 包装**：默认从 ``git diff --name-only <base>...HEAD`` 取变更文件、从 ``--marker-text``
  （或 ``--marker-file``，CI 用 ``gh pr view`` 取 PR body 落盘）取标记文本，调纯函数裁决。

退出码：0 = 合规（无需 bump / 已注明）；1 = 基线变更但缺标记（PR 拦截）；2 = 输入/环境错误
（git 不可用、路径非法）。

依赖零第三方库（仅标准库），与 ``scripts/phase_consistency_check.py`` 同款轻量风格。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 基线 JSON 落点（相对仓库根，与 ``ab_runner.BASELINES_DIR`` 同源）
BASELINES_REL = "src/home_perception/evaluation/fixtures/baselines"
# PR 描述须包含此标记，声明「本次基线 bump 经 Owner 评审」
BUMP_MARKER = "benchmark-baseline-bump"


def _norm_rel(path: str) -> str:
    """归一化为 POSIX 风格相对路径（跨平台一致比对）。"""
    return path.replace("\\", "/").strip()


def requires_bump_marker(
    changed_files: list[str], baselines_rel: str = BASELINES_REL
) -> bool:
    """变更文件列表中是否包含基线 JSON（新增 / 修改均算）。

    判定：任一变更文件的归一化相对路径落在 ``baselines_rel`` 目录（含子目录）内。
    仅看路径前缀，不关心增 / 删 / 改（删基线同样危险，须评审）。
    """
    rel = _norm_rel(baselines_rel).rstrip("/")
    for f in changed_files:
        nf = _norm_rel(f)
        if nf == rel or nf.startswith(rel + "/"):
            return True
    return False


def has_bump_marker(text: str, marker: str = BUMP_MARKER) -> bool:
    """标记文本是否含 bump 标记（大小写不敏感，容忍 PR 描述排版）。"""
    if not text:
        return False
    return marker.lower() in text.lower()


def check_bump_policy(
    changed_files: list[str],
    marker_text: str,
    baselines_rel: str = BASELINES_REL,
    marker: str = BUMP_MARKER,
) -> tuple[bool, str]:
    """裁决基线 bump 治理策略（纯函数）。

    返回 ``(ok, hint)``：
    - 无基线变更 → ``(True, "未改动基线，无需标记")``；
    - 有基线变更且标记齐全 → ``(True, "基线变更已注明 <marker>")``；
    - 有基线变更但缺标记 → ``(False, "基线变更须 PR 注明 <marker>")``（CI 拦截）。
    """
    if not requires_bump_marker(changed_files, baselines_rel):
        return True, "未改动基线文件，无需 benchmark-baseline-bump 标记"
    if has_bump_marker(marker_text, marker):
        return True, f"基线 JSON 变更已在 PR 注明标记 {marker!r}"
    return (
        False,
        (
            f"检测到基线 JSON 变更，但 PR 描述/提交信息缺少必需标记 {marker!r}\n"
            f"          请在 PR 描述中加入 `{marker}` 并说明 bump 理由（Owner 评审）。"
        ),
    )


def compute_changed_files(root: Path, base: str) -> list[str]:
    """从 ``git diff --name-only <base>...HEAD`` 取变更文件（相对仓库根）。

    ``...``（三点）= 仅属 HEAD 而不属 ``base`` 的变更（即本 PR 相对 main 的改动）。
    非 git 环境 / 命令失败 → 抛 ``RuntimeError``（交由 CLI 转退出码 2）。
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "diff", "--name-only", f"{base}...HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(f"git diff 失败（无法判定变更文件）：{exc}") from exc
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0033 基线 bump 治理检查")
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1],
        help="仓库根目录（默认脚本上级）",
    )
    parser.add_argument(
        "--base", default="origin/main",
        help="git 对比 base（默认 origin/main，用于 <base>...HEAD）",
    )
    parser.add_argument(
        "--changed-files", nargs="*", default=None,
        help="显式变更文件列表（相对仓库根）；不提供则从 git diff 计算",
    )
    parser.add_argument(
        "--marker-text", default="",
        help="PR 描述 / 提交信息文本（含 bump 标记即合规）",
    )
    parser.add_argument(
        "--marker-file", type=Path, default=None,
        help="从文件读取标记文本（CI 用 gh pr view 落盘 PR body）",
    )
    parser.add_argument("--baselines-rel", default=BASELINES_REL)
    parser.add_argument("--marker", default=BUMP_MARKER)
    args = parser.parse_args(argv)

    if args.changed_files is not None:
        changed = args.changed_files
    else:
        try:
            changed = compute_changed_files(args.root, args.base)
        except RuntimeError as exc:
            print(f"[baseline-bump] 环境错误：{exc}", file=sys.stderr)
            return 2

    marker_text = args.marker_text
    if args.marker_file is not None:
        if not args.marker_file.exists():
            print(f"[baseline-bump] 标记文本文件不存在：{args.marker_file}", file=sys.stderr)
            return 2
        marker_text = args.marker_file.read_text(encoding="utf-8")

    ok, hint = check_bump_policy(
        changed, marker_text, baselines_rel=args.baselines_rel, marker=args.marker
    )
    # 信息性输出变更文件，便于 CI 日志排查
    if changed:
        print(f"[baseline-bump] 变更文件（{len(changed)}）：")
        for f in changed:
            print(f"  - {f}")
    print(f"[baseline-bump] {hint}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
