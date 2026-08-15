"""银龄盾 Demo 统一启动器（P0-11 演示可复现性入口）。

典型用法
--------
    python scripts/run_demo.py                       # 默认 night_visit 场景（CAVIAR 帧）
    python scripts/run_demo.py --scenario delivery_courier_normal
    python scripts/run_demo.py --video data/demo/my_door.mp4
    python scripts/run_demo.py --check               # 仅做环境预检，不启动

设计要点
--------
1. **先预检，后加载**：环境检查（check_env）只用标准库，绝不 import torch；
   通过后才懒加载 ``silver_demo.gateway``（其顶层 import 会拉 torch）。
2. **优雅降级**：视频素材 / CAVIAR 帧不入库（gitignore），缺失时给出明确指引，
   而不是静默崩在 YOLO 装配阶段。
3. **复现路径**：见 ``docs/DEVELOPMENT_ENV.md``。

依赖：AI 运行时（torch / ultralytics / opencv）须装在当前 Python
（当前为 system Python 3.14；managed venv 只放工具链）。
"""

from __future__ import annotations

import argparse
import atexit
import os
import sys
from pathlib import Path

import yaml  # 轻依赖，仅做场景解析，不触发 torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 便于 import check_env

from check_env import run_checks  # 纯标准库，不拉 torch

DEFAULT_SCENARIO = "config/demo/scenarios/night_visit.yaml"
SCENARIOS_DIR = "config/demo/scenarios"
HP_CONFIG = "config/default.yaml"


def _read_yaml(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _caviar_base_dir() -> Path:
    """从 config/default.yaml 读取 caviar_base_dir（CAVIAR 帧根目录）。"""
    cfg = _read_yaml(ROOT / HP_CONFIG)
    raw = cfg.get("runtime", {}).get("caviar_base_dir", "tests/fixtures/doorway")
    p = Path(raw)
    return p if p.is_absolute() else (ROOT / p)


def resolve_scenario(args: argparse.Namespace) -> tuple[Path, Path | None]:
    """解析最终要启动的场景 yaml 路径。

    返回 (场景路径, 临时文件或 None)：当使用了 ``--video`` 覆盖时，第二项指向
    写入 ``data/demo/.run_demo_scenario.yaml`` 的临时文件，由调用方在退出时清理。
    """
    if args.scenario:
        s = args.scenario
        if s.endswith((".yaml", ".yml")):
            base = ROOT / s
        else:
            base = ROOT / SCENARIOS_DIR / f"{s}.yaml"
    else:
        base = ROOT / DEFAULT_SCENARIO

    if not base.is_file():
        sys.stderr.write(
            f"❌ 场景文件不存在：{base}\n"
            f"   可用场景（config/demo/scenarios/ 下）：night_visit / real_doorway\n"
            f"   或用 --scenario <name|.yaml> 指定，或用 --video <path> 直接接入本地视频。\n"
        )
        sys.exit(1)

    # --video 覆盖：基于已解析场景的播放参数，改 media_path 为本地视频
    if args.video:
        video = Path(args.video)
        if not video.is_absolute():
            video = ROOT / video
        data = _read_yaml(base)
        data["source_type"] = "video_file"
        data["media_path"] = str(video)
        data["source"] = video.stem
        # 写入 data/demo/（gitignore，不入库），避免污染仓库
        out_dir = ROOT / "data" / "demo"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / ".run_demo_scenario.yaml"
        with out_path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
        return out_path, out_path

    return base, None


def preflight_media(scenario_path: Path) -> None:
    """启动前检查媒体/帧是否存在，缺失给出明确指引（避免 YOLO 装配阶段才崩）。"""
    data = _read_yaml(scenario_path)
    source_type = data.get("source_type", "caviar_jpg")
    source = data.get("source", "")
    media_path = data.get("media_path", "")

    if source_type == "video_file":
        if not media_path:
            sys.stderr.write(
                "❌ 场景 YAML 中 source_type=video_file 但 media_path 为空\n"
                "   请在 YAML 中设置 media_path，或启动时用 --video <path> 指定本地视频。\n"
            )
            sys.exit(1)
        mp = Path(media_path) if Path(media_path).is_absolute() else (ROOT / media_path)
        if not mp.is_file():
            sys.stderr.write(
                f"❌ 视频文件缺失：{mp}\n"
                f"   请将演示视频放到该路径，或在启动时用 --video <path> 指定本地视频。\n"
                f"   例：python scripts/run_demo.py --video data/demo/my_door.mp4\n"
            )
            sys.exit(1)
        print(f"   视频源：{mp}  ✓")
        return

    # caviar_jpg：检查 caviar_base_dir/<source> 帧目录
    frame_dir = _caviar_base_dir() / source
    has_frames = frame_dir.is_dir() and any(frame_dir.iterdir())
    if not has_frames:
        sys.stderr.write(
            f"❌ CAVIAR 帧缺失：{frame_dir}\n"
            f"   CAVIAR 帧不入库（gitignore），需先拉取：\n"
            f"       python tests/fixtures/download_fixtures.py\n"
            f"   或用 --video <path> 直接接入本地视频：\n"
            f"       python scripts/run_demo.py --video data/demo/my_door.mp4\n"
        )
        sys.exit(1)
    print(f"   CAVIAR 帧：{frame_dir}  ✓")


def print_banner(scenario_path: Path) -> None:
    data = _read_yaml(scenario_path)
    print("\n" + "=" * 52)
    print("  银龄盾 Demo 网关启动中")
    print("=" * 52)
    print(f"  场景   : {data.get('scenario_id', '?')} ({data.get('source_type', 'caviar_jpg')})")
    print(f"  帧循环 : loop={data.get('loop', True)}  fps_target={data.get('fps_target', '?')}")
    print(f"  访问   : http://{os.environ.get('DEMO_HOST', '127.0.0.1')}:"
          f"{os.environ.get('DEMO_PORT', '8765')}/")
    print("  链路   : 视频→身份→轨迹→行为→风险→解释→干预")
    print("=" * 52 + "\n")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="银龄盾 Demo 统一启动器")
    p.add_argument("--scenario", help="场景名或 yaml 路径（默认 night_visit）")
    p.add_argument("--video", help="本地视频路径，覆盖场景的 media_path（video_file 源）")
    p.add_argument("--host", help="绑定地址（默认 127.0.0.1，或 DEMO_HOST）")
    p.add_argument("--port", help="绑定端口（默认 8765，或 DEMO_PORT）")
    p.add_argument("--check", action="store_true", help="仅做环境预检，不启动网关")
    p.add_argument(
        "--live",
        action="store_true",
        help="启用 Live 次级入口（/live + WS + /demo/*）；默认旗舰 Case Viewer 模式（/）",
    )
    return p.parse_args()


