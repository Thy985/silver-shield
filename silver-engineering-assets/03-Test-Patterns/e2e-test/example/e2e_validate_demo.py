"""银龄盾 Demo · 真实端到端验证（P0-11.5a/5b 验收）。

为什么需要这个脚本
------------------
PR #50（稳定 HIGH 闭环）与 PR #51（三视图）的验证此前只停在**合约层**
（单元测试 + 静态扫描）。本脚本做一次**真实链路**端到端跑通：

    真实 FastAPI 网关（assemble → 加载 YOLO → run_loop 真实视频帧）
        ↓ WebSocket（真实 ASGI + 真实 WS 协议，经 httpx 驱动，非 mock）
    真实 WS 客户端收集消息
        ↓ 断言
    HIGH 风险产生 + 家属命令 + 社区任务 + warning_id 贯通三视图 + 上行回写闭环

**不依赖浏览器**：验证的是「网关 → WS → 视图数据」这一真实协议链路
（Dashboard 的 DOM 渲染由独立单元测试覆盖）。这是比赛演示链路在
无浏览器基建下能达到的最强端到端验证。

运行环境
--------
必须在装有完整 AI 运行时（torch / ultralytics / opencv）的 Python 下跑，
即 system Python 3.14；managed venv 只放工具链，缺 torch 会自动 SKIP。
真实视频 data/demo/CCTV_Surveillance_Final.mp4 与场景 yaml 需在本地（gitignore）。

用法
----
    python scripts/e2e_validate_demo.py            # 默认 CCTV 高速场景
    python scripts/e2e_validate_demo.py --budget 90
    python scripts/e2e_validate_demo.py --scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml

退出码：0 = 全部通过；1 = 有断言失败；2 = 环境不满足（SKIP）。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _env_gate() -> bool:
    """检查是否具备真实运行时（torch + httpx）。不具备则 SKIP。"""
    try:
        import torch  # noqa: F401
    except Exception as exc:  # pragma: no cover
        print(f"SKIP: torch 不可用（请跑在 system Python 3.14）：{exc}")
        return False
    try:
        import httpx  # noqa: F401
        from fastapi.testclient import TestClient  # noqa: F401
    except Exception as exc:  # pragma: no cover
        print(f"SKIP: httpx / fastapi.testclient 不可用：{exc}")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="银龄盾 Demo 真实端到端验证")
    ap.add_argument("--scenario", default="config/demo/scenarios/cctv_surveillance_suspicious.yaml")
    ap.add_argument("--budget", type=float, default=90.0, help="收集帧的墙钟预算（秒）")
    ap.add_argument("--loop-interval", type=float, default=0.001,
                    help="帧循环间隔秒（小正数=全速，不走 1/fps 限速回退；勿改 scenario.fps_target）")
    args = ap.parse_args()

    if not _env_gate():
        return 2

    base_yaml = (ROOT / args.scenario) if not Path(args.scenario).is_absolute() else Path(args.scenario)
    if not base_yaml.is_file():
        print(f"❌ 场景文件不存在：{base_yaml}（CCTV 真实视频场景为本地 untracked，请确保 data/demo/CCTV_Surveillance_Final.mp4 在）")
        return 2

    from fastapi.testclient import TestClient
    from silver_demo.config import DemoSettings
    from silver_demo.gateway import create_app

    # 用真实 CCTV 场景（fps_target=8 → VideoFileFrameSource 跳帧 skip=3，
    # demo-time 重入间隔落在 frequency_window 内 → RepeatVisit 能触发）。
    # frame_loop_interval_s 设小正数 → 网关 run_loop 跳过 `1/fps_target` 限速回退，
    # 全速跑（YOLO 推理主导，GPU ~30ms/帧）。
    # 注意：绝不能把 scenario.fps_target 改成 0 —— 那会让 skip=1 读全帧，
    # demo-time 重入间隔被拉宽超过 frequency_window → visits_in_window 永远=1 → 不出 HIGH。
    ds = DemoSettings(
        scenario_path=str(base_yaml),
        frame_loop_interval_s=args.loop_interval,
        jpeg_quality=30,
    )
    app = create_app(ds)

    # 收集器
    seen = {
        "frames": 0,
        "warnings": {},        # warning_id -> dict(risk_level, reason_summary, has_family, has_community)
        "family_cmds": {},     # warning_id -> count
        "community_cmds": {},  # warning_id -> count
        "high_seen": False,
        "high_wids": [],
        "family_wids": [],
        "community_wids": [],
        "snapshot_received": False,
        "all_wids": set(),
    }

    def collect_frame(msg: dict) -> None:
        seen["frames"] += 1
        view = msg.get("view", {})
        # 警告
        for w in view.get("warnings", []) or []:
            if not isinstance(w, dict):
                continue
            wid = w.get("warning_id")
            if not wid:
                continue
            seen["all_wids"].add(wid)
            rl = w.get("risk_level")
            rec = seen["warnings"].setdefault(wid, {"risk_level": rl, "reason": w.get("reason_summary")})
            rec["risk_level"] = rl
            rec["reason"] = w.get("reason_summary")
            if rl == "HIGH":
                seen["high_seen"] = True
                if wid not in seen["high_wids"]:
                    seen["high_wids"].append(wid)
        # 命令路由（三视图数据来源）
        routed = msg.get("routed_commands", {}) or {}
        for c in routed.get("family", []) or []:
            wid = c.get("warning_id")
            if wid:
                seen["family_cmds"][wid] = seen["family_cmds"].get(wid, 0) + 1
                if wid not in seen["family_wids"]:
                    seen["family_wids"].append(wid)
                seen["warnings"].setdefault(wid, {})["has_family"] = True
        for c in routed.get("community", []) or []:
            wid = c.get("warning_id")
            if wid:
                seen["community_cmds"][wid] = seen["community_cmds"].get(wid, 0) + 1
                if wid not in seen["community_wids"]:
                    seen["community_wids"].append(wid)
                seen["warnings"].setdefault(wid, {})["has_community"] = True

    results = []  # (name, ok, detail)

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))

    print("\n" + "=" * 56)
    print("  银龄盾 Demo · 真实端到端验证")
    print("=" * 56)

    with TestClient(app) as client:
        # 1) 健康检查（网关 assemble 完成）
        health = client.get("/health").json()
        check("网关启动 /health", health.get("status") == "ok" and health.get("n_frames", 0) > 0,
              f"scenario={health.get('scenario')} n_frames={health.get('n_frames')} active={health.get('active_connections')}")

        # 2) WebSocket 连接 + 首连 snapshot
        with client.websocket_connect(ds.ws_path) as ws:
            # 首条消息应为 snapshot（晚连恢复历史）
            first = ws.receive_json()
            seen["snapshot_received"] = (first.get("type") == "snapshot")
            check("WS 首连 snapshot", seen["snapshot_received"],
                  f"type={first.get('type')} warnings={len(first.get('warnings', []) or [])}")

            # 3) 收集帧，直到 HIGH + family + community 都出现，或预算耗尽
            deadline = time.time() + args.budget
            while time.time() < deadline:
                try:
                    msg = ws.receive_json()
                except Exception:
                    break
                t = msg.get("type")
                if t == "frame":
                    collect_frame(msg)
                elif t == "snapshot":
                    seen["snapshot_received"] = True
                # 早停：三件套齐了
                if seen["high_seen"] and seen["family_wids"] and seen["community_wids"]:
                    break

            n_frames = seen["frames"]
            n_high = len(seen["high_wids"])
            n_family = len(seen["family_wids"])
            n_community = len(seen["community_wids"])

            # 4) 断言核心三件套
            check("① 风险发现：HIGH 风险产生", n_high > 0,
                  f"HIGH 警告数={n_high} 累计帧={n_frames}")
            check("② 家属确认：SEND_FAMILY_MESSAGE 命令", n_family > 0,
                  f"家属命令 warning 数={n_family} 命令总数={sum(seen['family_cmds'].values())}")
            check("③ 社区处置：CREATE_COMMUNITY_TASK 任务", n_community > 0,
                  f"社区任务 warning 数={n_community} 命令总数={sum(seen['community_cmds'].values())}")

            # 5) warning_id 贯通：三个视图消费同一权威 warning 流
            #    家属/社区命令引用的 warning_id 必须是真实出现过的 warning（共享事实源）
            fam_not_in_warnings = [w for w in seen["family_wids"] if w not in seen["all_wids"]]
            com_not_in_warnings = [w for w in seen["community_wids"] if w not in seen["all_wids"]]
            check("warning_id 贯通：家属命令引用真实 warning",
                  len(fam_not_in_warnings) == 0,
                  f"孤儿 warning_id={len(fam_not_in_warnings)}")
            check("warning_id 贯通：社区任务引用真实 warning",
                  len(com_not_in_warnings) == 0,
                  f"孤儿 warning_id={len(com_not_in_warnings)}")

            # 6) 上行回写闭环（发现 → 确认 → 处置）：走真实 WS 上行 + 真实 store
            high_wid = seen["high_wids"][0] if seen["high_wids"] else None

            def drain_until(want_types, budget=10.0):
                d = time.time() + budget
                while time.time() < d:
                    m = ws.receive_json()
                    if m.get("type") in want_types:
                        return m
                    if m.get("type") == "frame":
                        collect_frame(m)
                return None

            # 6a) 家属确认 HIGH 风险（→ family_handled）
            ws.send_json({"type": "action", "warning_id": high_wid, "operator": "family", "action": "confirmed"})
            ack1 = drain_until({"action_ack"})
            ack1_ok = ack1 is not None and ack1.get("type") == "action_ack"
            check("上行回写：家属确认 → action_ack", ack1_ok,
                  f"ack={ack1.get('updated') if ack1 else None}")
            # 收 state_update 广播，断言 store 状态
            su1 = drain_until({"state_update"})
            st1 = (su1 or {}).get("state", {})
            fam_status = (st1.get(high_wid) or {}).get("status") if high_wid else None
            check("上行回写：①↔② 联动（family_handled）", fam_status == "family_handled",
                  f"warning_id={high_wid} status={fam_status}")

            # 6b) 社区处置 HIGH 风险（→ community_done）
            ws.send_json({"type": "action", "warning_id": high_wid, "operator": "community", "action": "verified"})
            ack2 = drain_until({"action_ack"})
            ack2_ok = ack2 is not None and ack2.get("type") == "action_ack"
            check("上行回写：社区处置 → action_ack", ack2_ok,
                  f"ack={ack2.get('updated') if ack2 else None}")
            su2 = drain_until({"state_update"})
            st2 = (su2 or {}).get("state", {})
            com_status = (st2.get(high_wid) or {}).get("status") if high_wid else None
            check("上行回写：①↔③ 联动（community_done）", com_status == "community_done",
                  f"warning_id={high_wid} status={com_status}")

            # 6c) 独立家属确认一个 LOW 警告（② 视图独立可用）
            low_family_wid = next((w for w in seen["family_wids"] if w != high_wid), None)
            if low_family_wid:
                ws.send_json({"type": "action", "warning_id": low_family_wid, "operator": "family", "action": "confirmed"})
                ack3 = drain_until({"action_ack"})
                su3 = drain_until({"state_update"})
                st3 = (su3 or {}).get("state", {})
                low_status = (st3.get(low_family_wid) or {}).get("status")
                check("② 视图独立：LOW 警告家属确认 → family_handled",
                      (ack3 is not None) and low_status == "family_handled",
                      f"warning_id={low_family_wid} status={low_status}")
            else:
                check("② 视图独立：LOW 警告家属确认", True, "无独立 LOW 家族警告（跳过）")

    # 汇总
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 56)
    print(f"  结果：{passed}/{total} 通过" + ("  ✅ 端到端闭环达成" if passed == total else "  ❌ 存在失败项"))
    print("=" * 56)
    print(f"  累计帧={seen['frames']}  HIGH={len(seen['high_wids'])}  "
          f"家属warning={len(seen['family_wids'])}  社区warning={len(seen['community_wids'])}")
    if seen["high_wids"]:
        print(f"  HIGH warning_id 示例：{seen['high_wids'][0]}")
        hw = seen["warnings"].get(seen["high_wids"][0], {})
        print(f"  HIGH reason_summary：{hw.get('reason')}")
    print()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
