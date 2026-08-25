"""银龄盾 Demo 自检诊断（演示可复现性 · 比赛现场可用）。

PR-C · Task Contract
=====================

What changes:
    scripts/demo_diagnostics.py —— 5 项诊断模块（环境 / Registry / yaml / 媒体 / 端口）
    scripts/run_demo.py         —— 新增 ``--diagnose`` CLI + 启动 banner 扩展
    src/silver_demo/gateway.py  —— /demo/scenario 错误响应增强（详细原因）
    tests/demo/test_demo_diagnostics.py —— 单元测试（mock 各种缺失场景）

How to verify:
    python scripts/run_demo.py --diagnose     # 干净环境跑过，所有 5 项 ✓
    pytest tests/demo/test_demo_diagnostics.py -v  # 单测全过

Feedback signals:
    - 成功：所有 5 项 ✓ + exit 0
    - 失败：每项 ✗ 含具体修复命令 + exit 1
    - 部分缺失（视频未就位）：⚠ WARN + 降级运行提示 + exit 0

Done 条件：
    - --diagnose 模式 5 项报告完整
    - 启动 banner 显示 3 场景就绪状态
    - ruff check src tests 全过
    - pytest tests/demo/test_demo_diagnostics.py 全过

边界（§6.3 #3 单一职责）：
    - 不动 pipeline 业务逻辑
    - 不动 Contract 类
    - 不修改任何架构决策文件（AGENTS.md / docs/02 / docs/08 / ADR）

设计原则
========

1. **零重依赖**：仅用标准库 + yaml（项目内已有轻依赖）；不引入 pytest 之外的任何包。
2. **独立可跑**：``python scripts/demo_diagnostics.py`` 直接运行，无需 torch / opencv。
3. **CI 友好**：诊断结果 ``exit 0``（全 OK / 仅 WARN）/ ``exit 1``（有 FAIL）。
4. **演示友好**：输出对齐 + 修复命令 + 端口占用 PID 提示，比赛现场可直接用。
"""
from __future__ import annotations

import socket
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

# ---------------------------------------------------------------------------
# 结果模型
# ---------------------------------------------------------------------------


class Status(str, Enum):
    """诊断单项状态。"""

    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


_OK_MARK = "✓"
_WARN_MARK = "⚠"
_FAIL_MARK = "✗"

STATUS_MARK: dict[Status, str] = {
    Status.OK: _OK_MARK,
    Status.WARN: _WARN_MARK,
    Status.FAIL: _FAIL_MARK,
}


@dataclass
class DiagnosticResult:
    """单项诊断结果。"""

    name: str
    title: str
    status: Status
    summary: str
    details: list[str] = field(default_factory=list)
    fix_hint: str | None = None

    def is_ok(self) -> bool:
        return self.status == Status.OK

    def is_fatal(self) -> bool:
        return self.status == Status.FAIL


# ---------------------------------------------------------------------------
# ① 环境诊断（复用 check_env.run_checks）
# ---------------------------------------------------------------------------


def diagnose_environment() -> DiagnosticResult:
    """诊断 ①：环境依赖（runtime / web / test 三类）。

    复用 ``scripts/check_env.py::run_checks()``（已存在，零重依赖）。
    """
    # 延迟 import：check_env 与本模块同目录，避免脚本入口污染 sys.path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        from check_env import run_checks
    except ImportError as exc:
        return DiagnosticResult(
            name="01_env",
            title="环境（runtime/web/test）",
            status=Status.FAIL,
            summary=f"无法加载 check_env.py：{exc}",
            fix_hint="确认 scripts/check_env.py 存在且无语法错误",
        )

    ok, lines, missing = run_checks()
    if ok:
        return DiagnosticResult(
            name="01_env",
            title="环境（runtime/web/test）",
            status=Status.OK,
            summary=f"{len(lines) - 1} 类依赖全部就位（torch / opencv / ultralytics / fastapi / uvicorn / pytest 等）",
        )

    fix = "\n".join(f"      - {disp}：{hint}" for disp, _cat, hint in missing)
    return DiagnosticResult(
        name="01_env",
        title="环境（runtime/web/test）",
        status=Status.FAIL,
        summary=f"缺少 {len(missing)} 项依赖",
        details=[ln.strip() for ln in lines if ln.strip()],
        fix_hint=fix or "pip install -e \".[demo]\"",
    )


