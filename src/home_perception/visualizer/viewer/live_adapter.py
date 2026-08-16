"""ADR-0036 Slice C · Live Adapter（VM-13 Phase B · 视觉 Live + 音频增量合并）。

把实时帧流（``FrameResult`` 契约输出）增量投影为 ``EvidenceProjection``，复用同一个
Case Viewer（仅 ``provenance_kind=REAL_SENSOR`` 着色差异）。

铁律（对齐 ADR-0036 + VM 系列）：
- **VM-3 / AC-5（依赖方向）**：本模块**绝不** import ``silver_demo`` / 生产 runtime
  （``runtime.pipeline.FrameResult``）；live 帧以**结构化 TypedDict + 鸭子类型映射**
  摄入，对 runtime 只认输出契约，不认内部状态。
- **VM-1（唯一 View Model）**：Live 与 Artifact 共享同一 ``EvidenceProjection`` schema，
  仅 ``provenance_kind`` 不同（AC-3）；本模块只产 ``EvidenceProjection``，不持第二份事实。
- **VM-7 / AC-8（禁伪造）**：Live 缺失的 ``gate`` / ``fingerprints`` / ``audio_evidence``
  必须显式表达——``gate=()`` / ``fingerprints=None`` / ``episodes=0`` /
  ``cross_modal_links=0`` / ``trace_outcome_kinds=()`` / ``suppress_reasons=()`` /
  ``episode_action_command_types=()``；断言不存在 ``gate=PASS`` 或伪造指纹/音频证据。
- **VM-8 / AC-4b（幂等）**：``ProjectionAccumulator`` 是**纯函数式累积**——同一有序帧流
  重放 N（≥2）次，最终 ``EvidenceProjection`` 逐字段一致（dedup 集合排序固定、滚动窗口
  裁剪确定性、无墙钟/随机数）。
- **AC-7（Provenance 一等视觉）**：所有 live 节点 ``provenance_kind=REAL_SENSOR``，
  时间轴会话锚点保证 provenance 非空，前端 banner 自然呈现「真实传感器·实时数据」。
- AST 契约（D3）：本模块属 visualizer **非 video** 部分，**仅** import stdlib（`typing`）
  + 同包（schema / case_presentation）；**不**引入 ``urllib`` / ``re`` 等未放行依赖。

WS 传输（ADR-0036 Slice B 描述）由宿主层负责把 ``FrameResult`` 喂给 ``ingest()``；
本模块不绑定传输协议，保持 torch-free、CI 友好。
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from home_perception.visualizer.schema.evidence import (
    AudioEvidenceNode,
    CaseTimeTrack,
    Counts,
    DecisionEvidence,
    EvidenceProjection,
    ProjectionMeta,
    ScenarioEvidence,
    TimelineNode,
)
from home_perception.visualizer.schema.graph import (
    EvidenceGraph,
    EvidenceGraphEdge,
    EvidenceGraphNode,
)
from home_perception.visualizer.viewer.case_presentation import (
    CasePresentationDescriptor,
    FirstScreenLayout,
    MediaBinding,
    TimeMapping,
)

# Live 模式固定展示面板顺序（与 Artifact 默认一致，VM-1 纯展示元数据）。
# P0-1：加 action_closure（人类处置闭环面板，Live 专属交互；Artifact 模式不渲染）。
_LIVE_PANELS: tuple[str, ...] = (
    "case_video",
    "current_risk",
    "why",
    "action",
    "action_closure",
    "evidence_timeline",
    # Gate 3 产品拍板（2026-08-16）：Live 首屏须展示"此刻真实传感器检测到什么"，
    # 与 Artifact 首屏音频面板语义对齐；首屏为实时摘要，完整技术细节在 details 区。
    "audio_perception",
)

# 确定性 ref 前缀（Live 源，区别于 artifact 的 ``<scenario>.canonical.json#...``）。
_LIVE_REF_PREFIX = "live"

# Live Case Time 音频 Lane 标签（仅本地最小映射，不 import renderer 生产展示表；
# 与 loader 的 audio kind → 中文标签语义对齐，缺失回落原始枚举，绝不编造）。
_AUDIO_KIND_ZH: dict[str, str] = {
    "audio_voice_raised": "音高升高",
    "audio_speech_rapid": "语速加快",
    "audio_distress_cry": "哭腔/求助",
    "audio_telephone_persistent": "持续电话声音",
    "audio_anomaly_other": "其他声学异常",
}


class LiveIngestError(ValueError):
    """Live 帧摄入契约违规（fail-closed：字段缺失/类型非法 → 拒绝累积，不产残缺投影）。"""


class LiveFrame(TypedDict):
    """单帧规范化结构（FrameResult 契约的视觉子集，鸭子类型摄入后落定）。

    不含生产对象引用、不含媒体字节（VM-10/AC-11）；只持投影所需的原始字符串分类。
    """

    frame_index: int
    n_detections: int
    n_visitor_events: int
    event_types: tuple[str, ...]      # 来自 perception_events[].event_type
    risk_levels: tuple[str, ...]       # 来自 warnings[].risk_level
    recommended_actions: tuple[str, ...]  # 来自 warnings[].recommended_action
    command_types: tuple[str, ...]     # 来自 commands[].command_type


_MISSING = object()

# VM-9 / AC-10 守卫：Live 音频摄入**绝不**携带语义判定 / ASR 文本 / 媒体字节字段。
# 镜像 home_perception.audio 的 FORBIDDEN_AUDIO_FIELDS（不得 import 生产包，VM-3）——只列
# 禁区名，摄入时若命中即 fail-closed 拒绝，防判定/文本/字节泄漏进 View Model。
_LIVE_AUDIO_FORBIDDEN_FIELDS = frozenset(
    {
        "text",
        "transcript",
        "fraud_result",
        "fraud_probability",
        "is_fraud",
        "is_scammer",
        "is_criminal",
        "verdict",
        "final_decision",
        "crime_probability",
        "guilt_score",
        "deception_score",
        "raw_audio",
        "mp4",
        "wav",
    }
)


class LiveAudioFrame(TypedDict):
    """单条音频感知规范化结构（AudioPerceptionEvent 契约的视觉子集，鸭子类型摄入）。

    不含生产对象引用、不含媒体字节（VM-10/AC-11）、不含 ASR 文本/语义判定
    （VM-9/AC-10）。Case Viewer 执行期间无 ASR/LLM，音频只产 perception。
    """

    timestamp: str                     # ← AudioPerceptionEvent.timestamp（Unix 秒）
    kind: str                          # ← AudioPerceptionKind.value（五值）
    score: float                       # ← .score (0~1)，规则强度
    confidence: float                  # ← .confidence (0~1)，检测可信度
    source_segment_ids: tuple[str, ...]  # ← .source_segment_ids
    labels: tuple[str, ...]            # ← .labels / .scored_labels
    event_id: NotRequired[str]         # ← AudioPerceptionEvent.event_id（透传，可选，溯源/幂等）


def _require_float(obj: Any, key: str, *, lo: float = 0.0, hi: float = 1.0) -> float:
    """取必填 float（fail-closed：缺/非数/越界 [lo,hi] / 布尔 → LiveIngestError）。"""
    value = _pick(obj, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LiveIngestError(f"live 音频字段 {key!r} 必须是数值，收到 {value!r}（fail-closed）")
    fv = float(value)
    if not (lo <= fv <= hi):
        raise LiveIngestError(f"live 音频字段 {key!r} 超出 [{lo},{hi}]，收到 {fv!r}（fail-closed）")
    return fv


def _iter_of_str(obj: Any, key: str) -> list[str]:
    """取可选 str 列表（缺省空列表；非序列/非 str 元素 → fail-closed）。"""
    value = _pick(obj, key, default=[])
    if not isinstance(value, (list, tuple)):
        raise LiveIngestError(f"live 音频字段 {key!r} 必须是 list/tuple，收到 {type(value).__name__}（fail-closed）")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise LiveIngestError(f"live 音频字段 {key!r} 元素必须是 str，收到 {item!r}（fail-closed）")
        out.append(item)
    return out


def _assert_no_forbidden_audio_fields(data: dict) -> None:
    """拒绝携带语义判定 / ASR 文本 / 媒体字节字段（VM-9 / AC-10 / AC-11）。"""
    offenders = sorted(k for k in data if k in _LIVE_AUDIO_FORBIDDEN_FIELDS)
    if offenders:
        raise LiveIngestError(f"live 音频摄入含禁区字段 {offenders}（VM-9/AC-10/AC-11 fail-closed）")


def audio_result_to_live_audio(audio_result: Any) -> LiveAudioFrame:
    """``AudioPerceptionEvent`` 契约 → ``LiveAudioFrame``（鸭子类型映射，不 import 生产类型，VM-3）。

    AC-10 / VM-9：音频只产 perception，绝不携带 ``text`` / ``transcript`` /
    ``FORBIDDEN_AUDIO_FIELDS`` / 媒体字节字段——命中即 fail-closed 拒绝。

    Args:
        audio_result: 任何带 ``timestamp`` / ``kind`` / ``score`` / ``confidence`` /
            ``source_segment_ids`` / ``labels`` 契约字段的对象（dict 或 dataclass 均可；
            推荐使用 ``AudioPerceptionEvent.to_dict()`` 的 dict 形态）。

    Raises:
        LiveIngestError: 任一必填字段缺失/类型非法/越界，或命中禁区字段（fail-closed）。
    """
    if isinstance(audio_result, dict):
        _assert_no_forbidden_audio_fields(audio_result)
    kind = _require_str(audio_result, "kind")
    timestamp = _coerce_timestamp(audio_result)
    score = _require_float(audio_result, "score")
    confidence = _require_float(audio_result, "confidence")
    source_segment_ids = tuple(_iter_of_str(audio_result, "source_segment_ids"))
    labels = tuple(_iter_of_str(audio_result, "labels"))
    # 可选 event_id（透传上游 AudioPerceptionEvent.event_id，仅溯源/幂等核对；非必填，
    # 缺省不投影）。鸭子类型取值，dict/对象均可；非 str 即拒绝（fail-closed）。
    raw_event_id = _pick(audio_result, "event_id", default=None)
    event_id = None
    if raw_event_id is not None:
        if not isinstance(raw_event_id, str) or not raw_event_id:
            raise LiveIngestError(f"live 音频 event_id 必须是非空 str，收到 {raw_event_id!r}（fail-closed）")
        event_id = raw_event_id
    return LiveAudioFrame(
        timestamp=timestamp,
        kind=kind,
        score=score,
        confidence=confidence,
        source_segment_ids=source_segment_ids,
        labels=labels,
        **({"event_id": event_id} if event_id is not None else {}),
    )


def _audio_dedup_key(la: LiveAudioFrame) -> str:
    """无 event_id 时的去重键（护 VM-8 重放幂等）：kind+timestamp+segments+score+conf 组合。

    仅作回退——优先用 event_id（上游 AudioPerceptionEvent.event_id，跨重放稳定）。
    """
    segs = ",".join(la["source_segment_ids"]) if la.get("source_segment_ids") else ""
    return "|".join([
        str(la["kind"]),
        str(la["timestamp"]),
        segs,
        repr(la["score"]),
        repr(la["confidence"]),
    ])


def _coerce_timestamp(audio_result: Any) -> str:
    """取 timestamp（Unix 秒）→ 规范字符串（fail-closed：缺/类型非法）。

    ``AudioPerceptionEvent.to_dict()`` 产出数值型 Unix 秒，JSONL 人工条目可能是字符串；
    二者都接受并归一为字符串（统一时间轴同源，AC-9）。
    """
    raw = _pick(audio_result, "timestamp")
    if isinstance(raw, bool):
        raise LiveIngestError(f"live 音频 timestamp 类型非法：{raw!r}（fail-closed）")
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, str):
        return raw
    raise LiveIngestError(f"live 音频 timestamp 类型非法：{raw!r}（fail-closed）")


def _pick(obj: Any, key: str, *, default: Any = _MISSING) -> Any:
    """鸭子类型取值：先映射（dict）后属性（dataclass/对象），二者皆无按 default 处理。

    不 import 任何生产类型——对 ``FrameResult`` / ``PerceptionEvent`` / ``WarningEvent`` /
    ``ActionCommand`` 只认**输出契约字段名**，靠字段名鸭子取值。
    """
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
    else:
        try:
            return getattr(obj, key)
        except AttributeError:
            pass
    if default is _MISSING:
        raise LiveIngestError(f"live 帧缺字段 {key!r}（fail-closed）")
    return default


def _require_str(obj: Any, key: str) -> str:
    """取必填非空字符串（fail-closed：缺/非 str/空 → LiveIngestError）。"""
    value = _pick(obj, key)
    if not isinstance(value, str) or not value:
        raise LiveIngestError(f"live 帧字段 {key!r} 必须是非空 str，收到 {value!r}（fail-closed）")
    return value


def _require_int(obj: Any, key: str) -> int:
    """取必填非负 int（fail-closed）。"""
    value = _pick(obj, key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LiveIngestError(f"live 帧字段 {key!r} 必须是 ≥0 int，收到 {value!r}（fail-closed）")
    return value


def _iter_of(obj: Any, key: str) -> list[Any]:
    """取可选 list/tuple（缺省空列表；非序列 → fail-closed）。"""
    value = _pick(obj, key, default=[])
    if not isinstance(value, (list, tuple)):
        raise LiveIngestError(f"live 帧字段 {key!r} 必须是 list/tuple，收到 {type(value).__name__}（fail-closed）")
    return list(value)


def frame_result_to_live_frame(frame_result: Any) -> LiveFrame:
    """``FrameResult`` 契约 → ``LiveFrame``（鸭子类型映射，不 import 生产类型，VM-3）。

    Args:
        frame_result: 任何带 ``frame_index`` / ``n_detections`` / ``perception_events`` /
            ``warnings`` / ``commands`` 等契约字段的对象（dataclass 或 dict 均可）。

    Raises:
        LiveIngestError: 任一必填字段缺失/类型非法（fail-closed）。
    """
    frame_index = _require_int(frame_result, "frame_index")
    n_detections = _require_int(frame_result, "n_detections")
    n_visitor_events = _require_int(frame_result, "n_visitor_events")

    event_types: list[str] = []
    for pe in _iter_of(frame_result, "perception_events"):
        event_types.append(_require_str(pe, "event_type"))

    risk_levels: list[str] = []
    recommended_actions: list[str] = []
    for w in _iter_of(frame_result, "warnings"):
        risk_levels.append(_require_str(w, "risk_level"))
        recommended_actions.append(_require_str(w, "recommended_action"))

    command_types: list[str] = []
    for c in _iter_of(frame_result, "commands"):
        command_types.append(_require_str(c, "command_type"))

    return LiveFrame(
        frame_index=frame_index,
        n_detections=n_detections,
        n_visitor_events=n_visitor_events,
        event_types=tuple(event_types),
        risk_levels=tuple(risk_levels),
        recommended_actions=tuple(recommended_actions),
        command_types=tuple(command_types),
    )


class ProjectionAccumulator:
    """增量投影累积器（VM-8 幂等 / AC-4b 重放稳定）。

    累积语义（确定性，与摄入顺序无关地稳定）：
    - ``counts``：跨全量帧**累计**（不随滚动窗口裁剪丢失），复用同一有序流 → 同值；
    - 分类集合（event_types / risk_levels / recommended_actions / command_types）：去重后
      **排序**固定（确定性，不依赖首见顺序）；
    - 时间轴节点：仅保留最近 ``window_size`` 帧的逐帧细节（滚动窗口），裁剪确定性；
    - 无墙钟 / 随机数 → 同输入 N 次重放逐字段一致。
    """

    def __init__(self, scenario_id: str, *, window_size: int = 64, mode: str = "live") -> None:
        if not scenario_id:
            raise LiveIngestError("scenario_id 不能为空（fail-closed）")
        if not isinstance(window_size, int) or window_size < 1:
            raise LiveIngestError(f"window_size 必须 ≥1 int，收到 {window_size!r}（fail-closed）")
        self.scenario_id = scenario_id
        self.window_size = window_size
        self.mode = mode
        # 滚动窗口内的最近事件（frame + audio 交错误现，仅用于时间轴逐帧/逐音频细节）。
        # 每个元素：{"kind": "frame", "frame": LiveFrame} 或
        # {"kind": "audio", "audio": LiveAudioFrame, "audio_index": int}。
        # 摄入顺序即时间轴交错顺序（确定性），与摄入顺序无关地稳定（VM-8）。
        self._recent_events: list[dict] = []
        # 全量累计计数（独立于滚动窗口）。
        self._total_frames = 0
        self._total_audio = 0
        self._counts: dict[str, int] = {
            "perception_events": 0,
            "warnings": 0,
            "commands": 0,
        }
        # 去重集合（最终排序输出，确定性）。
        self._event_types: set[str] = set()
        self._risk_levels: set[str] = set()
        self._recommended_actions: set[str] = set()
        self._command_types: set[str] = set()
        # 音频：累计摄入序号（确定性交错标签）+ 去重 kind 集合（不进 Counts schema）。
        self._audio_index = 0
        self._audio_kinds: set[str] = set()
        # 音频证据持久化（不进 _recent_events 滚动窗口）：真实音频是稀疏语义证据，须跨整个
        # 实时会话可见；否则长循环跑过 window_size 帧后早期音频被滚动裁掉 → audio_evidence
        # 首屏不持久（Gate 4 真实缺陷 #2）。帧/处置细节走窗口，音频走持久列表，二者分别服务
        # "细节"与"证据"，时间轴 AUDIO 节点 ↔ audio_evidence ↔ Case Time 音频 Lane 同源一致。
        self._audio_events: list[LiveAudioFrame] = []  # 持久音频事件（dict{kind,audio,audio_index}）
        # 去重键集合（event_id 优先，缺失回退组合键），护 VM-8 重放幂等：
        # live 循环重启 frame_index 归零会重新喂入同一音频事件，去重避免重复累积污染证据。
        self._audio_dedup_keys: set[str] = set()
        # P0-1 处置闭环：Resolution 事实累计序号（确定性 ref 标签）。
        self._resolution_index = 0

    @property
    def n_frames(self) -> int:
        """累计摄入帧数（独立于滚动窗口裁剪）。"""
        return self._total_frames

    @property
    def n_audio(self) -> int:
        """累计摄入音频条数（独立于滚动窗口裁剪）。"""
        return self._total_audio

    @property
    def audio_kinds(self) -> tuple[str, ...]:
        """累计摄入的音频 kind 去重集合（确定性排序输出）。"""
        return tuple(sorted(self._audio_kinds))

    def ingest(self, frame_result: Any) -> ProjectionAccumulator:
        """摄入一帧（FrameResult 契约对象）→ 累积；返回 self 便于链式。
        
        Raises:
            LiveIngestError: 帧契约违规（fail-closed）。
        """
        live = frame_result_to_live_frame(frame_result)
        self._accumulate(live)
        return self

    def ingest_audio(self, audio_result: Any) -> ProjectionAccumulator:
        """摄入一条音频感知（AudioPerceptionEvent 契约）→ 累积；返回 self 便于链式。

        Raises:
            LiveIngestError: 音频契约违规 / 命中禁区字段（fail-closed）。
        """
        live_audio = audio_result_to_live_audio(audio_result)
        self._ingest_audio(live_audio)
        return self

    def ingest_resolution(self, fact: dict) -> ProjectionAccumulator:
        """摄入一条处置完成事实（Resolution Fact）→ 累积；返回 self。

        **P0-1 铁律（Projection 不回写 / VM-6）**：Resolution 是"事实源产生的新事实事件"，
        经本 accumulator 摄入 → ``to_evidence_projection()`` **重新构造** EvidenceProjection
        （只读派生，绝不 mutate projection）。gateway 在状态机到达终态 ``community_done`` 时
        构造该事实；鸭子类型读 dict（AST 契约 _STDLIB_TOP 不变）。

        Raises:
            LiveIngestError: 事实契约违规（fail-closed）。
        """
        if not isinstance(fact, dict):
            raise LiveIngestError("resolution fact 必须是对象（fail-closed）")
        wid = fact.get("warning_id")
        status = fact.get("status")
        if not isinstance(wid, str) or not wid:
            raise LiveIngestError("resolution.warning_id 缺失/非 str（fail-closed）")
        if status != "community_done":
            raise LiveIngestError(
                f"resolution.status 须为 community_done，收到 {status!r}（fail-closed）"
            )
        operator = fact.get("operator")
        if operator is not None and not isinstance(operator, str):
            raise LiveIngestError("resolution.operator 非 str（fail-closed）")
        action = fact.get("action")
        if action is not None and not isinstance(action, str):
            raise LiveIngestError("resolution.action 非 str（fail-closed）")
        self._resolution_index += 1
        self._recent_events.append(
            {
                "kind": "resolution",
                "resolution": {
                    "warning_id": wid,
                    "operator": operator or "community",
                    "action": action or "complete",
                    "index": self._resolution_index,
                },
            }
        )
        if len(self._recent_events) > self.window_size:
            # 确定性裁剪：与帧/音频同一滚动窗口（时间轴交错呈现，AC-9）。
            self._recent_events = self._recent_events[-self.window_size:]
        return self

    def _accumulate(self, live: LiveFrame) -> None:
        self._recent_events.append({"kind": "frame", "frame": live})
        if len(self._recent_events) > self.window_size:
            # 确定性裁剪：仅保留末尾 window_size 事件（滚动窗口逐帧/逐音频细节）。
            self._recent_events = self._recent_events[-self.window_size:]
        self._total_frames += 1
        self._counts["perception_events"] += len(live["event_types"])
        self._counts["warnings"] += len(live["risk_levels"])
        self._counts["commands"] += len(live["command_types"])
        self._event_types.update(live["event_types"])
        self._risk_levels.update(live["risk_levels"])
        self._recommended_actions.update(live["recommended_actions"])
        self._command_types.update(live["command_types"])

    def _ingest_audio(self, live_audio: LiveAudioFrame) -> None:
        self._audio_index += 1
        self._total_audio += 1
        self._audio_kinds.add(live_audio["kind"])
        # 持久化（不进滚动窗口）：真实音频是稀疏语义证据，须跨整个实时会话可见。
        # 去重保护 VM-8 重放幂等：live 循环重启 frame_index 归零会重新喂入同一音频事件，
        # 以 event_id 为主键（缺失回退组合键 _audio_dedup_key）去重，避免重复累积污染证据。
        key = live_audio.get("event_id") or _audio_dedup_key(live_audio)
        if key not in self._audio_dedup_keys:
            self._audio_dedup_keys.add(key)
            self._audio_events.append(
                {"kind": "audio", "audio": live_audio, "audio_index": self._audio_index}
            )

    # —— 投影构件（镜像 loader 形态，但 provenance=REAL_SENSOR、ref 走 live://） ——

    def _build_counts(self) -> Counts:
        # Live 无独立 sink 分离 / 无 memory episode / 无跨模态关联（Phase B 视觉+音频，
        # 音频不进 Counts schema，仅时间轴 AUDIO 节点 + audio_kinds 累积）：
        # sink_commands 与 commands 同源（实时命令即被消费）；decision_traces 以 warnings 计
        # （每警告对应一条决策）；episodes / cross_modal_links 显式 0（AC-8 禁伪造）。
        return Counts(
            perception_events=self._counts["perception_events"],
            warnings=self._counts["warnings"],
            commands=self._counts["commands"],
            sink_commands=self._counts["commands"],
            decision_traces=self._counts["warnings"],
            episodes=0,
            cross_modal_links=0,
        )

    def _build_timeline(self) -> tuple[TimelineNode, ...]:
        sid = self.scenario_id
        nodes: list[TimelineNode] = []
        # 会话锚点：真实实时会话标识（非 synthetic 数据），保证 provenance 非空（AC-7）。
        # modality=VISION：live 会话以视觉流为主锚（音频同会话合并），AC-9 只要求节点带
        # modality 判别；会话根节点以 host 模态标注，帧/音频节点各自带精确 modality。
        nodes.append(
            TimelineNode(
                timestamp="LIVE",
                stage="live",
                type="session",
                summary=f"实时会话 {sid}（REAL_SENSOR · 滚动窗口 {self.window_size} 事件）",
                verdict="INFO",
                modality="VISION",
                provenance_kind="REAL_SENSOR",
                ref=f"{_LIVE_REF_PREFIX}://session/{sid}",
            )
        )
        # 按摄入顺序呈现视觉/处置结果（AC-9：统一时间轴，不三套独立时间轴）。
        # 注：音频节点不再经 _recent_events 滚动窗口——已迁至持久列表 _audio_events，
        # 与时间轴 AUDIO 节点 ↔ audio_evidence ↔ Case Time 音频 Lane 同源一致（VM-13 / Gate 4 缺陷 #2）。
        for ev in self._recent_events:
            kind = ev["kind"]
            if kind == "frame":
                lf = ev["frame"]
                fi = lf["frame_index"]
                nw = len(lf["risk_levels"])
                nodes.append(
                    TimelineNode(
                        timestamp=f"F{fi}",
                        stage="perception",
                        type="frame",
                        summary=f"frame {fi}: {lf['n_detections']} 检测, {nw} 警告",
                        verdict="INFO",
                        modality="VISION",
                        provenance_kind="REAL_SENSOR",
                        ref=f"{_LIVE_REF_PREFIX}://frame/{fi}",
                    )
                )
            elif kind == "resolution":  # P0-1 处置闭环事实
                rs = ev["resolution"]
                nodes.append(
                    TimelineNode(
                        timestamp=f"R{rs['index']}",
                        stage="action",
                        type="resolution",
                        summary=(
                            f"处置完成：warning {rs['warning_id'][:8]} 由 "
                            f"{rs['operator']}「{rs['action']}」（community_done）"
                        ),
                        verdict="INFO",
                        modality="ACTION",
                        provenance_kind="REAL_SENSOR",
                        ref=f"{_LIVE_REF_PREFIX}://resolution/{rs['index']}",
                    )
                )
        # 音频证据节点（持久，跨会话可见）：与时间轴 AUDIO 节点 ↔ audio_evidence 同源一致。
        for ev in self._audio_events:
            la = ev["audio"]
            a_idx = ev["audio_index"]
            nodes.append(
                TimelineNode(
                    timestamp=f"A{a_idx}",
                    stage="perception",
                    type="audio",
                    summary=(
                        f"audio {a_idx}: {la['kind']} "
                        f"(score={la['score']:.2f}, conf={la['confidence']:.2f})"
                    ),
                    verdict="INFO",
                    modality="AUDIO",
                    provenance_kind="REAL_SENSOR",
                    ref=f"{_LIVE_REF_PREFIX}://audio/{a_idx}",
                )
            )
        return tuple(nodes)

    def _build_decision_evidence(
        self,
        event_types: tuple[str, ...],
        risk_levels: tuple[str, ...],
        recommended_actions: tuple[str, ...],
        command_types: tuple[str, ...],
    ) -> tuple[DecisionEvidence, ...]:
        sid = self.scenario_id
        ev: list[DecisionEvidence] = []

        def _add(kind: str, label: str, values: tuple[str, ...], ref: str) -> None:
            value = ", ".join(values) if values else "(无)"
            ev.append(
                DecisionEvidence(kind=kind, label=label, value=value, ref=ref)
            )

        # 分组语义对齐 loader：Observation Evidence（检测证据）→ Reasoning（风险级别）→
        # Outcome（推荐动作 + 已执行命令）。
        _add("evidence", "Observation · 检测证据（事件类型）", event_types,
             f"{_LIVE_REF_PREFIX}://{sid}/event_types")
        _add("reasoning", "Reasoning · 风险级别", risk_levels,
             f"{_LIVE_REF_PREFIX}://{sid}/risk_levels")
        _add("outcome", "Outcome · 推荐动作", recommended_actions,
             f"{_LIVE_REF_PREFIX}://{sid}/recommended_actions")
        _add("outcome", "Outcome · 已执行命令", command_types,
             f"{_LIVE_REF_PREFIX}://{sid}/command_types")
        if not ev or all(e["value"] == "(无)" for e in ev):
            # benign 实时会话（无事件/警告）→ 降级摘要，非捏造。
            ev.clear()
            ev.append(
                DecisionEvidence(
                    kind="outcome",
                    label="决策证据",
                    value="(实时会话暂无事件/警告——benign 场景预期)",
                    ref=f"{_LIVE_REF_PREFIX}://{sid}/session",
                )
            )
        return tuple(ev)

    def _build_graph(
        self,
        event_types: tuple[str, ...],
        risk_levels: tuple[str, ...],
        recommended_actions: tuple[str, ...],
    ) -> EvidenceGraph:
        sid = self.scenario_id
        canon_ref = f"{_LIVE_REF_PREFIX}://{sid}"
        nodes: list[EvidenceGraphNode] = []
        edges: list[EvidenceGraphEdge] = []

        def _node(nid: str, ntype: str, label: str, ref: str) -> None:
            nodes.append(
                EvidenceGraphNode(
                    id=nid, type=ntype, label=label, ref=ref,
                    provenance_kind="REAL_SENSOR",
                )
            )

        def _edge(source: str, target: str, etype: str, ref: str) -> None:
            edges.append(
                EvidenceGraphEdge(source=source, target=target, type=etype, ref=ref)
            )

        # 因果链：Scenario → Event(observed_from) → Decision(caused_by) → Action(triggered)。
        _node("scn", "Scenario", sid, f"{canon_ref}/session")
        event_ids: list[str] = []
        for i, et in enumerate(event_types):
            nid = f"event-{i}"
            _node(nid, "Event", et, f"{canon_ref}/event[{i}]")
            _edge("scn", nid, "observed_from", f"{canon_ref}/event[{i}]")
            event_ids.append(nid)
        decision_ids: list[str] = []
        if event_ids:
            for i, rl in enumerate(risk_levels):
                nid = f"decision-{i}"
                _node(nid, "Decision", rl, f"{canon_ref}/risk[{i}]")
                decision_ids.append(nid)
                for prev in event_ids:
                    _edge(prev, nid, "caused_by", f"{canon_ref}/risk[{i}]")
        if decision_ids:
            for i, ra in enumerate(recommended_actions):
                nid = f"action-{i}"
                _node(nid, "Action", ra, f"{canon_ref}/action[{i}]")
                for prev in decision_ids:
                    _edge(prev, nid, "triggered", f"{canon_ref}/action[{i}]")
        # Episode / Link 节点：Live 无 episodes / cross_modal_links（AC-8 显式 absent）
        # → 不建节点（与 loader 守卫一致，禁 synthetic）。
        return EvidenceGraph(
            scenario_id=sid, nodes=tuple(nodes), edges=tuple(edges)
        )

    def _build_audio_evidence_live(self) -> tuple[AudioEvidenceNode, ...]:
        """ADR-0036 VM-13 Phase B（Owner 2026-08-16 决策）：把摄入的音频感知投影为
        ``audio_evidence``（provenance=REAL_SENSOR），与 Artifact（Phase C loader）共用同一
        ``AudioEvidenceNode`` schema，区别仅 ``provenance_kind``。

        铁律（VM-13 6 MUST / AC-12）：
        - fail-closed：``audio_result_to_live_audio`` 已拒绝 forbidden 字段（verdict/transcript/
          raw_audio/…），此处不再校验，但只透传上游既有字段，绝不新生成 UUID/墙钟/判定；
        - 无 ASR/LLM：不产 text/transcript/verdict/risk 解释；
        - 保留 provenance：每条 ``provenance_kind="REAL_SENSOR"``，``ref="live://audio/{idx}"``；
        - 幂等（VM-8）：同一有序流重放 N 次 → 同一节点列表（滚动窗口裁剪确定性，与帧一致）；
        - 未摄入音频（``_audio_events`` 空）→ 恒 ``()``，绝不编造。
        仅投影持久列表（``_audio_events``）的音频项——跨整个实时会话不随帧窗口滚动裁掉
        （Gate 4 真实缺陷 #2 修复），与时间轴 AUDIO 节点 ↔ Case Time 音频 Lane 同源一致（VM-13）。
        """
        nodes: list[AudioEvidenceNode] = []
        for ev in self._audio_events:
            la = ev["audio"]
            a_idx = ev["audio_index"]
            node: AudioEvidenceNode = AudioEvidenceNode(
                timestamp=la["timestamp"],
                kind=la["kind"],
                score=la["score"],
                confidence=la["confidence"],
                labels=la["labels"],
                source_segment_ids=la["source_segment_ids"],
                ref=f"{_LIVE_REF_PREFIX}://audio/{a_idx}",
                provenance_kind="REAL_SENSOR",
            )
            event_id = la.get("event_id")
            if event_id is not None:
                node["event_id"] = event_id
            nodes.append(node)
        return tuple(nodes)

    def _build_case_time_tracks(self) -> tuple[CaseTimeTrack, ...]:
        """Live Case Time 主轴（Step 6 全链路同步 · VM-13 Phase B）：仅音频 Lane。

        实时会话无 memory episodes（Live 不产记忆落库），故只铺音频 Lane。与 loader
        共用 ``CaseTimeTrack`` schema，T0 = 最早音频时间戳，相对时间确定性排序（VM-8 幂等）。
        无摄入音频 → 恒 ``()``（AC-12 / VM-13 6 MUST，绝不编造）。仅投影持久列表（``_audio_events``）音频项，
        与 ``_build_audio_evidence_live`` 同源同窗口（时间轴 AUDIO 节点 ↔ Case Time 音频标记一致）。
        """
        audio = self._build_audio_evidence_live()
        if not audio:
            return ()
        times = [float(a["timestamp"]) for a in audio]
        t0 = min(times)
        tracks: list[CaseTimeTrack] = []
        # 按时间戳确定排序（同源同窗口 → 同序，重放幂等）。
        for a in sorted(audio, key=lambda x: float(x["timestamp"])):
            ts = float(a["timestamp"])
            rel = ts - t0
            kind = a["kind"]
            label = _AUDIO_KIND_ZH.get(kind, kind)
            tracks.append(
                CaseTimeTrack(
                    time=round(rel, 3),
                    kind="audio",
                    ref=a["ref"],
                    label=label,
                )
            )
        return tuple(tracks)

    def to_evidence_projection(self) -> EvidenceProjection:
        """累积状态 → ``EvidenceProjection``（确定性，fail-closed，AC-4b 幂等）。

        Live 显式缺失字段（AC-8 / VM-7）：``gate=()`` / ``gate_passed=False`` /
        ``gate_degraded=False`` / ``fingerprints=None`` / ``trace_outcome_kinds=()`` /
        ``suppress_reasons=()``（刻意分歧：Live 是进行中的实时流、case 未终结，负向能力卡
        "为什么没有报警"仅对※已终结的 Canonical case※有意义，故 Live 恒不显示该卡）/
        ``episode_action_command_types=()`` / ``episodes=0`` / ``cross_modal_links=0``。
        ``audio_evidence``（VM-13 Phase B · Owner 2026-08-16）：由 ``_build_audio_evidence_live``
        投影摄入的 REAL_SENSOR 音频感知（与 Artifact 共用 AudioEvidenceNode 契约，区别仅
        provenance_kind）；未摄入音频 → 恒 ``()``，绝不编造（AC-12 / 6 MUST fail-closed）。
        """
        sid = self.scenario_id
        counts = self._build_counts()
        event_types = tuple(sorted(self._event_types))
        risk_levels = tuple(sorted(self._risk_levels))
        recommended_actions = tuple(sorted(self._recommended_actions))
        command_types = tuple(sorted(self._command_types))

        timeline = self._build_timeline()
        decision_evidence = self._build_decision_evidence(
            event_types, risk_levels, recommended_actions, command_types
        )
        graph = self._build_graph(event_types, risk_levels, recommended_actions)
        audio_evidence = self._build_audio_evidence_live()
        # Step 6（Case Time 全链路同步）：音频 Lane 经 CaseTimeTrack 并入统一主轴（仅音频，
        # 实时会话无 memory episodes），与 artifact 路径（loader 双 Lane）同源 schema（VM-1）。
        case_time_tracks = self._build_case_time_tracks()

        refs: list[str] = (
            [n["ref"] for n in timeline]
            + [e["ref"] for e in decision_evidence]
            + [n["ref"] for n in graph["nodes"]]
            + [e["ref"] for e in graph["edges"]]
        )

        scenario = ScenarioEvidence(
            scenario_id=sid,
            ok=True,  # 实时会话进行中（无集成 Gate，故 ok 仅表会话健康）
            mode=self.mode,
            n_frames=self._total_frames,
            # scenario_fingerprint：用实时会话标识（真实标识，非伪造集成指纹）；
            # 集成 FingerprintPair 由 fingerprints=None 显式表达 absent。
            scenario_fingerprint=sid,
            counts=counts,
            event_types=event_types,
            risk_levels=risk_levels,
            recommended_actions=recommended_actions,
            command_types=command_types,
            trace_outcome_kinds=(),
            # 刻意分歧（ADR-0036）：Live 进行中、case 未终结，"为什么没有报警"卡仅对
            # 已终结 Canonical case 有意义；此处恒 () 使渲染层不出现负向能力卡（诚实优先）。
            suppress_reasons=(),
            episode_action_command_types=(),
            # P1（干预回执 + 闭环可达性）：Live 进行中、case 未终结，无已派发指令回执
            # （真实派发回执由 Canonical loader 从 artifacts.command_types 派生）；此处恒 ()，
            # 使渲染层呈现实诚空卡，绝不编造派发/送达/时延（AC-12）。
            intervention_dispatch=(),
            timeline=timeline,
            decision_evidence=decision_evidence,
            # VM-13 Phase B（Owner 2026-08-16）：Live 真实摄入音频 → REAL_SENSOR 派生
            # audio_evidence（与 Artifact 共用 AudioEvidenceNode 契约）；未摄入恒 ``()``，
            # 绝不编造（AC-12 / 6 MUST fail-closed）。
            audio_evidence=audio_evidence,
            # Step 6：Live Case Time 音频 Lane（相对最早音频 T0；无音频恒 ()）。
            case_time_tracks=case_time_tracks,
            # Live 不产 memory episodes（实时会话无记忆落库）→ 显式 absent（AC-8 禁伪造）。
            memory_episodes=(),
            gate=(),
            gate_passed=False,
            gate_degraded=False,
            fingerprints=None,
            refs=tuple(refs),
            graph=graph,
        )
        # 确定性 meta：generated_at 用常量 "live"（无墙钟，保重放幂等）。
        return EvidenceProjection(
            meta=ProjectionMeta(generated_at="live", scenario_count=1),
            scenarios=(scenario,),
        )


def build_live_presentation(
    projection: EvidenceProjection,
    *,
    scenario_index: int = 0,
    live_ws_path: str = "/ws",
) -> tuple[EvidenceProjection, CasePresentationDescriptor]:
    """Live ``EvidenceProjection`` → （投影 + 纯展示编排，VM-11）。

    - 事实：来自 ``projection``（VM-1，EvidenceProjection 唯一事实源）；
    - 编排：media_binding 绑定 ``LiveFrameSource``（媒体字节由 Media Source Adapter 经
      ref 解析，不进 View Model；Slice A 的 ``resolve_media_source`` 对 LiveFrameSource
      返回 ``None`` → 前端显示「无媒体绑定」脚注，诚实表达无媒体资产）；
    - P0-1：``live_ws_path`` 为纯展示元数据（行动闭环面板 WS 连接路径，非事实字段），
      由 Host（gateway）注入 ``demo_settings.ws_path``。
    """
    scenarios = projection.get("scenarios")
    if not isinstance(scenarios, tuple) or not scenarios:
        raise LiveIngestError("EvidenceProjection 无场景，无法派生 Live 展示编排（fail-closed）")
    if not 0 <= scenario_index < len(scenarios):
        raise LiveIngestError(
            f"scenario_index 越界：{scenario_index} 不在 [0, {len(scenarios)})（fail-closed）"
        )
    sid = scenarios[scenario_index]["scenario_id"]
    descriptor = CasePresentationDescriptor(
        case_id=sid,
        title=f"Live · {sid}",
        scenario_ref=sid,
        media_binding=MediaBinding(
            source_kind="LiveFrameSource",
            ref=f"{sid}#live",
        ),
        first_screen_layout=FirstScreenLayout(panels=_LIVE_PANELS),
        time_mapping=TimeMapping(media_duration_s=60.0, mode="linear"),
        live_ws_path=live_ws_path,
    )
    return projection, descriptor


__all__ = [
    "CasePresentationDescriptor",
    "LiveAudioFrame",
    "LiveFrame",
    "LiveIngestError",
    "ProjectionAccumulator",
    "audio_result_to_live_audio",
    "build_live_presentation",
    "frame_result_to_live_frame",
]
