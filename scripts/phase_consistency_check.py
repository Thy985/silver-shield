#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目阶段一致性检测器（银龄盾 · Home 感知模块）。

用途
----
本项目已确立「项目阶段 / 交付状态」的**单一事实源（SSOT）**归属关系：
  * `README.md` 的「当前状态」节 = SSOT（其顶部「状态归属声明」明确这一点）；
  * `AGENTS.md` §10 与 `docs/08_roadmap.md` §8.2 = README 的**投影（projection）**，须与 README 一致；
  * `docs/05_git_workflow.md` 仅就 Git 提交规范以 `AGENTS.md` 为准，不覆盖阶段状态 SSOT。

本脚本是一个**只读、加法型**的 CI 门禁：从 README 的「当前状态」节抽取一组
*规范事实标记（canonical tokens）*，再校验每个投影文件是否都包含这些标记。
任一投影缺失某标记 → 判定为**漂移（drift）**。脚本**只检测、不修复**，
绝不修改任何现有文件，因此适合作为 CI 的「一致性锁」——把"已对账 + 已声明归属"
这一人工成果永久自动化锁定。

规范事实标记（canonical tokens，取自 README·当前状态）
---------------------------------------------------
  * phase        : 阶段标识，如 `MVP Release Candidate` / `Release Candidate`
  * version      : 版本 tag，如 `v0.1.0-mvp-rc`
  * p0_completion: P0 完成范围，须同时含 `P0-11` 与一条 `P0-x~P0-y` 全链路范围标识
  * test_baseline: 测试基线，须同时含测试数量（如 `289`）与 `全绿` 短语

退出码约定（与 FormulaFix `drift_check.py` 对齐）
-------------------------------------------------
  0 = 一致（无漂移）
  1 = 检出 ≥1 处漂移
  2 = 输入文件缺失 / 解析失败

用法
----
  python scripts/phase_consistency_check.py
  python scripts/phase_consistency_check.py --readme /tmp/readme_drift.md
  python scripts/phase_consistency_check.py \
      --readme README.md --agents AGENTS.md \
      --roadmap docs/08_roadmap.md --gitworkflow docs/05_git_workflow.md

依赖：仅 Python 3 标准库（argparse / hashlib / pathlib / re / sys）。
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# 默认路径（相对仓库根；脚本位于 <repo>/scripts/ 下，故仓库根 = 父目录）
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_README = REPO_ROOT / "README.md"
DEFAULT_AGENTS = REPO_ROOT / "AGENTS.md"
DEFAULT_ROADMAP = REPO_ROOT / "docs" / "08_roadmap.md"
DEFAULT_GITWORKFLOW = REPO_ROOT / "docs" / "05_git_workflow.md"

# 退出码
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

# 投影检查的分组总数（用于 OK 摘要里"命中标记数"的分母）
PROJECTION_GROUP_COUNT = 4

# SSOT 归属声明关键字（README 顶部须至少含其一）
SSOT_DECLARATION_KEYWORDS = ("单一事实源", "SSOT", "状态归属声明")

# 阶段 / 交付状态投影的目标章节标题正则
README_STATUS_HEADING = re.compile(r"^##\s+当前状态")
AGENTS_SECTION_HEADING = re.compile(r"^##\s+10\.")
ROADMAP_SECTION_HEADING = re.compile(r"^##\s+8\.2")

# 规范标记抽取正则
PHASE_RE = re.compile(r"MVP Release Candidate|Release Candidate")
VERSION_RE = re.compile(r"v\d+\.\d+\.\d+(?:-[A-Za-z0-9.\-]+)?")
P0_MARKER_RE = re.compile(r"P0-11")
P0_RANGE_RE = re.compile(r"P0-\d+~P0-\d+")
TEST_COUNT_RE = re.compile(r"(\d+)\s*测试全绿|(\d+)\s*全绿")
TEST_PHRASE = "全绿"


class ParseError(Exception):
    """输入文件缺失或无法解析预期章节时抛出（映射到退出码 2）。"""


@dataclass
class CanonicalTokens:
    """从 README·当前状态节抽取出的规范事实标记。"""

    phase: Optional[str] = None
    version: Optional[str] = None
    p0_marker: Optional[str] = None
    p0_range: Optional[str] = None
    test_count: Optional[str] = None

    def summary_lines(self) -> List[str]:
        """返回人类可读的规范标记摘要行。"""
        return [
            f"    phase         = {self.phase or '(未抽取)'}",
            f"    version       = {self.version or '(未抽取)'}",
            f"    p0_completion = {self.p0_marker or '(未抽取)'} "
            f"(+ 全链路 {self.p0_range or '(未抽取)'})",
            f"    test_baseline = {self.test_count or '(未抽取)'} 测试全绿",
        ]


