"""Demo 自检诊断（demo_diagnostics）单元测试（PR-C · torch-free）。

锁定 4 类合约：
  1. 5 项诊断（环境 / Registry / yaml / 媒体 / 端口）返回 ``DiagnosticResult`` 形状正确；
  2. 失败场景（缺失视频 / 占用端口 / Registry 异常）输出 ``Status.FAIL`` / ``Status.WARN``；
  3. ``run_all_diagnostics`` 返回 5 项且顺序稳定；
  4. ``print_diagnostics`` 输出格式对齐 + 退出码 0/1 正确。
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIAG_PATH = REPO_ROOT / "scripts" / "demo_diagnostics.py"  # 留作诊断用


def _load_diag():
    """通过 sys.path + import 加载 scripts/demo_diagnostics.py（与 run_demo.py 同款模式）。"""
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import demo_diagnostics as diag  # 延迟导入

    return diag


def _load_gateway():
    """通过 sys.path + import 加载 silver_demo.gateway（保留包上下文）。"""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    import silver_demo.gateway as gw  # 延迟导入

    return gw


# ---------------------------------------------------------------------------
# ① 形状合约
# ---------------------------------------------------------------------------


class TestDiagnosticResultShape:
    """``DiagnosticResult`` 形状 + Status 枚举。"""

    def test_status_enum_values(self) -> None:
        diag = _load_diag()
        assert {s.value for s in diag.Status} == {"OK", "WARN", "FAIL"}

    def test_diagnostic_result_required_fields(self) -> None:
        diag = _load_diag()
        r = diag.DiagnosticResult(
            name="x", title="t", status=diag.Status.OK, summary="s"
        )
        assert r.is_ok() is True
        assert r.is_fatal() is False
        assert r.details == []  # 默认空列表
        assert r.fix_hint is None

    def test_status_marks(self) -> None:
        diag = _load_diag()
        assert diag.STATUS_MARK[diag.Status.OK] == "✓"
        assert diag.STATUS_MARK[diag.Status.WARN] == "⚠"
        assert diag.STATUS_MARK[diag.Status.FAIL] == "✗"


# ---------------------------------------------------------------------------
# ① 环境诊断
# ---------------------------------------------------------------------------


class TestDiagnoseEnvironment:
    """环境诊断：复用 check_env.run_checks()。"""

    def test_ok_when_all_deps_present(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_environment()
        # 当前环境：torch / opencv / ultralytics / fastapi / uvicorn / pytest 全部已装
        assert result.status == diag.Status.OK
        assert "依赖" in result.summary or "就位" in result.summary
        assert result.fix_hint is None

    def test_returns_diagnostic_result(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_environment()
        assert isinstance(result, diag.DiagnosticResult)
        assert result.name == "01_env"


# ---------------------------------------------------------------------------
# ② Registry 一致性诊断
# ---------------------------------------------------------------------------


class TestDiagnoseRegistry:
    """Registry 一致性：3 场景字段 + Contract 对齐。"""

    def test_ok_in_current_repo(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_registry(REPO_ROOT)
        # 当前 main / PR-C branch 都有完整 Registry
        assert result.status == diag.Status.OK, (
            f"Registry 不一致：{result.details}\nfix_hint: {result.fix_hint}"
        )
        assert "3 场景" in result.summary

    def test_lists_three_scenarios(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_registry(REPO_ROOT)
        # 详情里 3 个 bullet
        bullet_lines = [ln for ln in result.details if ln.startswith("  • ")]
        assert len(bullet_lines) == 3
        sids = {ln.split()[1] for ln in bullet_lines}
        assert sids == {"telephone_risk", "cctv_surveillance_suspicious", "delivery_courier_normal"}

    def test_handles_missing_registry(self, monkeypatch, tmp_path: Path) -> None:
        """Registry 模块不存在时 → Status.FAIL + 修复提示。"""
        diag = _load_diag()

        # 用 fake_import 阻断 silver_demo.product_scenarios 的加载
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "silver_demo.product_scenarios" or name.startswith(
                "silver_demo.product_scenarios."
            ):
                raise ImportError("simulated registry unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        sys.modules.pop("silver_demo.product_scenarios", None)

        result = diag.diagnose_registry(tmp_path)
        assert result.status == diag.Status.FAIL
        assert (
            "product_scenarios" in result.summary
            or "无法加载" in result.summary
            or "导入" in result.summary
        )
        assert result.fix_hint is not None


# ---------------------------------------------------------------------------
# ③ 场景 yaml 完整性诊断
# ---------------------------------------------------------------------------


class TestDiagnoseScenarioYamls:
    """场景 yaml 完整性。"""

    def test_ok_in_current_repo(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_scenario_yamls(REPO_ROOT)
        assert result.status == diag.Status.OK, (
            f"yaml 不完整：{result.summary}\n{result.details}"
        )

    def test_detects_missing_yaml(self, monkeypatch, tmp_path: Path) -> None:
        """Registry 指向不存在的 YAML → Status.FAIL。"""
        diag = _load_diag()

        # 关键：直接 patch 模块属性（不能 string-path，否则 monkeypatch 找不到对象）。
        # 构造 1 个指向不存在 YAML 的虚拟场景 → diagnose_scenario_yamls 会 fail。
        from dataclasses import dataclass

        @dataclass
        class _FakeScenario:
            scenario_id: str = "fake"
            scenario_yaml: str = "config/demo/scenarios/__nonexistent__.yaml"

        from silver_demo import product_scenarios as ps_mod  # 延迟导入

        monkeypatch.setattr(
            ps_mod, "list_product_scenarios", lambda: (_FakeScenario(),)
        )

        empty_repo = tmp_path / "empty_demo"
        empty_repo.mkdir()
        result = diag.diagnose_scenario_yamls(empty_repo)
        assert result.status == diag.Status.FAIL
        assert "YAML" in result.summary or "不存在" in result.summary or "完整" in result.summary


# ---------------------------------------------------------------------------
# ④ 媒体资产诊断
# ---------------------------------------------------------------------------


class TestDiagnoseMediaAssets:
    """媒体资产存在性 + gitignore 资产缺失提示。"""

    def test_ok_when_assets_present(self) -> None:
        diag = _load_diag()
        result = diag.diagnose_media_assets(REPO_ROOT)
        # 当前仓库已有 CCTV_Surveillance_Final.mp4 + Delivery_Courier_Final.mp4 + telephone_risk audio
        assert result.status == diag.Status.OK, (
            f"媒体缺失：{result.summary}\n{result.details}\nfix: {result.fix_hint}"
        )
        assert "就位" in result.summary

    def test_human_size(self) -> None:
        diag = _load_diag()
        assert diag._human_size(512) == "512 B"
        assert "KB" in diag._human_size(2048)
        assert "MB" in diag._human_size(2 * 1024 ** 2)
        assert "GB" in diag._human_size(2 * 1024 ** 3)


# ---------------------------------------------------------------------------
# ⑤ 端口占用诊断
# ---------------------------------------------------------------------------


def _find_free_port() -> int:
    """找一个当前空闲的端口（让 diagnose_port 默认场景 OK）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestDiagnosePort:
    """端口占用探测。"""

    def test_ok_when_port_free(self) -> None:
        diag = _load_diag()
        free_port = _find_free_port()
        result = diag.diagnose_port(port=free_port)
        assert result.status == diag.Status.OK
        assert "空闲" in result.summary

    def test_fail_when_port_occupied(self) -> None:
        """占住一个端口，diagnose_port 应该报 FAIL。"""
        diag = _load_diag()
        # 占住一个端口
        holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            holder.bind(("127.0.0.1", 0))
            occupied_port = holder.getsockname()[1]
            holder.listen(1)
            result = diag.diagnose_port(port=occupied_port)
            assert result.status == diag.Status.FAIL
            assert "占用" in result.summary
            assert result.fix_hint is not None
            assert "DEMO_PORT" in result.fix_hint
        finally:
            holder.close()


