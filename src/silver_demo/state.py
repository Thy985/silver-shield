"""DemoStateStore — 进程内反馈闭环状态（ADR-0015 §2.5）。

第一版仅内存 dict，无数据库 / 无登录 / 无权限。演示重启即重置。

状态流转（与 ADR-0015 §2.5 一致）：
    pending → family_handled → community_done

严格规则：
- **不回写** ``WarningEvent`` / ``ActionCommand``（冻结对象只读消费）。
- 状态翻转只发生在本 Store 内。
- 按 ``warning_id`` 幂等映射（同一 warning 多次上行只记一条）。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional


# 合法状态（与 ADR-0015 §2.5 一致）
VALID_STATUSES = ("pending", "family_handled", "community_done")

# 合法操作者
VALID_OPERATORS = ("family", "community")

# 状态翻转规则（单向流转，不可逆）
TRANSITIONS: Dict[str, frozenset] = {
    "pending": frozenset({"family_handled"}),
    "family_handled": frozenset({"community_done"}),
    "community_done": frozenset(),  # 终态
}


class DemoStateStore:
    """进程内反馈闭环状态存储。

    线程安全：所有读写经 ``asyncio.Lock`` 保护（网关在事件循环内调用）。
    单演示连接即可，不做多用户/跨会话同步（ADR-0015 §6 明确不做）。
    """

    def __init__(self) -> None:
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, warning_id: str, status: str = "pending", operator: str = "") -> Dict[str, Any]:
        """按 warning_id 幂等插入或更新状态。

        - 首次见到的 warning_id → 初始化为 pending。
        - 已存在的 warning_id → 校验翻转合法性后更新。
        - 非法翻转 → 抛 ValueError（不静默接受，便于发现前端 bug）。

        Returns:
            更新后的状态 dict ``{"warning_id", "status", "operator"}``。
        """
        if status not in VALID_STATUSES:
            raise ValueError(f"status 必须是 {VALID_STATUSES} 之一，收到 {status!r}")

        async with self._lock:
            entry = self._state.get(warning_id)
            if entry is None:
                # 首次：尊重请求的状态（演示交互「单次点击即确认」需要）。
                # 合法非 pending 状态（family_handled / community_done）直接作为初值，
                # 否则回退 pending。后续翻转仍受 TRANSITIONS 单向约束。
                init_status = status if status in VALID_STATUSES and status != "pending" else "pending"
                entry = {"warning_id": warning_id, "status": init_status, "operator": operator}
                self._state[warning_id] = entry
                return dict(entry)

            # 已存在：校验翻转
            cur = entry["status"]
            if status == cur:
                # 幂等：相同状态重复上行，只更新 operator
                entry["operator"] = operator or entry["operator"]
                return dict(entry)
            if status not in TRANSITIONS.get(cur, frozenset()):
                raise ValueError(
                    f"warning_id={warning_id!r} 状态不能从 {cur!r} 翻转到 {status!r}；"
                    f"允许的下一状态：{sorted(TRANSITIONS.get(cur, frozenset()))}"
                )
            entry["status"] = status
            entry["operator"] = operator or entry["operator"]
            return dict(entry)

    async def get(self, warning_id: str) -> Optional[Dict[str, Any]]:
        """读取单条状态；不存在返回 None。"""
        async with self._lock:
            entry = self._state.get(warning_id)
            return dict(entry) if entry else None

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回全量状态快照（供 Dashboard 行动闭环区展示）。"""
        async with self._lock:
            return {wid: dict(e) for wid, e in self._state.items()}

    async def clear(self) -> None:
        """清空所有状态（演示重启场景用；正常退出无需调用，进程即重置）。"""
        async with self._lock:
            self._state.clear()


# ======================================================================
# P0-11.3.5 · 服务端权威聚合状态（DemoAggregateState）
# ======================================================================

# 行为里程碑样式（与 dashboard BEHAV 保持一致；服务端派生，避免双份逻辑）
_BEHAV: Dict[str, Dict[str, str]] = {
    "visit_normal": {"icon": "🌙", "label": "异常时段到访", "color": "#0891b2"},
    "visit_pending_verify": {"icon": "🔍", "label": "待核实到访", "color": "#0ea5e9"},
    "abnormal_dwell": {"icon": "⏱", "label": "停留超过阈值", "color": "#d97706"},
    "repeat_visit": {"icon": "🔁", "label": "再次出现", "color": "#7c3aed"},
    "high_risk_approach": {"icon": "⚠", "label": "高风险逼近", "color": "#dc2626"},
}
_RISK_RANK: Dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
_WARNING_MAX = 30
_BEHAVIOR_MAX = 120