# ---------------------------------------------------------------------------
# 文件读取与章节抽取
# ---------------------------------------------------------------------------
def read_text(path: Path) -> str:
    """读取文本文件；文件不存在或不可读时抛 ParseError。"""
    if not path.is_file():
        raise ParseError(f"输入文件不存在: {path}")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 防御性
        raise ParseError(f"无法读取文件 {path}: {exc}") from exc


def sha256_of(path: Path) -> str:
    """计算文件 sha256（仅用于审计/零改动确认，不写文件）。"""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def extract_section(text: str, heading_re: "re.Pattern[str]", label: str) -> str:
    """抽取 markdown 中首个匹配 heading_re 的 `## ` 章节（含标题行），到下一个
    `## ` 标题为止。找不到章节时抛 ParseError。"""
    lines = text.splitlines()
    start_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        if heading_re.match(line):
            start_idx = idx
            break
    if start_idx is None:
        raise ParseError(f"在文档中未找到预期章节: {label}")
    collected: List[str] = []
    for line in lines[start_idx:]:
        # 跳过首个之后的同级标题（章节结束）
        if collected and re.match(r"^##\s", line):
            break
        collected.append(line)
    return "\n".join(collected)


# ---------------------------------------------------------------------------
# 规范标记抽取与校验
# ---------------------------------------------------------------------------
def extract_canonical_tokens(status_text: str) -> CanonicalTokens:
    """从 README·当前状态节文本中抽取规范事实标记。"""
    tokens = CanonicalTokens()

    phase_match = PHASE_RE.search(status_text)
    if phase_match:
        tokens.phase = phase_match.group(0)

    version_match = VERSION_RE.search(status_text)
    if version_match:
        tokens.version = version_match.group(0)

    p0_marker_match = P0_MARKER_RE.search(status_text)
    if p0_marker_match:
        tokens.p0_marker = p0_marker_match.group(0)

    p0_range_match = P0_RANGE_RE.search(status_text)
    if p0_range_match:
        tokens.p0_range = p0_range_match.group(0)

    test_count_match = TEST_COUNT_RE.search(status_text)
    if test_count_match:
        # 捕获组 1 或 2 其一非空
        tokens.test_count = (
            test_count_match.group(1) or test_count_match.group(2)
        )
    return tokens


def check_ssot_declaration(readme_text: str) -> bool:
    """README 顶部是否含 SSOT 归属声明（防漂移前提）。"""
    return any(kw in readme_text for kw in SSOT_DECLARATION_KEYWORDS)


def check_projection(proj_label: str, proj_text: str, tokens: CanonicalTokens) -> List[str]:
    """校验单个投影文件是否包含全部规范标记。

    返回缺失项的人类可读描述列表（空列表 = 全部命中）。
    """
    missing: List[str] = []

    # 1) 阶段标识
    if tokens.phase and tokens.phase not in proj_text:
        missing.append(
            f"[{proj_label}] 缺失阶段标识: '{tokens.phase}' "
            f"（建议以 README 为准回溯修正）"
        )

    # 2) 版本 tag
    if tokens.version and tokens.version not in proj_text:
        missing.append(
            f"[{proj_label}] 缺失版本 tag: '{tokens.version}' "
            f"（建议以 README 为准回溯修正）"
        )

    # 3) P0 完成范围：须同时含 P0-11 与一条全链路范围标识
    if tokens.p0_marker and tokens.p0_marker not in proj_text:
        missing.append(
            f"[{proj_label}] 缺失 P0 完成标记: '{tokens.p0_marker}' "
            f"（建议以 README 为准回溯修正）"
        )
    if not P0_RANGE_RE.search(proj_text):
        missing.append(
            f"[{proj_label}] 缺失 P0 全链路范围标识 (P0-x~P0-y) "
            f"（建议以 README 为准回溯修正）"
        )

    # 4) 测试基线：须同时含测试数量与 '全绿' 短语
    if tokens.test_count and tokens.test_count not in proj_text:
        missing.append(
            f"[{proj_label}] 缺失测试基线数量: '{tokens.test_count}' "
            f"（建议以 README 为准回溯修正）"
        )
    if TEST_PHRASE not in proj_text:
        missing.append(
            f"[{proj_label}] 缺失测试基线短语: '{TEST_PHRASE}' "
            f"（建议以 README 为准回溯修正）"
        )

    return missing


