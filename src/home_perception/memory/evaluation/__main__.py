"""E-1 评估 CLI 入口。

用法::

    python -m home_perception.memory.evaluation --fixtures tests/fixtures/memory_replay \
        --out artifacts/e1

产出 ``e1_report.json`` + ``e1_report.md``（DESIGN-memory-evaluation.md §11）；
Hard Gate（§9）失败时退出码为 1，可直接作为 CI gate 使用。
"""

from __future__ import annotations

from home_perception.memory.evaluation.report import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