def _register_synthetic_source() -> None:
    """把 ADR-0032 合成帧源注册进 demo 帧源表（组装层接线）。

    这里是**依赖倒置的汇合点**：``silver_demo`` 不 import
    ``home_perception.validation``（否则撞 ADR-0015 §5 冻结 import 白名单），
    ``validation`` 也不 import ``silver_demo``；由本脚本——既不属于前者包内、
    也不属于后者——把 builder 递进去。

    合成源是**可选**能力：validation 层缺失（例如精简部署）时静默跳过，
    demo 照常以 CAVIAR / MP4 启动，不因此失败。
    """
    try:
        from home_perception.validation.demo_adapter import install_into
        from silver_demo.sources import register_frame_source
    except ImportError as exc:  # pragma: no cover - 精简部署降级路径
        print(f"[i] 合成帧源不可用，跳过注册（{exc}）")
        return
    install_into(register_frame_source, replace=True)


def _run_live(args: argparse.Namespace) -> None:
    """Legacy Live 模式：既有实时 Dashboard + WS + YOLO（/live 次级入口）。"""
    scenario_path, temp_path = resolve_scenario(args)
    # --video 产生的临时点文件：启动失败（含 preflight_media 的 sys.exit）时清理
    if temp_path is not None:
        atexit.register(lambda: temp_path.unlink(missing_ok=True))
    preflight_media(scenario_path)

    os.environ["DEMO_SCENARIO"] = str(scenario_path)
    if args.host:
        os.environ["DEMO_HOST"] = args.host
    if args.port:
        os.environ["DEMO_PORT"] = str(args.port)
    os.environ["DEMO_LIVE"] = "1"  # 暴露 /live + WS + /demo/*

    print_banner(scenario_path)

    import silver_demo.gateway as gw  # 懒加载：预检通过后才 import（会拉 torch）

    _register_synthetic_source()
    gw.main()