# ---------------------------------------------------------------------------
# ② Registry 一致性诊断
# ---------------------------------------------------------------------------


def diagnose_registry(repo_root: Path) -> DiagnosticResult:
    """诊断 ②：Product Scenario Registry 一致性。

    校验：
      - 3 场景字段非空（scenario_id / display_name / scenario_yaml / expected_product_result / Contract）
      - Contract 模块可 import + 类存在
      - expected_product_result ∈ {RAISED, WARN, MONITOR}
      - YAML 文件存在
    """
    sys.path.insert(0, str(repo_root))
    try:
        from silver_demo.product_scenarios import (
            get_product_scenario,
            list_product_scenarios,
        )
    except ImportError as exc:
        return DiagnosticResult(
            name="02_registry",
            title="Registry 一致性（3 场景 + Contract 对齐）",
            status=Status.FAIL,
            summary=f"无法加载 product_scenarios：{exc}",
            fix_hint="确认 src/silver_demo/product_scenarios.py 无语法错误",
        )

    allowed_results = {"RAISED", "WARN", "MONITOR"}
    scenarios = list_product_scenarios()
    details: list[str] = []
    fails: list[str] = []

    if len(scenarios) != 3:
        fails.append(f"Registry 项数={len(scenarios)} ≠ 3（frozen 冻结白名单）")

    for ps in scenarios:
        line = f"  • {ps.scenario_id} [{ps.expected_product_result}]  → {ps.display_name}"
        details.append(line)

        # 字段非空
        for f_name in ("scenario_id", "display_name", "scenario_yaml", "contract_module", "contract_class"):
            if not getattr(ps, f_name, ""):
                fails.append(f"{ps.scenario_id}.{f_name} 为空")

        # expected_product_result 枚举校验
        if ps.expected_product_result not in allowed_results:
            fails.append(
                f"{ps.scenario_id}.expected_product_result={ps.expected_product_result!r} "
                f"∉ {sorted(allowed_results)}"
            )

        # YAML 存在
        yaml_p = repo_root / ps.scenario_yaml
        if not yaml_p.is_file():
            fails.append(f"{ps.scenario_id}.scenario_yaml={ps.scenario_yaml} 不存在")

        # Contract 可 import + 类存在
        try:
            mod = __import__(ps.contract_module, fromlist=["__name__"])
            cls = getattr(mod, ps.contract_class, None)
            if cls is None:
                fails.append(
                    f"{ps.scenario_id}.contract_class={ps.contract_class} 不在 {ps.contract_module}"
                )
        except ImportError as exc:
            fails.append(f"{ps.scenario_id}.contract_module={ps.contract_module} import 失败：{exc}")

    # get_product_scenario round-trip
    for ps in scenarios:
        if get_product_scenario(ps.scenario_id) is None:
            fails.append(f"get_product_scenario({ps.scenario_id}) 返回 None")

    if fails:
        return DiagnosticResult(
            name="02_registry",
            title="Registry 一致性（3 场景 + Contract 对齐）",
            status=Status.FAIL,
            summary=f"{len(fails)} 项 Registry 不一致",
            details=fails,
            fix_hint="运行 pytest tests/visualizer/test_scenario_config_integrity.py 查看详细差异",
        )

    return DiagnosticResult(
        name="02_registry",
        title="Registry 一致性（3 场景 + Contract 对齐）",
        status=Status.OK,
        summary=f"{len(scenarios)} 场景字段完整 + Contract 类可 import + 枚举对齐",
        details=details,
    )


# ---------------------------------------------------------------------------
# ③ 场景 yaml 完整性诊断
# ---------------------------------------------------------------------------


# 必需字段（基线；每个场景都应满足）
REQUIRED_YAML_FIELDS: tuple[str, ...] = (
    "scenario_id",
    "source",
    "source_type",
)