def soft_check_gitworkflow(gitwf_text: str) -> List[str]:
    """软检查：docs/05 是否把阶段状态权威正确指向 README，而非无限定地
    以 AGENTS.md 独占阶段状态。软检查**不阻塞**主判定（不影响退出码）。"""
    warnings: List[str] = []

    # 若含"权威来源以 AGENTS.md 为准"，必须带有 Git 提交规范之类的限定词
    for match in re.finditer(r"权威来源以\s*`?AGENTS\.md`?\s*为准", gitwf_text):
        after = gitwf_text[match.end(): match.end() + 80]
        if not re.search(r"限\s*Git|Git 提交规范|协作约束|提交规范", after):
            warnings.append(
                "docs/05 含无限定词的'权威来源以 AGENTS.md 为准'，"
                "可能独占阶段状态权威（软检查不通过）"
            )
            break

    # 良好信号：显式把阶段状态单一事实源指向 README
    good_signal = (
        "单一事实源为 README" in gitwf_text
        or "README.md·SSOT" in gitwf_text
        or ("README" in gitwf_text and "单一事实源" in gitwf_text)
    )
    if not good_signal:
        warnings.append(
            "docs/05 未将阶段状态权威显式指向 README（建议补充 SSOT 指向，软检查提示）"
        )
    return warnings


# ---------------------------------------------------------------------------
# 报告渲染
# ---------------------------------------------------------------------------
def render_ok(
    tokens: CanonicalTokens,
    projection_hits: List[Tuple[str, int]],
    soft_warnings: List[str],
    sha_lines: List[str],
) -> str:
    """渲染一致（OK）摘要。projection_hits = [(标签, 命中分组数), ...]。"""
    lines = [
        "[phase_consistency_check] OK — 项目阶段一致性已锁定（README 为 SSOT）",
        "  README.md (SSOT) 规范标记:",
    ]
    lines.extend(tokens.summary_lines())
    lines.append("  投影命中情况:")
    for label, hits in projection_hits:
        lines.append(
            f"    {label}: 命中 {hits}/{PROJECTION_GROUP_COUNT} 标记组"
        )
    if not soft_warnings:
        lines.append("  docs/05_git_workflow.md 软检查: PASS（阶段状态权威指向 README）")
    else:
        lines.append("  docs/05_git_workflow.md 软检查: 有提示（不阻塞）")
        for w in soft_warnings:
            lines.append(f"    - {w}")
    lines.append("  输入文件 sha256（只读审计）:")
    lines.extend(sha_lines)
    return "\n".join(lines)


def render_drift(
    tokens: CanonicalTokens,
    ssot_ok: bool,
    all_missing: List[str],
    soft_warnings: List[str],
    sha_lines: List[str],
) -> str:
    """渲染漂移报告。"""
    lines = [
        f"[phase_consistency_check] DRIFT — 检出 {len(all_missing)} 处投影漂移",
        "  README.md (SSOT) 规范标记:",
    ]
    lines.extend(tokens.summary_lines())
    if not ssot_ok:
        lines.append(
            "  [README] 缺失 SSOT 归属声明（'单一事实源'/'SSOT'/'状态归属声明'）"
            "—— 防漂移前提缺失，请补回顶部状态归属声明"
        )
    lines.append("  --- 漂移明细 ---")
    for item in all_missing:
        lines.append(f"  {item}")
    if soft_warnings:
        lines.append("  docs/05_git_workflow.md 软检查提示（不阻塞）:")
        for w in soft_warnings:
            lines.append(f"    - {w}")
    lines.append("  输入文件 sha256（只读审计）:")
    lines.extend(sha_lines)
    return "\n".join(lines)