def _try_build_case_viewer() -> Path | None:
    """尽力用 Trusted Case Factory 生成 high_risk 案例的 case_viewer.html 到 demo/ 目录。

    仅用自包含的合成 high_risk fixture（adr0034_high_risk.yaml，无外部帧依赖），
    故本地即可确定性构建。返回产物目录；失败（torch / YOLO 权重缺失等）返回 None，
    由调用方降级（/ 显示引导提示页）。
    """
    out_dir = ROOT / "src" / "silver_demo" / "demo"
    src_yaml = (
        ROOT / "src" / "home_perception" / "validation" / "fixtures"
        / "scenarios" / "integration" / "adr0034_high_risk.yaml"
    )
    if not src_yaml.is_file():
        print("[i] 未找到 high_risk fixture，跳过自动构建。")
        return None

    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="ss_case_"))
    try:
        shutil.copyfile(src_yaml, tmp / "adr0034_high_risk.yaml")
        try:
            from build_trusted_case import main as build_main
        except ImportError as exc:  # pragma: no cover - 精简部署降级路径
            print(f"[i] 无法加载 build_trusted_case，跳过自动构建（{exc}）")
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[*] 自动构建旗舰 Case Viewer（high_risk）→ {out_dir} ...")
        rc = build_main(["--scenarios", str(tmp), "--out-dir", str(out_dir)])
        if rc not in (0, None):
            print(f"[!] 自动构建返回非零退出码 {rc}，降级启动。")
            return None
    except Exception as exc:  # noqa: BLE001
        print(f"[!] 自动构建 Case Viewer 失败，降级启动（/ 显示引导提示）：{exc}")
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_dir if (out_dir / "case_viewer.html").is_file() else None


def _run_flagship(args: argparse.Namespace) -> None:
    """旗舰模式：静态托管 Factory 预渲染的 Case Viewer（/），不依赖 runtime、不 import visualizer。"""
    os.environ["DEMO_LIVE"] = "0"  # 确定性关闭 Live 次级入口
    if args.host:
        os.environ["DEMO_HOST"] = args.host
    if args.port:
        os.environ["DEMO_PORT"] = str(args.port)

    # 确定 case_artifacts_dir：优先 DEMO_CASE_ARTIFACTS；否则尽力本地构建 high_risk 案例
    artifacts_dir = os.environ.get("DEMO_CASE_ARTIFACTS")
    if not artifacts_dir or not (Path(artifacts_dir) / "case_viewer.html").is_file():
        built = _try_build_case_viewer()
        if built is not None:
            artifacts_dir = str(built)
            os.environ["DEMO_CASE_ARTIFACTS"] = artifacts_dir

    host = os.environ.get("DEMO_HOST", "127.0.0.1")
    port = os.environ.get("DEMO_PORT", "8765")
    print("\n" + "=" * 52)
    print("  银龄盾 Demo 网关启动中 · 旗舰模式")
    print("=" * 52)
    print("  入口   : /  (Verified Cases · 可信案例回放)")
    print("  来源   : Trusted Case Factory 预渲染（CI / build_trusted_case）")
    if artifacts_dir and (Path(artifacts_dir) / "case_viewer.html").is_file():
        print(f"  产物   : {artifacts_dir}/case_viewer.html  ✓")
    else:
        print("  产物   : 未构建 → / 显示引导提示页（先跑 build_trusted_case 或 --live）")
    print(f"  访问   : http://{host}:{port}/")
    print("  Live   : /live（需 --live 或 DEMO_LIVE=1，当前关闭）")
    print("=" * 52 + "\n")

    import silver_demo.gateway as gw  # 懒加载：预检通过后才 import
    gw.main()


def main() -> None:
    args = parse_args()

    # 1) 环境预检（纯标准库，不拉 torch）
    ok, lines, missing = run_checks()
    print("银龄盾 Demo · 环境预检")
    print("=" * 48)
    for ln in lines:
        print(ln)
    if not ok:
        print("\n❌ 缺少运行依赖，请先安装：")
        seen: set[str] = set()
        for disp, cat, hint in missing:
            print(f"  - {disp}")
            if hint not in seen:
                print(f"      安装：{hint}")
                seen.add(hint)
        sys.exit(1)
    if args.check:
        print("\n✅ 环境检查通过。")
        sys.exit(0)

    # Task 0 双入口：--live 走 Legacy 实时；默认走旗舰 Case Viewer
    if args.live:
        _run_live(args)
    else:
        _run_flagship(args)


if __name__ == "__main__":
    main()