# 媒体字段（按 source_type 决定是否必需）
_VIDEO_REQUIRED: tuple[str, ...] = ("media_path",)
_AUDIO_OPTIONAL: tuple[str, ...] = ("audio_path", "audio_replay_path")


def diagnose_scenario_yamls(repo_root: Path) -> DiagnosticResult:
    """诊断 ③：3 场景 YAML 字段完整性。

    校验：
      - YAML 文件可解析
      - scenario_id 与 Registry 一致
      - 必需字段非空（scenario_id / source / source_type）
      - source_type=video_file → media_path 非空 + 存在（与诊断 ④ 联动）
    """
    sys.path.insert(0, str(repo_root))
    try:
        import yaml  # 已有轻依赖

        from silver_demo.product_scenarios import list_product_scenarios
    except ImportError as exc:
        return DiagnosticResult(
            name="03_yaml",
            title="场景 yaml 完整性（3 场景字段）",
            status=Status.FAIL,
            summary=f"依赖导入失败：{exc}",
        )

    scenarios = list_product_scenarios()
    details: list[str] = []
    fails: list[str] = []

    for ps in scenarios:
        yaml_p = repo_root / ps.scenario_yaml
        line_prefix = f"  • {ps.scenario_id}"

        if not yaml_p.is_file():
            fails.append(f"{ps.scenario_id}: YAML 文件不存在 {yaml_p}")
            details.append(f"{line_prefix}: YAML ✗")
            continue

        try:
            data = yaml.safe_load(yaml_p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            fails.append(f"{ps.scenario_id}: YAML 解析失败 {exc}")
            details.append(f"{line_prefix}: 解析 ✗")
            continue

        # scenario_id 一致性
        yaml_sid = data.get("scenario_id", "")
        if yaml_sid != ps.scenario_id:
            fails.append(
                f"{ps.scenario_id}: YAML scenario_id={yaml_sid!r} 与 Registry 不一致"
            )

        # 必需字段
        missing_fields = [f for f in REQUIRED_YAML_FIELDS if not data.get(f)]
        if missing_fields:
            fails.append(f"{ps.scenario_id}: 缺少必需字段 {missing_fields}")

        # source_type=video_file → media_path 非空
        source_type = data.get("source_type", "")
        media_path = data.get("media_path", "")
        if source_type == "video_file" and not media_path:
            fails.append(f"{ps.scenario_id}: source_type=video_file 但 media_path 为空")

        # 字段统计
        has_audio = bool(data.get("audio_path") or data.get("audio_replay_path"))
        details.append(
            f"{line_prefix}: source={data.get('source', '?')} "
            f"type={source_type or '?'} "
            f"video={'✓' if media_path else '✗'} "
            f"audio={'✓' if has_audio else '—（无音频轨）'}"
        )

    if fails:
        return DiagnosticResult(
            name="03_yaml",
            title="场景 yaml 完整性（3 场景字段）",
            status=Status.FAIL,
            summary=f"{len(fails)} 项 YAML 字段不完整",
            details=details + [""] + ["问题："] + [f"  - {f}" for f in fails],
            fix_hint="参见 docs/scenarios/MATRIX.md §SSOT 字段规范",
        )

    return DiagnosticResult(
        name="03_yaml",
        title="场景 yaml 完整性（3 场景字段）",
        status=Status.OK,
        summary=f"{len(scenarios)} 场景字段完整（scenario_id / source / source_type / media_path / audio_path）",
        details=details,
    )


# ---------------------------------------------------------------------------
# ④ 媒体资产诊断
# ---------------------------------------------------------------------------


def _human_size(n_bytes: int) -> str:
    """人类可读文件大小（KB/MB/GB）。"""
    if n_bytes < 1024:
        return f"{n_bytes} B"
    for unit in ("KB", "MB", "GB"):
        n = n_bytes / 1024 ** (["KB", "MB", "GB"].index(unit) + 1)
        if n < 1024:
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TB"


def diagnose_media_assets(repo_root: Path) -> DiagnosticResult:
    """诊断 ④：媒体资产（视频 / 音频）存在性 + 大小。

    gitignore 资产（``dataset/benign/media/*.mp4``）缺失时给明确修复指引：
    - 视频缺失：报绝对路径 + 期望放置位置
    - 音频路径不存在：报校验失败

    缺失不视为 fatal（warn 级别）—— 比赛现场可降级用 synthetic 源。
    """
    sys.path.insert(0, str(repo_root))
    try:
        import yaml

        from silver_demo.product_scenarios import list_product_scenarios
    except ImportError as exc:
        return DiagnosticResult(
            name="04_media",
            title="媒体资产（3 场景视频/音频）",
            status=Status.FAIL,
            summary=f"依赖导入失败：{exc}",
        )

    scenarios = list_product_scenarios()
    details: list[str] = []
    warns: list[str] = []
    asset_root = repo_root / "dataset"

    for ps in scenarios:
        yaml_p = repo_root / ps.scenario_yaml
        if not yaml_p.is_file():
            details.append(f"  • {ps.scenario_id}: YAML 缺失，跳过媒体检查")
            warns.append(f"{ps.scenario_id}: YAML 缺失")
            continue

        try:
            data = yaml.safe_load(yaml_p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue

        source_type = data.get("source_type", "")
        media_path_str = data.get("media_path", "")
        audio_path_str = data.get("audio_path", "")
        audio_replay_str = data.get("audio_replay_path", "")

        # 视频
        video_ok = True
        video_size = ""
        if source_type == "video_file":
            if not media_path_str:
                video_ok = False
                warns.append(f"{ps.scenario_id}: media_path 为空（YAML 字段缺失）")
            else:
                mp = Path(media_path_str)
                if not mp.is_absolute():
                    mp = repo_root / mp
                if not mp.is_file():
                    video_ok = False
                    warns.append(
                        f"{ps.scenario_id}: 视频缺失 {mp}"
                    )
                else:
                    video_size = _human_size(mp.stat().st_size)

        # 音频（仅在声明时检查）
        audio_ok: bool | None = None  # None = 未声明
        audio_size = ""
        if audio_path_str or audio_replay_str:
            audio_path = audio_path_str or audio_replay_str
            ap = Path(audio_path)
            if not ap.is_absolute():
                ap = repo_root / ap
            if ap.is_file():
                audio_ok = True
                audio_size = _human_size(ap.stat().st_size)
            else:
                audio_ok = False
                warns.append(f"{ps.scenario_id}: 音频缺失 {ap}")

        # 详情行
        v_mark = _OK_MARK if video_ok else _FAIL_MARK
        a_part = "—（无音频轨）" if audio_ok is None else f"{_OK_MARK if audio_ok else _FAIL_MARK} ({audio_size})"
        v_part = f"{v_mark} ({video_size})" if video_ok and video_size else f"{v_mark}"

        details.append(
            f"  • {ps.scenario_id}: video {v_part} · audio {a_part}"
        )

    if warns:
        return DiagnosticResult(
            name="04_media",
            title="媒体资产（3 场景视频/音频）",
            status=Status.WARN,
            summary=f"{len(warns)} 项资产缺失（可降级 synthetic 源继续演示）",
            details=details,
            fix_hint=(
                "gitignore 资产需手动准备；或启动时用 --video <path> 覆盖。\n"
                f"      期望目录：{asset_root}/benign/media/"
            ),
        )

    return DiagnosticResult(
        name="04_media",
        title="媒体资产（3 场景视频/音频）",
        status=Status.OK,
        summary=f"{len(scenarios)} 场景视频/音频资产全部就位",
        details=details,
    )


# ---------------------------------------------------------------------------
# ⑤ 端口占用诊断
# ---------------------------------------------------------------------------


def diagnose_port(port: int = 8765, host: str = "127.0.0.1") -> DiagnosticResult:
    """诊断 ⑤：端口占用（默认 8765，demo 网关绑定端口）。

    用 ``socket.connect_ex`` 探测端口可达性；若占用列出持有进程（仅 Windows / 类 Unix 尽力）。
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.5)
    try:
        rc = sock.connect_ex((host, port))
        if rc == 0:
            # 端口已被占用
            pid_info = _try_find_holder_pid(port)
            return DiagnosticResult(
                name="05_port",
                title=f"端口 {port} 占用",
                status=Status.FAIL,
                summary=f"端口 {port} 已被占用（启动将失败 bind）",
                details=[f"  占用方：{pid_info}" if pid_info else "  占用方：（平台不支持探测）"],
                fix_hint=(
                    "      - 找到占用进程后停止；或换端口启动：DEMO_PORT=<other> python scripts/run_demo.py --live"
                ),
            )
    finally:
        sock.close()

    return DiagnosticResult(
        name="05_port",
        title=f"端口 {port} 占用",
        status=Status.OK,
        summary=f"端口 {port} 空闲（可启动 demo 网关）",
    )


def _try_find_holder_pid(port: int) -> str | None:
    """尽力探测占用端口的进程 PID（Windows + 类 Unix 双平台）。

    返回持有进程描述（"PID 1234 (python.exe)"）或探测失败时返回 None。
    """
    import platform
    import subprocess

    system = platform.system()
    try:
        if system == "Windows":
            cmd = ["netstat", "-ano", "-p", "TCP"]
            out = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                encoding="gbk",
                errors="replace",
            ).stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        return f"PID {parts[-1]}（netstat）"
        else:
            cmd = ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"]
            out = subprocess.run(
                cmd, capture_output=True, text=True, timeout=2, check=False
            ).stdout.strip()
            if out:
                return f"PID {out.splitlines()[0]}（lsof）"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    return None


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------


def run_all_diagnostics(
    repo_root: Path | None = None,
    port: int = 8765,
    skip_environment: bool = False,
) -> list[DiagnosticResult]:
    """运行 5 项诊断并按顺序返回结果。

    Args:
        repo_root: 仓库根路径；None 时用 ``Path.cwd()``
        port: 端口探测目标
        skip_environment: 跳过环境诊断（CI 中 torch 已确认安装时省时间）
    """
    root = (repo_root or Path.cwd()).resolve()
    results: list[DiagnosticResult] = []
    if not skip_environment:
        results.append(diagnose_environment())
    results.append(diagnose_registry(root))
    results.append(diagnose_scenario_yamls(root))
    results.append(diagnose_media_assets(root))
    results.append(diagnose_port(port=port))
    return results


def print_diagnostics(results: Iterable[DiagnosticResult]) -> int:
    """格式化打印诊断报告。返回进程退出码（0=OK/WARN, 1=FAIL）。"""
    results = list(results)
    if not results:
        print("（无诊断结果）")
        return 0

    print("\n银龄盾 Demo · 自检诊断（Diagnostic Report）")
    print("=" * 72)

    ok_count = 0
    warn_count = 0
    fail_count = 0

    for r in results:
        mark = STATUS_MARK[r.status]
        # 标题行
        print(f"[{r.name}] {r.title}  {mark} {r.summary}")
        # 详情
        for line in r.details:
            print(line)
        # 修复提示
        if r.fix_hint:
            print("  修复：")
            print(r.fix_hint)
        # 空行
        print()

        if r.status == Status.OK:
            ok_count += 1
        elif r.status == Status.WARN:
            warn_count += 1
        else:
            fail_count += 1

    print("=" * 72)
    overall = (
        f"✓ {ok_count} OK"
        + (f" · ⚠ {warn_count} WARN" if warn_count else "")
        + (f" · ✗ {fail_count} FAIL" if fail_count else "")
    )
    if fail_count:
        print(f"整体: {overall} · 存在致命问题，请修复后再启动 demo")
    elif warn_count:
        print(f"整体: {overall} · 可降级继续演示，建议尽快补齐")
    else:
        print(f"整体: {overall} · 所有诊断通过")

    return 1 if fail_count else 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：``python scripts/demo_diagnostics.py [port]``。"""
    port = 8765
    if argv is not None and len(argv) >= 1:
        try:
            port = int(argv[0])
        except ValueError:
            print(f"无效端口：{argv[0]}（应为整数）", file=sys.stderr)
            return 2
    results = run_all_diagnostics(port=port)
    return print_diagnostics(results)


if __name__ == "__main__":
    sys.exit(main())