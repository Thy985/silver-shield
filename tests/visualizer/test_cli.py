"""ADR-0035 D1 · CLI 入口测试（scripts/run_evidence_explorer.py）。

评审 #11：--artifacts 指向不存在目录 → FileNotFoundError → 退出码 2。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from run_evidence_explorer import main


def test_cli_missing_artifacts_dir_exit_2(tmp_path):
    """--artifacts 指向不存在目录 → FileNotFoundError → exit 2（评审 #11）。"""
    rc = main(["--artifacts", str(tmp_path / "no-such-dir"), "--output", str(tmp_path / "o.html")])
    assert rc == 2


def test_cli_fail_closed_exit_1(tmp_path):
    """artifact 不全（缺 gate.json）→ EvidenceProjectionError → exit 1（fail-closed 不产空白）。"""
    from .conftest import make_artifacts

    d = make_artifacts(tmp_path / "a", drop_file="sw_t1.gate.json")
    out = tmp_path / "out.html"
    rc = main(["--artifacts", str(d), "--output", str(out)])
    assert rc == 1
    assert not out.exists(), "fail-closed 不得产出文件"


def test_cli_success_exit_0(tmp_path):
    """合法 artifact 集 → 生成 HTML → exit 0（验收 1）。"""
    from .conftest import make_artifacts

    d = make_artifacts(tmp_path / "a")
    out = tmp_path / "evidence.html"
    rc = main(["--artifacts", str(d), "--output", str(out)])
    assert rc == 0
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    assert "Runtime Evidence Explorer" in html
