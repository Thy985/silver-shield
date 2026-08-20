"""G0-4 · build_trusted_case --golden 契约测试（轻量，不跑完整集成链路）。

验证 golden 生产模式的静态契约：
- ``--golden`` 参数存在且解析为 True（不触发前缀匹配陷阱）；
- golden 默认场景目录存在且含 golden fixtures；
- golden 媒体映射（dataset 相对路径）结构合法，本地资产存在时指向真实视频；
- prepare_case_media 的 golden 映射键与 golden fixtures 的 scenario_id 对齐。

完整 CI 生产链（golden repeated_visit → canonical → Case Viewer）由端到端脚本验证
（build_trusted_case --golden 手工跑通），本测试不重复执行重链路。
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_GOLDEN_FIXTURES = (
    _REPO_ROOT / "src/home_perception/validation/fixtures/scenarios/golden"
)


def test_golden_arg_parses():
    """--golden 是独立 store_true 参数（不与 --golden/--no-golden 前缀冲突）。"""
    import scripts.build_trusted_case as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    # 参数定义：独立 --golden + store_true（不写成 --golden/--no-golden store 形式）。
    assert '"--golden"' in src
    assert 'action="store_true"' in src
    assert "--golden/--no-golden" not in src


def test_golden_fixtures_dir_exists():
    """golden 默认场景目录存在且含 yaml fixtures。"""
    assert _GOLDEN_FIXTURES.is_dir()
    yamls = sorted(_GOLDEN_FIXTURES.glob("*.yaml"))
    assert len(yamls) >= 2
    assert any("repeated_visit" in y.name for y in yamls)
    assert any("benign" in y.name for y in yamls)


def test_golden_media_map_structure():
    """golden 媒体映射：值相对 dataset、键对齐 golden fixtures scenario_id。"""
    from scripts.prepare_case_media import _DEFAULT_GOLDEN_MEDIA_MAP

    assert _DEFAULT_GOLDEN_MEDIA_MAP  # 非空
    for sid, rel in _DEFAULT_GOLDEN_MEDIA_MAP.items():
        # 相对路径（media_root=dataset 下），不含绝对路径/穿越。
        assert not Path(rel).is_absolute(), f"{sid} 映射必须是相对路径"
        assert ".." not in rel, f"{sid} 映射不得穿越"
        assert rel.endswith(".mp4"), f"{sid} 映射必须是 mp4"
    # 键对齐 golden fixtures 的 scenario_id（至少 repeated_visit）。
    import yaml

    rv = _GOLDEN_FIXTURES / "golden_repeated_visit.yaml"
    assert rv.is_file()
    data = yaml.safe_load(rv.read_text(encoding="utf-8"))
    assert data["meta"]["scenario_id"] in _DEFAULT_GOLDEN_MEDIA_MAP


@pytest.mark.skipif(
    not (_REPO_ROOT / "dataset").is_dir(),
    reason="dataset/ 资产未检出",
)
def test_golden_media_map_targets_exist_locally():
    """本地有 dataset 资产时，映射指向真实视频文件。"""
    from scripts.prepare_case_media import _DEFAULT_GOLDEN_MEDIA_MAP

    for sid, rel in _DEFAULT_GOLDEN_MEDIA_MAP.items():
        target = _REPO_ROOT / "dataset" / rel
        assert target.is_file(), f"{sid} -> {rel} 不存在（dataset 资产缺失）"