# ---------------------------------------------------------------------------
# ⑥ 总入口 run_all_diagnostics + print_diagnostics
# ---------------------------------------------------------------------------


class TestRunAllDiagnostics:
    """``run_all_diagnostics`` 返回 5 项 + 顺序稳定。"""

    def test_returns_five_results(self) -> None:
        diag = _load_diag()
        results = diag.run_all_diagnostics(repo_root=REPO_ROOT)
        assert len(results) == 5

    def test_order_stable(self) -> None:
        diag = _load_diag()
        results = diag.run_all_diagnostics(repo_root=REPO_ROOT)
        names = [r.name for r in results]
        assert names == ["01_env", "02_registry", "03_yaml", "04_media", "05_port"]

    def test_skip_environment(self) -> None:
        diag = _load_diag()
        results = diag.run_all_diagnostics(
            repo_root=REPO_ROOT, skip_environment=True
        )
        assert len(results) == 4
        assert results[0].name == "02_registry"


class TestPrintDiagnostics:
    """``print_diagnostics`` 输出格式 + 退出码。"""

    def test_exit_zero_when_all_ok(self, capsys) -> None:
        diag = _load_diag()
        results = diag.run_all_diagnostics(
            repo_root=REPO_ROOT, port=_find_free_port()
        )
        # 当前仓库 + 空闲端口 → 全 OK
        ok_results = [r for r in results if r.status == diag.Status.OK]
        assert len(ok_results) == len(results), (
            f"期望全 OK，实际：{[(r.name, r.status.value) for r in results]}"
        )

        rc = diag.print_diagnostics(results)
        captured = capsys.readouterr()
        assert rc == 0
        assert "Diagnostic Report" in captured.out
        assert "所有诊断通过" in captured.out

    def test_exit_one_when_fatal(self, capsys) -> None:
        diag = _load_diag()
        # 手动造一个 FAIL 结果
        results = [
            diag.DiagnosticResult(
                name="99_fake",
                title="fake fail",
                status=diag.Status.FAIL,
                summary="simulated",
                fix_hint="fix me",
            )
        ]
        rc = diag.print_diagnostics(results)
        captured = capsys.readouterr()
        assert rc == 1
        assert "致命问题" in captured.out


