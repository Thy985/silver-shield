"""P0-11.5a 测量脚本：驱动 CCTV 夜间场景端到端跑 N 个循环，报告 HIGH 闭环可复现性。

目标（用户拍板）：验证「CCTV 夜间场景确定性产出 HIGH + family + community 命令」。
本脚本直接驱动冻结流水线（detector→tracker→...→rule→decision→action），逐帧记录：
- 每帧 perception_event 的 event_type / rule / visits_in_window / hour / dwell / visitor_id
- 每帧 warning 的 risk_level / recommended_action / reason_summary / trigger_event_types
- 每帧 command 的 command_type（SEND_FAMILY_MESSAGE / CREATE_COMMUNITY_TASK / LOG_ONLY）

聚合（逐 loop）：
- HIGH 是否触发、首个 HIGH 的全局帧序号
- 同 visitor_id 的 max visits_in_window（决定 RepeatVisitRule 是否可能命中）
- 出现的 event_type 集合（LongDurationRule/OddHourRule/RepeatVisitRule/HighRiskApproachRule）
- routed command 计数（family / community / log_only）

用法（系统 Py3.14，torch CUDA）：
    python scripts/measure_cctv_high.py [--loops 3] [--scenario config/demo/scenarios/cctv_surveillance_suspicious.yaml] [--max-frames 0]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
os.chdir(REPO_ROOT)

from home_perception.core.config import Settings  # noqa: E402
from home_perception.runtime.pipeline import DemoClock, PerceptionPipeline  # noqa: E402

from silver_demo.scenarios import load_scenario  # noqa: E402
from silver_demo.sources import Source  # noqa: E402


def resolve_device(hp: Settings) -> str:
    try:
        import torch

        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            return "cuda:0"
    except Exception:
        pass
    return getattr(getattr(hp, "detection", None), "device", "cpu") or "cpu"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="config/demo/scenarios/cctv_surveillance_suspicious.yaml")
    ap.add_argument("--loops", type=int, default=3)
    ap.add_argument("--max-frames", type=int, default=0, help="每 loop 最大帧数(0=不限)")
    ap.add_argument("--config", default="config/default.yaml")
    ap.add_argument("--repeat-visit-count", type=int, default=None,
                    help="覆盖 repeat_visit_count 阈值（仅本测量，不改配置）")
    args = ap.parse_args()

    scenario = load_scenario(args.scenario)
    hp = Settings.load(args.config)
    # 复刻网关行为：应用场景级 rule_overrides（除非命令行显式覆盖）
    if args.repeat_visit_count is not None:
        hp.rule.repeat_visit_count = args.repeat_visit_count
    elif getattr(scenario, "rule_overrides", None):
        for k, v in scenario.rule_overrides.items():
            if hasattr(hp.rule, k):
                setattr(hp.rule, k, v)
        print(f"[rule_overrides] 应用场景覆盖: {scenario.rule_overrides}", flush=True)
    device = resolve_device(hp)
    if hp.detection.device != device:
        try:
            hp.detection.device = device
        except Exception:
            hp = hp.model_copy(deep=True)
            hp.detection.device = device
    print(f"[device] {device}  (repeat_visit_count={hp.rule.repeat_visit_count}, "
          f"long_duration_seconds={hp.rule.long_duration_seconds}, "
          f"frequency_window_s={hp.rule.frequency_window_s})", flush=True)

    print(f"[scenario] {scenario.scenario_id} start={scenario.start_time.isoformat()} "
          f"interval={scenario.frame_interval_s} fps_target={scenario.fps_target}", flush=True)

    source = Source()
    source.load(scenario, hp)
    n_frames = source.frame_count
    print(f"[source] frames(per loop)≈{n_frames} media={scenario.media_path}", flush=True)

    det = None
    global_idx = 0
    loops_report = []

    t0 = time.time()
    for loop_i in range(args.loops):
        clock = DemoClock(start=scenario.start_time, interval_s=scenario.frame_interval_s)
        pipeline = PerceptionPipeline.from_settings(
            hp,
            detector=det,
            device_id=scenario.source,
            now_provider=clock,
            frame_interval_s=scenario.frame_interval_s,
        )
        if det is None:
            pipeline.load_detector()
            det = pipeline.detector

        rec = {
            "loop": loop_i,
            "frames": 0,
            "n_warnings_by_level": {},
            "n_commands_by_type": {},
            "first_high_frame": None,
            "high_count": 0,
            "max_visits_in_window": 0,
            "max_visits_visitor": None,
            "event_types": set(),
            "rule_names": set(),
            "first_high_detail": None,
            "raw_max_visits_in_window": 0,
            "raw_max_visits_visitor": None,
        }
        # 捕获 RAW visits_in_window（绕过 cooldown 压制，得真实峰值）
        fe = pipeline.feature_extractor
        orig_extract = fe.extract

        def _wrapped(ev):
            rf = orig_extract(ev)
            if rf.frequency is not None:
                v = rf.frequency.visits_in_window
                if v > rec["raw_max_visits_in_window"]:
                    rec["raw_max_visits_in_window"] = v
                    rec["raw_max_visits_visitor"] = str(rf.visitor_id)
            return rf

        fe.extract = _wrapped
        per_loop_t0 = time.time()
        for local_i, (_, frame) in enumerate(iter(source)):
            clock.tick(scenario.frame_interval_s)
            res = pipeline.process_frame(frame, frame_index=global_idx)
            rec["frames"] += 1

            for p in res.perception_events:
                rec["event_types"].add(p.event_type)
                rec["rule_names"].add(p.meta.get("rule", "?"))
                vw = p.meta.get("visits_in_window", 0)
                if isinstance(vw, int) and vw > rec["max_visits_in_window"]:
                    rec["max_visits_in_window"] = vw
                    rec["max_visits_visitor"] = str(p.visitor_id)

            for w in res.warnings:
                lvl = w.risk_level
                rec["n_warnings_by_level"][lvl] = rec["n_warnings_by_level"].get(lvl, 0) + 1
                if lvl == "HIGH" and rec["first_high_frame"] is None:
                    rec["first_high_frame"] = global_idx
                    rec["first_high_detail"] = {
                        "risk_level": w.risk_level,
                        "recommended_action": w.recommended_action,
                        "reason_summary": w.reason_summary,
                        "trigger_types": sorted({t.get("event_type") for t in w.trigger_events}),
                    }
                if lvl == "HIGH":
                    rec["high_count"] += 1

            for c in res.commands:
                ct = c.command_type
                rec["n_commands_by_type"][ct] = rec["n_commands_by_type"].get(ct, 0) + 1

            global_idx += 1
            if args.max_frames and rec["frames"] >= args.max_frames:
                break
            if rec["frames"] % 200 == 0:
                print(f"  loop {loop_i} frame {rec['frames']} "
                      f"(global {global_idx})...", flush=True)

        rec["event_types"] = sorted(rec["event_types"])
        rec["rule_names"] = sorted(rec["rule_names"])
        loops_report.append(rec)
        per_loop_dt = time.time() - per_loop_t0
        print(f"[loop {loop_i}] frames={rec['frames']} dt={per_loop_dt:.1f}s "
              f"warnings={rec['n_warnings_by_level']} high={rec['high_count']} "
              f"first_high={rec['first_high_frame']} max_visits={rec['max_visits_in_window']} "
              f"cmds={rec['n_commands_by_type']}", flush=True)

    total_dt = time.time() - t0

    # === 汇总报告 ===
    print("\n================ P0-11.5a 测量汇总 ================")
    print(f"device={device} loops={args.loops} total_dt={total_dt:.1f}s "
          f"repeat_visit_count={hp.rule.repeat_visit_count}")
    all_high = [r for r in loops_report if r["high_count"] > 0]
    print(f"触发 HIGH 的 loop 数: {len(all_high)}/{len(loops_report)}")
    for r in loops_report:
        print(f"\n--- loop {r['loop']} ---")
        print(f"  frames={r['frames']}")
        print(f"  warnings_by_level={r['n_warnings_by_level']}")
        print(f"  high_count={r['high_count']} first_high_frame={r['first_high_frame']}")
        print(f"  max_visits_in_window={r['max_visits_in_window']} (visitor={r['max_visits_visitor']})")
        print(f"  raw_max_visits_in_window={r['raw_max_visits_in_window']} (visitor={r['raw_max_visits_visitor']})")
        print(f"  event_types={r['event_types']}")
        print(f"  rule_names={r['rule_names']}")
        print(f"  commands_by_type={r['n_commands_by_type']}")
        if r["first_high_detail"]:
            d = r["first_high_detail"]
            print(f"  HIGH detail: action={d['recommended_action']} "
                  f"triggers={d['trigger_types']} reasons={d['reason_summary']}")

    # 结论判定
    fam = sum(r["n_commands_by_type"].get("SEND_FAMILY_MESSAGE", 0) for r in loops_report)
    comm = sum(r["n_commands_by_type"].get("CREATE_COMMUNITY_TASK", 0) for r in loops_report)
    print("\n--- 结论 ---")
    if all_high and fam > 0 and comm > 0:
        print("✅ 稳定 HIGH 闭环达成：每 loop 均触发 HIGH，且 family + community 命令均生成。")
    elif all_high:
        print(f"⚠️ HIGH 触发但命令不全：family={fam} community={comm}（需检查 action 路由）。")
    else:
        print(f"❌ 未触发 HIGH。raw visits_in_window 峰值={max((r['raw_max_visits_in_window'] for r in loops_report), default=0)}"
              f"，当前 repeat_visit_count={hp.rule.repeat_visit_count}。")
        print("   建议：若 max_visits_in_window>=2，将 repeat_visit_count 降到该峰值即可；")
        print("   若峰值=1（重入换了 track_id），需改 visitor_id 稳定性或放宽 HighRiskApproachRule。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