class DemoAggregateState:
    """Demo 聚合状态（P0-11.3.5 · 服务端权威状态 / 单一事实来源）。

    把原本只在 Dashboard 客户端累积的 ``warningMap`` / ``behaviorEvents`` / ``commandMap``
    上移到服务端，作为**单一事实来源**。客户端退化为「快照渲染器 + 增量消费者」：
    晚连的浏览器通过 WS 首连 ``snapshot`` 恢复历史，重置 / 切换输入源时服务端清空聚合。

    同时是未来产品（家属端 App / 社区 Web / 手机推送）的第一个服务端状态源雏形。

    线程模型：网关在 asyncio 事件循环内单线程驱动 ``ingest``，无需加锁；
    ``clear`` / ``snapshot`` / ``meta`` 同样在事件循环内调用。
    """

    def __init__(self) -> None:
        # 累积状态（镜像客户端既有去重规则）
        self.warnings: Dict[str, Dict[str, Any]] = {}
        self.behaviors: List[Dict[str, Any]] = []
        self._behavior_seen: Dict[str, bool] = {}
        # warning_id -> {"family": {cid: cmd}, "community": {...}, "log_only": {...}}
        self.commands: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {}
        self._visitor_seq: Dict[str, str] = {}
        self._visitor_first: Dict[str, bool] = {}
        self._visitor_n = 0
        self._behavior_n = 0

        # 运行时元数据
        self.session_status: str = "RUNNING"
        self.frame_index: int = 0
        self.loop_count: int = 0
        self.started_at: float = 0.0
        self.last_warning: Optional[Dict[str, Any]] = None
        self.scenario: str = ""
        self.source: str = ""
        self.source_type: str = ""
        self.n_frames: int = 0

    # ------------------------------------------------------------------
    # 累积（每帧由网关调用，镜像 dashboard 既有去重规则）
    # ------------------------------------------------------------------
    def ingest(
        self,
        active_warnings: List[Dict[str, Any]],
        perception_events: List[Dict[str, Any]],
        all_warnings: List[Dict[str, Any]],
        routed: Dict[str, List[Dict[str, Any]]],
        frame_index: int,
        loop_count: int,
    ) -> None:
        """消费一帧的派生数据，更新聚合状态。

        Args:
            active_warnings: ``collect_active_warnings(view["warnings"])``（已过滤终态）。
            perception_events: ``view["perception_events"]``（本帧稀疏感知事件）。
            all_warnings: ``view["warnings"]``（本帧全部 warning，用于 snapshot 兜底）。
            routed: ``route_commands(view["commands"])``（按三端路由的命令）。
            frame_index / loop_count: 当前帧序号 / 循环计数（来自网关）。
        """
        self.frame_index = frame_index
        self.loop_count = loop_count
        self._ingest_warnings(active_warnings)
        self._merge_commands(routed)
        self._ingest_behavior(perception_events, active_warnings)
        self._recompute_last_warning()

    def _ingest_warnings(self, active_warnings: List[Dict[str, Any]]) -> None:
        for w in active_warnings:
            if not isinstance(w, dict) or not w.get("warning_id"):
                continue
            self.warnings[w["warning_id"]] = w
        # 终态移除（与客户端 ingestWarnings 一致）
        for wid in list(self.warnings.keys()):
            if self.warnings[wid].get("status") in ("RESOLVED", "REJECTED"):
                del self.warnings[wid]
        self._prune_warnings()

    def _prune_warnings(self) -> None:
        if len(self.warnings) <= _WARNING_MAX:
            return
        ids = sorted(
            self.warnings.keys(),
            key=lambda k: str(self.warnings[k].get("created_at") or ""),
        )
        for wid in ids[: len(self.warnings) - _WARNING_MAX]:
            del self.warnings[wid]

    def _merge_commands(self, routed: Dict[str, List[Dict[str, Any]]]) -> None:
        for ctype in ("family", "community", "log_only"):
            arr = (routed or {}).get(ctype) or []
            for c in arr:
                if not isinstance(c, dict):
                    continue
                wid = c.get("warning_id")
                if not wid:
                    continue
                self.commands.setdefault(
                    wid, {"family": {}, "community": {}, "log_only": {}}
                )
                bucket = self.commands[wid][ctype]
                cid = c.get("command_id")
                if cid is None:
                    cid = "__null__"
                if cid not in bucket:
                    bucket[cid] = c
                    if len(bucket) > 24:  # 与客户端一致的上限
                        old = next(iter(bucket))
                        del bucket[old]

    def _friendly_visitor(self, vid: str) -> str:
        if not vid:
            return "访客"
        if vid not in self._visitor_seq:
            self._visitor_n += 1
            self._visitor_seq[vid] = "访客#" + str(self._visitor_n)
        return self._visitor_seq[vid]

    def _add_behavior(self, ev: Dict[str, Any]) -> None:
        key = ev.get("key")
        if not key or key in self._behavior_seen:
            return
        self._behavior_seen[key] = True
        self._behavior_n += 1
        ev = dict(ev)
        ev["seq"] = self._behavior_n
        self.behaviors.insert(0, ev)  # 最新在上
        if len(self.behaviors) > _BEHAVIOR_MAX:
            dropped = self.behaviors.pop()
            if dropped.get("key") in self._behavior_seen:
                del self._behavior_seen[dropped["key"]]

    def _ingest_behavior(
        self,
        perception_events: List[Dict[str, Any]],
        active_warnings: List[Dict[str, Any]],
    ) -> None:
        for pe in perception_events or []:
            if not isinstance(pe, dict):
                continue
            vid = pe.get("visitor_id") or ""
            who = self._friendly_visitor(vid)
            if vid and vid not in self._visitor_first:
                self._visitor_first[vid] = True
                self._add_behavior({
                    "key": "enter|" + vid,
                    "time": pe.get("created_at"),
                    "icon": "👤", "label": "首次出现", "color": "#0891b2",
                    "who": who, "detail": "进入" + (pe.get("location") or "门口") + "画面",
                })
            bm = _BEHAV.get(
                pe.get("event_type"),
                {"icon": "•", "label": pe.get("event_type") or "事件", "color": "#64748b"},
            )
            repeat = pe.get("repeat_count")
            self._add_behavior({
                "key": "pe|" + vid + "|" + str(pe.get("event_type")) + "|" + (str(repeat) if repeat is not None else "0"),
                "time": pe.get("created_at"),
                "icon": bm["icon"], "label": bm["label"], "color": bm["color"],
                "who": who,
                "score": pe["score"] if isinstance(pe.get("score"), (int, float)) else None,
                "repeat": repeat,
                "detail": ("位置 " + pe["location"]) if pe.get("location") else "",
            })
        for w in active_warnings or []:
            if not isinstance(w, dict) or not w.get("warning_id"):
                continue
            wk = "warn|" + w["warning_id"]
            if wk in self._behavior_seen:
                continue
            rl = w.get("risk_level")
            color = "#dc2626" if rl == "HIGH" else ("#d97706" if rl == "MEDIUM" else "#16a34a")
            label = "生成风险预警（" + ("高" if rl == "HIGH" else ("中" if rl == "MEDIUM" else "低")) + "）"
            self._add_behavior({
                "key": wk, "time": w.get("created_at"),
                "icon": "⚠", "label": label, "color": color,
                "who": "", "detail": "、".join(w.get("reason_summary") or []),
            })

    def _recompute_last_warning(self) -> None:
        if not self.warnings:
            self.last_warning = None
            return
        best = None
        best_rank = -1
        for w in self.warnings.values():
            rank = _RISK_RANK.get(w.get("risk_level"), 0)
            if rank > best_rank:
                best_rank = rank
                best = w
        self.last_warning = best

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def clear(self, reset_session: bool = False) -> None:
        """清空聚合状态（重置 / 切换输入源时调用）。

        ``reset_session=True`` 同时重置会话计时（``started_at``），表达「新会话」语义。
        """
        self.warnings = {}
        self.behaviors = []
        self._behavior_seen = {}
        self.commands = {}
        self._visitor_seq = {}
        self._visitor_first = {}
        self._visitor_n = 0
        self._behavior_n = 0
        self.frame_index = 0
        self.loop_count = 0
        self.last_warning = None
        if reset_session:
            self.started_at = time.time()

    def snapshot(self) -> Dict[str, Any]:
        """返回供 WS 首连 ``snapshot`` 的完整聚合状态。

        含行为去重键与访客映射，便于客户端**精确恢复**其累积状态（而非重新累积）。
        """
        commands_out: Dict[str, Any] = {}
        for wid, groups in self.commands.items():
            commands_out[wid] = {
                "family": list(groups["family"].values()),
                "community": list(groups["community"].values()),
                "log_only": list(groups["log_only"].values()),
            }
        return {
            "warnings": list(self.warnings.values()),
            "behaviors": list(self.behaviors),
            "commands": commands_out,
            "visitor_seq": dict(self._visitor_seq),
            "behavior_seen": list(self._behavior_seen.keys()),
            "visitor_first": list(self._visitor_first.keys()),
        }

    def meta(self) -> Dict[str, Any]:
        """轻量运行时元数据（每帧广播 + snapshot 共用，供状态面板 / 晚连恢复）。"""
        return {
            "session_status": self.session_status,
            "loop_count": self.loop_count,
            "frame_index": self.frame_index,
            "last_warning": self.last_warning,
            "started_at": self.started_at,
            "scenario": self.scenario,
            "source": self.source,
            "source_type": self.source_type,
            "n_frames": self.n_frames,
        }