# ---------------------------------------------------------------------------
# ⑦ Gateway 错误响应增强（PR-C）
# ---------------------------------------------------------------------------


class TestRegistryWhitelistHint:
    """``gateway._registry_whitelist_hint`` 返回白名单。"""

    def test_returns_whitelist_in_current_repo(self) -> None:
        gw = _load_gateway()
        hint = gw._registry_whitelist_hint()
        assert "telephone_risk" in hint
        assert "cctv_surveillance_suspicious" in hint
        assert "delivery_courier_normal" in hint
        assert "RAISED" in hint and "WARN" in hint and "MONITOR" in hint

    def test_returns_empty_string_when_registry_unavailable(self, monkeypatch) -> None:
        """Registry 模块不存在时容错返回空串（不阻塞错误响应）。"""
        gw = _load_gateway()

        # monkeypatch 让 product_scenarios import 抛错
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "silver_demo.product_scenarios":
                raise ImportError("simulated registry unavailable")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        sys.modules.pop("silver_demo.product_scenarios", None)

        hint = gw._registry_whitelist_hint()
        assert hint == ""


class TestCategorizeSwitchFailure:
    """``gateway._categorize_switch_failure`` 分类错误。"""

    def _make_scenario(self, media="", audio=""):
        from types import SimpleNamespace

        return SimpleNamespace(
            media_path=media, audio_path=audio, audio_replay_path=""
        )

    def test_categorizes_filenotfound_as_media(self) -> None:
        gw = _load_gateway()
        sc = self._make_scenario(
            media="dataset/benign/media/missing.mp4", audio=""
        )
        exc = FileNotFoundError("dataset/benign/media/missing.mp4")
        category, fix = gw._categorize_switch_failure(exc, sc)
        assert category == "media_missing"
        assert "missing.mp4" in fix

    def test_categorizes_audio_path_error(self) -> None:
        gw = _load_gateway()
        sc = self._make_scenario(
            media="dataset/benign/media/x.mp4",
            audio="dataset/_canonical/audio_semantic/missing.wav",
        )
        # exc message 必须含 "audio" 关键字才能让 _categorize_switch_failure
        # 走 audio 分支（实现基于关键字匹配）
        exc = FileNotFoundError(
            "audio file missing: dataset/_canonical/audio_semantic/missing.wav"
        )
        category, fix = gw._categorize_switch_failure(exc, sc)
        assert category == "audio_invalid"
        assert "missing.wav" in fix or "音频" in fix

    def test_categorizes_pipeline_error(self) -> None:
        gw = _load_gateway()
        sc = self._make_scenario()
        exc = RuntimeError("YOLO weights not found")
        category, fix = gw._categorize_switch_failure(exc, sc)
        assert category == "pipeline"
        assert "YOLO" in fix or "weights" in fix

    def test_categorizes_unknown_error(self) -> None:
        gw = _load_gateway()
        sc = self._make_scenario()
        exc = ValueError("weird")
        category, _ = gw._categorize_switch_failure(exc, sc)
        assert category == "unknown"