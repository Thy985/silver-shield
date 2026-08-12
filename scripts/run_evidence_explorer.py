"""ADR-0035 D1 · Runtime Evidence Explorer 入口：一次运行 → 可视化。

把 ADR-0034 落盘 artifact（``artifacts/adr0034_integration/``）投影为
``EvidenceProjection``（D2 契约）并渲染为**自包含单页 HTML**（D4：ECharts 内联，
零服务器、浏览器直开）。

用法：
    python scripts/run_evidence_explorer.py
    python scripts/run_evidence_explorer.py --artifacts D:/temp/d1-artifacts --output out.html

退出码（D9 零行为：不接 CI 门禁，默认 0 = 生成成功；fail-closed 时才非 0）：
- 0：成功生成 HTML；
- 1：投影契约违规（artifact 缺失/字段演化）——fail-closed，不产空白页；
- 2：参数/目录错误。
"""

from __future__ import annotations

import argparse
from pathlib import Path

_DEFAULT_ARTIFACTS = (
    Path(__file__).resolve().parent.parent
    / "artifacts"
    / "adr0034_integration"
)
_DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "evidence_explorer.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0035 D1 Evidence Explorer")
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=_DEFAULT_ARTIFACTS,
        help="ADR-0034 artifact 目录（默认 artifacts/adr0034_integration）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_DEFAULT_OUTPUT,
        help="输出 HTML 路径（默认 evidence_explorer.html）",
    )
    args = parser.parse_args(argv)

    # 延迟导入：--help / 参数错误时不拉起 visualizer 链。
    from home_perception.visualizer import (
        EvidenceProjectionError,
        load_evidence_projection,
        render_projection,
    )

    try:
        projection = load_evidence_projection(args.artifacts)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        return 2
    except EvidenceProjectionError as exc:
        print(f"[FAIL-CLOSED] 投影契约违规，拒绝生成：{exc}")
        return 1

    # render_projection 的 ValueError 是**第二道防御层**（评审 #10）：loader 已保证
    # projection 结构合法（fail-closed），此 catch 只兜"loader 与 renderer 契约漂移"
    # 的极端情况——两者同源（visualizer/schema），正常不可能触发。
    try:
        html_doc = render_projection(projection)
    except ValueError as exc:
        print(f"[FAIL-CLOSED] 渲染拒绝：{exc}")
        return 1

    out: Path = args.output
    try:
        out.write_text(html_doc, encoding="utf-8")
    except OSError as exc:
        print(f"[ERROR] 写输出失败：{exc}")
        return 2

    n_scenarios = projection["meta"]["scenario_count"]
    print(f"[OK] Runtime Evidence Explorer 已生成：{out}")
    print(f"     场景 {n_scenarios} 个 · HTML {out.stat().st_size / 1024:.0f} KB（自包含，浏览器直开）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
