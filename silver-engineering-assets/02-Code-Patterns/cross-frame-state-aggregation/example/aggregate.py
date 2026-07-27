"""Cross-Frame State Aggregation · 可复用骨架（示例）。

来源：Silver Shield `state.py` 的 DemoAggregateState 提炼。
**不是银龄盾代码**，是抽取的模式骨架。

核心思想：
- 跨帧累积状态上移到服务端，作为单一事实来源（Single Source of Truth）。
- 按 warning_id upsert 保活（不闪现）；终态移除；超上限 prune。
- 行为里程碑按去重键跨帧累积（enter|vid / pe|vid|type / warn|wid）。
- snapshot() 供晚连恢复；clear() 供 reset / 切换源清空（不串场）。
"""

from __future__ import annotations

from typing import Any, Dict, List


class DemoAggregateState:
    """演示/实时系统的服务端权威聚合状态。"""

    def __init__(self) -> None:
        self.warnings: Dict[str, Dict[str, Any]] = {}
        self.behaviors: List[Dict[str, Any]] = []
        self._behavior_seen: Dict[str, bool] = {}
        self.commands: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
        self.frame_index = 0
        self.loop_count = 0
        # 运行时元数据
        self.session_status = "RUNNING"
        self.scenario = ""
        self.source = ""

    # ------------------------------------------------------------------
    # 每帧累积
    # ------------------------------------------------------------------
    def ingest(
        self,
        active_warnings: List[Dict[str, Any]],
        routed: Dict[str, List[Dict[str, Any]]],
        perception_events: List[Dict[str, Any]],
        frame_index: int,
        loop_count: int,
    ) -> None:
        self.frame_index = frame_index
        self.loop_count = loop_count
        self._ingest_warnings(active_warnings)
        self._merge_commands(routed)
        self._ingest_behavior(perception_events, active_warnings)

    def _ingest_warnings(self, active_warnings: List[Dict[str, Any]]) -> None:
        for w in active_warnings:
            if not isinstance(w, dict) or not w.get("warning_id"):
                continue
            self.warnings[w["warning_id"]] = w          # upsert 保活
        # 终态移除
        for wid in list(self.warnings.keys()):
            if self.warnings[wid].get("status") in ("RESOLVED", "REJECTED"):
                del self.warnings[wid]
        self._prune_warnings()

    def _prune_warnings(self, max_n: int = 30) -> None:
        if len(self.warnings) <= max_n:
            return
        ids = sorted(self.warnings, key=lambda k: str(self.warnings[k].get("created_at") or ""))
        for wid in ids[: len(self.warnings) - max_n]:
            del self.warnings[wid]

    def _merge_commands(self, routed: Dict[str, List[Dict[str, Any]]]) -> None:
        for ctype in ("family", "community", "log_only"):
            for c in (routed or {}).get(ctype) or []:
                wid = c.get("warning_id")
                if not wid:
                    continue
                self.commands.setdefault(wid, {"family": {}, "community": {}, "log_only": {}})
                self.commands[wid][ctype][c.get("command_id", "__null__")] = c

    def _add_behavior(self, ev: Dict[str, Any]) -> None:
        key = ev.get("key")
        if not key or key in self._behavior_seen:
            return                                     # 跨帧去重
        self._behavior_seen[key] = True
        self.behaviors.insert(0, ev)                  # 最新在上
        if len(self.behaviors) > 120:
            dropped = self.behaviors.pop()
            if dropped.get("key") in self._behavior_seen:
                del self._behavior_seen[dropped["key"]]

    def _ingest_behavior(
        self, perception_events: List[Dict[str, Any]], active_warnings: List[Dict[str, Any]]
    ) -> None:
        for pe in perception_events or []:
            vid = pe.get("visitor_id") or ""
            self._add_behavior({
                "key": "pe|" + vid + "|" + str(pe.get("event_type")) + "|" + str(pe.get("repeat_count", 0)),
                "icon": "•", "label": pe.get("event_type"), "who": vid,
            })
        for w in active_warnings or []:
            wk = "warn|" + w.get("warning_id", "")
            if wk in self._behavior_seen:
                continue
            self._add_behavior({
                "key": wk, "icon": "⚠",
                "label": "生成风险预警（" + w.get("risk_level") + "）",
            })

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def clear(self, reset_session: bool = False) -> None:
        """清空聚合（reset / 切换源）。reset_session=True 表达「新会话」。"""
        self.warnings = {}
        self.behaviors = []
        self._behavior_seen = {}
        self.commands = {}
        self.frame_index = 0
        self.loop_count = 0

    def snapshot(self) -> Dict[str, Any]:
        """供 WS 首连 snapshot：完整聚合状态，便于客户端精确恢复。"""
        commands_out: Dict[str, Any] = {}
        for wid, groups in self.commands.items():
            commands_out[wid] = {t: list(groups[t].values()) for t in ("family", "community", "log_only")}
        return {
            "warnings": list(self.warnings.values()),
            "behaviors": list(self.behaviors),
            "commands": commands_out,
            "behavior_seen": list(self._behavior_seen.keys()),
        }