def render_error(message: str) -> str:
    return f"[phase_consistency_check] ERROR — {message}"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="银龄盾项目阶段一致性检测器（只读 / 加法型 CI 门禁）",
    )
    parser.add_argument(
        "--readme",
        type=Path,
        default=DEFAULT_README,
        help=f"README.md 路径（SSOT 源），默认 {DEFAULT_README}",
    )
    parser.add_argument(
        "--agents",
        type=Path,
        default=DEFAULT_AGENTS,
        help=f"AGENTS.md 路径（§10 投影），默认 {DEFAULT_AGENTS}",
    )
    parser.add_argument(
        "--roadmap",
        type=Path,
        default=DEFAULT_ROADMAP,
        help=f"docs/08_roadmap.md 路径（§8.2 投影），默认 {DEFAULT_ROADMAP}",
    )
    parser.add_argument(
        "--gitworkflow",
        type=Path,
        default=DEFAULT_GITWORKFLOW,
        help=f"docs/05_git_workflow.md 路径（软检查），默认 {DEFAULT_GITWORKFLOW}",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    # 1) 读取四个输入文件（缺失 → 退出码 2）
    try:
        readme_text = read_text(args.readme)
        agents_text = read_text(args.agents)
        roadmap_text = read_text(args.roadmap)
        gitwf_text = read_text(args.gitworkflow)
    except ParseError as exc:
        print(render_error(f"输入文件缺失/不可读: {exc}"))
        return EXIT_ERROR

    # 审计用 sha256（证明脚本未改动任何文件）
    sha_lines = [
        f"    {args.readme.name:<22} {sha256_of(args.readme)}",
        f"    {args.agents.name:<22} {sha256_of(args.agents)}",
        f"    {args.roadmap.name:<22} {sha256_of(args.roadmap)}",
        f"    {args.gitworkflow.name:<22} {sha256_of(args.gitworkflow)}",
    ]

    # 2) 抽取 README·当前状态节与规范标记
    try:
        readme_status = extract_section(
            readme_text, README_STATUS_HEADING, "README·当前状态"
        )
    except ParseError as exc:
        print(render_error(f"README 解析失败: {exc}"))
        return EXIT_ERROR

    tokens = extract_canonical_tokens(readme_status)

    # 校验规范标记是否被成功抽取（关键标记缺失视为解析失败）
    if not (tokens.phase and tokens.version and tokens.p0_marker and tokens.test_count):
        print(
            render_error(
                "README·当前状态节未能抽取全部规范标记"
                f"（phase={tokens.phase}, version={tokens.version}, "
                f"p0={tokens.p0_marker}, test={tokens.test_count}）"
            )
        )
        return EXIT_ERROR

    # 3) SSOT 归属声明（README 顶部）
    ssot_ok = check_ssot_declaration(readme_text)

    # 4) 校验两个投影章节
    all_missing: List[str] = []
    projection_hits: List[Tuple[str, int]] = []

    try:
        agents_section = extract_section(
            agents_text, AGENTS_SECTION_HEADING, "AGENTS.md §10"
        )
    except ParseError as exc:
        all_missing.append(f"[AGENTS.md §10] 章节缺失: {exc}（投影结构漂移）")
        agents_section = ""
    try:
        roadmap_section = extract_section(
            roadmap_text, ROADMAP_SECTION_HEADING, "docs/08_roadmap.md §8.2"
        )
    except ParseError as exc:
        all_missing.append(f"[docs/08_roadmap.md §8.2] 章节缺失: {exc}（投影结构漂移）")
        roadmap_section = ""

    if agents_section:
        miss = check_projection("AGENTS.md §10", agents_section, tokens)
        all_missing.extend(miss)
        projection_hits.append(("AGENTS.md §10", PROJECTION_GROUP_COUNT - _count_groups_missing(miss)))

    if roadmap_section:
        miss = check_projection("docs/08_roadmap.md §8.2", roadmap_section, tokens)
        all_missing.extend(miss)
        projection_hits.append(
            ("docs/08_roadmap.md §8.2", PROJECTION_GROUP_COUNT - _count_groups_missing(miss))
        )

    # 5) 软检查（不阻塞退出码）
    soft_warnings = soft_check_gitworkflow(gitwf_text)

    # 6) 汇总判定
    drift_detected = (not ssot_ok) or bool(all_missing)

    if drift_detected:
        print(render_drift(tokens, ssot_ok, all_missing, soft_warnings, sha_lines))
        return EXIT_DRIFT

    print(render_ok(tokens, projection_hits, soft_warnings, sha_lines))
    return EXIT_OK


def _count_groups_missing(missing_items: List[str]) -> int:
    """根据缺失项描述粗略反推缺失的分组数（用于 OK 摘要的命中计数）。

    缺失项按分组聚合：phase / version / p0_completion（最多 2 条）/
    test_baseline（最多 2 条）。这里统计"涉及的不同分组"数量。
    """
    groups = set()
    for item in missing_items:
        if "阶段标识" in item:
            groups.add("phase")
        elif "版本 tag" in item:
            groups.add("version")
        elif "P0" in item:
            groups.add("p0_completion")
        elif "测试基线" in item:
            groups.add("test_baseline")
    return len(groups)


if __name__ == "__main__":
    sys.exit(main())
