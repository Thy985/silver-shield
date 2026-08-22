"""ADR-0042 步骤 6 · YAMNet class_map_path 加载链路修复测试。

缺陷回归背景：``build_tagger`` 此前从未消费 ``class_map_path``（且配置对象上
不存在 ``class_names`` 字段，``getattr`` 恒 None）→ 521 类全部退化为 ``class_N``
透传 → 下游语义映射永不命中 → kind 分类全落 fallback → D4 MONITOR ceiling
因此永不可解除。

覆盖：
- ``load_class_names`` fail-fast 契约（格式 / 长度 / 路径安全 / 缺失）；
- ``Tier1AudioConfig._class_map_path_guard`` 配置期守卫；
- ``build_tagger`` 端到端接线（class_map_path → class_names → 语义标签）。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError

from home_perception.audio.tagging import (
    YamNetTagger,
    build_tagger,
    load_class_names,
)
from home_perception.core.config import Tier1AudioConfig


def _names(first: str = "Speech") -> list[str]:
    return [first] + [f"c{i}" for i in range(1, 521)]


def _write_class_map(path, fmt: str = "json") -> str:
    if fmt == "json":
        import json

        path.write_text(json.dumps(_names()), encoding="utf-8")
    elif fmt == "csv":
        import csv

        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "mid", "display_name"])
            for i, n in enumerate(_names()):
                w.writerow([i, f"/m/0{i:05x}", n])
    else:
        import yaml

        path.write_text(yaml.safe_dump(_names()), encoding="utf-8")
    return str(path)


# ============================================================================
# load_class_names · fail-fast 契约
# ============================================================================


class TestLoadClassNames:
    def test_json_roundtrip(self, tmp_path):
        p = _write_class_map(tmp_path / "class_map.json")
        names = load_class_names(p)
        assert len(names) == 521
        assert names[0] == "Speech"

    def test_yaml_roundtrip(self, tmp_path):
        p = _write_class_map(tmp_path / "class_map.yaml", fmt="yaml")
        names = load_class_names(p)
        assert len(names) == 521
        assert names[0] == "Speech"

    def test_empty_path_rejected(self):
        with pytest.raises(ValueError, match="不能为空"):
            load_class_names("")

    def test_bad_suffix_rejected(self, tmp_path):
        p = tmp_path / "class_map.txt"
        p.write_text("a,b\n", encoding="utf-8")
        with pytest.raises(ValueError, match="\\.csv/\\.yaml/\\.yml/\\.json"):
            load_class_names(str(p))

    def test_missing_file_fail_fast(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="class_map 文件不存在"):
            load_class_names(str(tmp_path / "nope.json"))

    def test_traversal_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="路径遍历"):
            load_class_names(str(tmp_path / ".." / "evil.json"))

    @pytest.mark.parametrize("bad", [[1, 2], {"a": 1}, "not-a-list"])
    def test_non_string_list_rejected(self, tmp_path, bad):
        import json

        p = tmp_path / "bad.json"
        p.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises(ValueError, match="字符串列表"):
            load_class_names(str(p))

    def test_wrong_length_rejected(self, tmp_path):
        import json

        p = tmp_path / "short.json"
        p.write_text(json.dumps(["a"] * 520), encoding="utf-8")
        with pytest.raises(ValueError, match="521"):
            load_class_names(str(p))


# ============================================================================
# CSV 格式（AudioSet 官方分发格式：index,mid,display_name）
# ============================================================================


class TestCsvFormat:
    def test_csv_roundtrip(self, tmp_path):
        p = _write_class_map(tmp_path / "class_map.csv", fmt="csv")
        names = load_class_names(p)
        assert len(names) == 521
        assert names[0] == "Speech"

    def test_csv_missing_display_name_column_rejected(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("index,mid\n0,/m/1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="display_name"):
            load_class_names(str(p))

    def test_csv_non_contiguous_index_rejected(self, tmp_path):
        import csv

        p = tmp_path / "gap.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "mid", "display_name"])
            for i in (0, 2, 3):  # 断裂：缺 1
                w.writerow([i, "/m/1", f"c{i}"])
        with pytest.raises(ValueError, match="连续递增"):
            load_class_names(str(p))

    def test_csv_empty_display_name_rejected(self, tmp_path):
        import csv

        p = tmp_path / "blank.csv"
        with p.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["index", "mid", "display_name"])
            for i in range(521):
                w.writerow([i, "/m/1", "" if i == 7 else f"c{i}"])
        with pytest.raises(ValueError, match="空 display_name"):
            load_class_names(str(p))

    def test_real_asset_smoke(self):
        """真实资产 smoke：随权重分发的官方 CSV 可直接加载（无则 skip）。"""
        asset = "data/models/yamnet/yamnet_class_map.csv"
        if not Path(asset).exists():
            pytest.skip("yamnet_class_map.csv not present in this environment")
        names = load_class_names(asset)
        assert len(names) == 521
        assert names[0] == "Speech"


# ============================================================================
# Tier1AudioConfig · 配置期守卫
# ============================================================================


class TestConfigGuard:
    def test_default_empty(self):
        assert Tier1AudioConfig().class_map_path == ""

    def test_valid_suffix_accepted(self):
        cfg = Tier1AudioConfig(class_map_path="data/models/yamnet/class_map.json")
        assert cfg.class_map_path.endswith(".json")

    def test_invalid_suffix_rejected(self):
        with pytest.raises(ValidationError, match="\\.csv/\\.yaml/\\.yml/\\.json"):
            Tier1AudioConfig(class_map_path="x.txt")

    def test_traversal_rejected(self):
        with pytest.raises(ValidationError, match="路径遍历"):
            Tier1AudioConfig(class_map_path="../secrets.json")


# ============================================================================
# build_tagger · 端到端接线（缺陷回归闭环）
# ============================================================================


class _FakeOrtSession:
    """最小 ONNX session 替身：返回固定 521 维 score，绕过权重加载。"""

    def __init__(self) -> None:
        scores = np.zeros(521, dtype=np.float32)
        scores[0] = 0.95  # index 0 → class_names[0]
        self._scores = scores

    def get_inputs(self):
        return [SimpleNamespace(name="input.1")]

    def run(self, _, feed):
        return [self._scores[None, :]]


class TestBuildTaggerWiring:
    def test_class_map_path_wired_into_tagger(self, tmp_path):
        """回归主断言：class_map_path 非空 → class_names 真实传入（此前恒 None）。"""
        cm = _write_class_map(tmp_path / "class_map.json")
        tagger = build_tagger(
            Tier1AudioConfig(enabled=True, model_path="fake.onnx", class_map_path=cm)
        )
        assert isinstance(tagger, YamNetTagger)
        assert tagger.class_names is not None
        assert len(tagger.class_names) == 521

    def test_empty_class_map_path_keeps_none_with_warning_path(self):
        """留空 = 内嵌精选子集模式（既有行为不变）：class_names=None + 构造告警。"""
        tagger = build_tagger(Tier1AudioConfig(enabled=True, model_path="fake.onnx"))
        assert isinstance(tagger, YamNetTagger)
        assert tagger.class_names is None

    def test_end_to_end_semantic_label_instead_of_class_n(self, tmp_path):
        """闭环证明：经 class_map 加载后，index 0 输出 speech（而非 class_0）。"""
        cm = _write_class_map(tmp_path / "class_map.json")
        tagger = build_tagger(
            Tier1AudioConfig(enabled=True, model_path="fake.onnx", class_map_path=cm)
        )
        assert isinstance(tagger, YamNetTagger)
        tagger._session = _FakeOrtSession()
        tags = tagger.tag(np.full(16000, 0.5, dtype=np.float32), 16000)
        labels = [t.label for t in tags]
        assert "speech" in labels
        assert not any(l.startswith("class_") for l in labels)

    def test_load_failure_propagates_not_silent(self, tmp_path):
        """fail-fast 契约：坏 class_map 必须显式失败，绝不静默退回 class_N。"""
        bad = tmp_path / "short.json"
        bad.write_text('["a"]', encoding="utf-8")
        with pytest.raises(ValueError, match="521"):
            build_tagger(
                Tier1AudioConfig(
                    enabled=True, model_path="fake.onnx", class_map_path=str(bad)
                )
            )