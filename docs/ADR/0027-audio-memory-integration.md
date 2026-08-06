# ADR-0027: 音频记忆集成（Audio Memory Integration）

- **Status**: Proposed（review-ready，待 Owner 冻结）
- **Date**: 2026-08-06
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0024（Memory 架构·三类记忆模型与 Memory Policy）
  - ADR-0025（Memory Consumer 架构·Retrieval/Aggregation/Context Builder/Reasoning Interface）
  - ADR-0026（音频感知链路·具体设计，§5.1 AudioAdapter / §6 CrossModalEvidence / §7 AudioEvidencePolicy / §10 开放项）
  - ADR-0022（证据链·多模态接口：EvidenceItem + EvidenceAggregator + WarningEvent.evidence_items）
  - ADR-0019（多模态证据融合：Vision/Audio 双独立感知链 + Evidence Fusion 阶段）
  - ADR-0023（身份连续性：track_id / visitor_instance_id / person_identity_id 三层，v1 不冒充真实身份）
  - ADR-0014（事件 Schema 冻结）/ ADR-0001（仅产事实不裁决）/ ADR-0002（隐私铁律）
- **Phase**: v2 · Phase 3（音频双通道）→ Memory 闭环

---

## 0. 背景与动机（Context）

v2 的音频感知链路（ADR-0026）已落地：`Audio → AudioSegmentEvent → AudioPerceptionEvent → AudioAdapter → RiskSignal(source=AUDIO) → EvidenceItem(AUDIO)`。但它**只走到了 Evidence 层**，尚未真正进入 Memory。

### 0.1 一个关键事实：音频其实已经能间接进 Memory

当前链路中，音频 `RiskSignal` 与视觉 `RiskSignal` 在 `DecisionPolicy` 汇聚，产出 `WarningEvent`，再由 **Episode Builder** 投影为 `EpisodicRecord` 写入 Memory。也就是说：

```
Audio Perception
      │
      ▼
AudioAdapter → RiskSignal(AUDIO) ─┐
                                   ├─▶ DecisionPolicy ─▶ WarningEvent ─▶ EpisodeBuilder ─▶ EpisodicRecord
Vision Perception ─▶ RiskSignal   ─┘
```

**音频并非"进不了 Memory"，而是"进了 Memory 却 indistinguishable（不可区分）"。** 这正是本 ADR 要解决的核心问题——它不是要新开一条写入链，而是要补齐**音频在 Memory 中的可区分性、可关联性与可消费性**。

### 0.2 四个缺口（G1–G4）

- **G1 记忆不含来源模态**：音频驱动的 episode 与视觉 episode 都是同一个 `EpisodicRecord` 形状，Memory 无法回答"这条 episode 来自声音还是画面"。`summary` 不反映"哭腔/通话/急促"。
- **G2 证据模态断层（技术债）**：`records.EvidenceRef` 带 `modality`，但 `audio_adapter.py` 实际用的是 `core/event.EvidenceRef`（**无 modality**）；ADR-0022 规划的 `EvidenceItem` 至今未落地。两条"证据"世界并存，音频证据挂不上 episode。
- **G3 跨模态不关联**：同一访客的视觉 episode 与音频 episode 在 Memory 里是两条孤立记录；ADR-0026 §6 的 `CrossModalEvidence` 关联层尚未实现。
- **G4 Consumer 不感知音频**：`VisitorProfile` / `RiskPattern` 不区分音频模式，`ReasoningInput` 里的音频证据"在了但没被用"。Agent 只能回答"访问次数增加"，回答不了"过去 7 天夜间出现 5 次异常哭泣声"。

### 0.3 本 ADR 的目标

把音频**作为一等模态**接入既有 Memory 架构——沿用 ADR-0024/0025 的写入与消费管道，**不改 DecisionPolicy、不新增写入链、不污染 Memory 语义**。

---

## 1. 决策（Decision）

**音频不新开 Memory 写入链；在既有 `EpisodeBuilder → EpisodicRecord → Consumer` 管道上，通过 D1–D9 九处增量，把音频接进 Memory：** 统一证据对象 `EvidenceItem`（独立存储、`evidence_refs` 以 ID 引用）+ 多模态标记 `modalities` + 音频原生身份 `AudioSessionId` + 跨模态关联 `CrossModalLink` + Consumer 音频感知 + `EvidenceModality` 枚举契约（继承 ADR-0022）+ Schema Evolution 兼容 + Audio Evidence 分层留存与可变生命周期 `EvidenceAssetState`。

一句话：**DecisionPolicy 是 Memory 的唯一门槛；音频必须经过这道门槛，与视觉同权进入 Memory。**

---

## 2. 决策要点（D1–D9，按优先级排列，D3 为基石置于前）

### D3（基石·最高优先级）：不新增 Audio Memory 写入链

> 音频仍只经 `RiskSignal → DecisionPolicy → WarningEvent → EpisodeBuilder` 入 Memory。

**这是全 ADR 最重要的决策，必须保留。** 反例（禁止）：

```
AudioDetector → AudioMemory   （独立音频记忆孤岛）
VisionDetector → VisionMemory （视觉记忆孤岛）
```

若 YAMNet 直接把 `crying=0.8` 写入 Memory，之后发现"其实是电视声"，Memory 已被污染且无法回滚到正确判定。

`DecisionPolicy` 作为 Memory 门槛的意义（承接 ADR-0001/0010）：

- 只有经过系统生命周期确认的事件（RiskSignal 经 Decision 产出 WarningEvent）才被记录；
- Memory 记录的是"系统已确认的风险事件"，不是"某个感知模型的原始打分"；
- 误判由 `memory_status=INVALID`（ADR-0024 §5.7）修正，**不污染**正常 episode。

**`DecisionPolicy` 零改动**——它只消费既有 `RiskSignal`，不感知来源模态（ADR-0026 §5.1）。

### D1：EpisodicRecord 增加 `modalities: list[EvidenceModality]`

**评审修正**：原方案 `source_modality: str`（单值）改为 **`modalities: list[EvidenceModality]`**（多值列表）。`EvidenceModality` 枚举值集见 **D7**（继承 ADR-0022，不引入 `SENSOR`/`POSE`/`UNKNOWN`）。

理由：本项目的真实域是**银发反诈**（非防跌倒）——多模态诈骗 episode 例如「疑似冒充熟人/公检法诈骗」= `VISION(老人在家神色紧张、翻找银行卡)` + `AUDIO(telephone_persistent 长时间通话 + distress_cry 哭腔)` + `VISION(kind=pose_panic_avoidance 恐慌回避姿态)`。它不是"一个模态"，而是多模态合证（视觉神态 + 通话特征 + 声学 distress）。单值会限制表达力。

```python
modalities: list[EvidenceModality] = field(default_factory=list)
# 例：纯音频 episode -> [EvidenceModality.AUDIO]
#     复合诈骗 episode -> [EvidenceModality.VISION, EvidenceModality.AUDIO]
#     （姿态（恐慌回避）归 VISION，用 kind=pose_* 表达，不再单列 POSE 模态，见 D7）
```

- `EpisodicRecord.modalities` 由 `EpisodeBuilder` 从 `evidence_refs` 解析出的 `EvidenceItem.modality` 收敛（也可由 WarningEvent 的来源显式标注）。
- 与 ADR-0024 的"Memory 只记录经过确认的事件"一致：modalities 只是**标签**，不参与任何打分。

### D2：落地 `EvidenceItem` 为独立存储的不可变证据；`evidence_refs` 改为 ID 引用（关 G2，修复接口契约）

**关键修正（评审反馈 #3）**：ADR-0024:638/663-665 明确 `evidence_refs` 是"引用的证据项 **ID**（`[ev_001, ev_002]`）"，通过 ID 链接独立的 `EvidenceItem`。因此本 ADR **不内嵌** EvidenceItem，采用**引用模型**：`EpisodicRecord.evidence_refs` 存 `evidence_id` 字符串列表，独立 `EvidenceItem` 按 ID 解析。

`EvidenceModality` 枚举值集见 **D7**（继承 ADR-0022，不增 `SENSOR`/`POSE`/`UNKNOWN`）；`RetentionTier` 见下。

```python
@dataclass
class EvidenceItem:                 # 独立存储、不可变（ADR-0024 I2 Monotonicity）
    evidence_id: str                # 全局唯一，被 evidence_refs 引用
    modality: EvidenceModality
    kind: str                       # segment | clip | snapshot | pose_* | ...（模态内类型）
    uri: str | None                 # 本地路径 / 片段 id（原片不上传，ADR-0002 §3.3）
    captured_at: datetime           # UTC
    confidence: float | None = None   # None = 未知（迁移数据绝不伪造 1.0）
    metadata: dict[str, Any] = field(default_factory=dict)  # 模态内附加（如 audio kind / score / duration）
    retention_tier: RetentionTier = RetentionTier.SHORT      # 留存层级（见 D9）
    expires_at: datetime | None = None                      # SHORT/MEDIUM 到期；LONG=None 永久

class RetentionTier(str, Enum):
    SHORT = "short"      # 原始音频片段：24h
    MEDIUM = "medium"    # 特征摘要：30d
    LONG = "long"        # 语义模式标签：永久
```

```python
class EpisodicRecord:
    ...
    evidence_refs: list[str] = field(default_factory=list)   # evidence_id 字符串列表（引用，非内嵌）
    modalities: list[EvidenceModality] = field(default_factory=list)
    audio_session_id: str | None = None
```

收敛规则（修复双 `EvidenceRef` 架构漂移）：

- 删除 `core/event.py` 的 `EvidenceRef`（`{kind, uri, timestamp}`，无 `evidence_id`，与 ADR-0024 引用语义不符）；
- `records.py` 的 `EvidenceRef`（`{evidence_id, modality, captured_at, uri}`）**废弃**：其 `evidence_id` 直接成为 `evidence_refs` 的字符串元素，事实（`modality/captured_at/uri`）落到独立 `EvidenceItem`；
- `EpisodicRecord.evidence_refs` 元素类型由 `EvidenceRef` 对象**降为 `str` ID**（字段名 `evidence_refs` 保留，`EPISODIC_RECORD_DICT_KEYS` 闭合契约不受影响）；
- `EvidenceItem` 独立存储于证据库；`AudioEvidenceCollector`（ADR-0026 §5.1/§7）产出 `EvidenceItem(modality=AUDIO, kind∈{segment, clip})`；读取端 `EpisodicRecord → evidence_refs(evidence_id) → EvidenceItem` 经 ID 解析，Trust Layer 路径与 ADR-0024:663-665 一致；
- 由此关闭 ADR-0022 "EvidenceItem 未落地" 遗留项。
- 所有证据模态（video→VISION / audio→AUDIO / identity→IDENTITY）统一收敛到 `EvidenceItem`；`POSE` 归 `VISION`（见 D7），`SENSOR` 属 `SourceModality`（ADR-0021），不在此枚举。

### D4：音频原生身份用 `AudioSessionId`，不强绑 `visitor_instance_id`（评审修正）

**原风险**：音频天然无身份（"客厅传来哭声"无法确认是老人/电视/手机/访客）。原方案"纯音频需 `visitor_instance_id`"会把音频**强行绑定**到视觉访客，带来隐私与误绑定风险。

**修正**：引入音频原生身份 `AudioSessionId`，绑定是**可选且单向**的：

```
Audio Identity (audio_session_id="audio_session_001")
        │
        │  (可选绑定，仅当视觉在场时)
        ▼
Visitor Identity (visitor_instance_id="visitor_123")
        ▲
        │  CrossModalLink（不反向）
```

- `EpisodicRecord` 新增可选字段 `audio_session_id: str | None = None`；
- **纯音频 episode**：`visitor_instance_id` 可为 `None`（与 ADR-0023 v1 `person_identity_id=None` 同源），仅持 `audio_session_id`，保持匿名；
- **视觉在场时**：由 `CrossModalLink`（D5）把 `audio_session_id` 关联到同访客的 `visitor_instance_id`；
- **绝不反向**：禁止"用视觉访客 id 反填音频 episode 的 visitor_instance_id"来制造虚假身份归属；
- 身份判定权重（何时算"同一访客"）归 `CrossModalEvidence.overlap_with_visitor`（ADR-0026 §10 开放项，本 ADR 不抢答）。

> 注意：当前 `EpisodicRecord.__post_init__` 强制 `visitor_instance_id` 非空（I4 相关）。D4 要求**放宽该不变式**：仅当 `audio_session_id is None` 时 `visitor_instance_id` 才必填；音频-only episode 用 `audio_session_id` 满足可解释性（I4 的 `source_event_ids` 仍必填，溯源链不断）。详见 D8。

### D5：跨模态关联用 `CrossModalLink`（关系 / 边，不合并 episode）

> 这是本 ADR 最有价值的部分：**关联的是关系，不是实体。**

错误设计（禁止）：

```
VisionEpisode + AudioEpisode = 新合并 Episode   # Memory 失去来源模态
```

正确设计（知识图谱式 Node—Edge—Node）：

```
Episode A (VISION: 老人在家神色紧张、翻找银行卡)
      │
      │  CrossModalLink(relationship="supports", time_overlap=…)
      │
Episode B (AUDIO: telephone_persistent 长时间通话 + distress_cry 哭腔)
```

```python
@dataclass
class CrossModalLink:
    link_id: str
    episode_ids: list[str]          # 关联 episode 的 record_id（≥2）
    relationship: str               # "co_occurs" | "supports" | ...（白名单枚举，非自由文本）
    time_overlap: tuple[datetime, datetime] | None
    confidence: float               # 0~1（关联可信度，非风险分）
    created_at: datetime            # UTC
    # —— 证据级关联（建议2，评审补充；粒度细化，实现期启用）——
    supporting_evidence_ids: list[str] = field(default_factory=list)
    #   与 episode_ids 平行：当关联需细化到"哪条证据支撑哪条证据"时使用。
    #   例：Vision Episode A 的 EvidenceItem(person_bbox_001) 与
    #       Audio Episode B 的 EvidenceItem(telephone_persistent_003) 构成证据对，
    #       解释粒度从 episode 级下钻到 evidence 级。
    #   v1 可留空（仅 episode 级关联）；证据级关联作为后续增强，不阻塞本 ADR 冻结。
```

- `CrossModalLink` **不是** MemoryRecord 子类型（不继承 `EpisodicRecord`/`SemanticAggregate`），而是独立的轻量关联索引，挂在两边 episode 的 `corrections` 之外或独立存储；
- 触发与权重策略（时间窗重叠 + 关联强度）归实现期 / 融合 ADR（ADR-0026 §10 开放项）；
- Agent 可据此回答"为什么判断疑似诈骗风险？"→ 视觉 老人紧张翻找银行卡 + 关联音频 长时间通话 + 哭腔，生成解释性推理。

### D6：Consumer 变 audio-aware（关 G4，零新组件）

承接 ADR-0025 四组件，仅增量、不新建组件、不引入分数：

- **Retrieval**：支持按 `modalities` 过滤（如"取所有含 AUDIO 的 episode"）；
- **Aggregation**：新增 `audio_patterns: list[str]` —— **纯描述性标签，非分数**，例如 `["night_crying", "persistent_telephone", "abnormal_cough"]`；可选 `audio_episode_ratio`（该类 episode 占比，用于排序提示，不是决策输入）；
- **ReasoningInput**：经 `evidence_refs`（evidence_id）解析消费音频 `EvidenceItem`；可选加 `modalities: list[EvidenceModality]` 提示字段；
- **不变式（必须守）**：ADR-0025 C1（Consumer 不产出 / 不改 Risk Score）+ C2（只读）**不变**。模式标签如 `night_crying_pattern` 只是"描述发生了什么"，绝不变成 `health_risk_score=0.83`（那是 DecisionPolicy 的职责）。否则 Memory 开始替 Decision 做判断，边界崩塌。

### D7（新增·评审建议）：`EvidenceModality` 枚举契约（有界上下文，继承 ADR-0022）

**问题（评审反馈 #2）**：ADR-0022 §3.1 已把 `EvidenceModality` 冻结为 `VISION / AUDIO / IDENTITY`，并明确它与 `SourceModality`（ADR-0021）分属不同限界上下文——"证据不可能是 `SENSOR`、信号来源不可能是 `IDENTITY`"，刻意不共享类名。本 ADR 初稿擅自删除 `IDENTITY`、新增 `SENSOR`/`POSE`/`UNKNOWN`，却描述为"落地 ADR-0022"，既未声明扩展也未声明取代，会导致既有 `IDENTITY` 证据无法反序列化，并重新混淆"证据模态"与"信号来源模态"。

**决策**：本 ADR **不修改 ADR-0022 的 `EvidenceModality` 值集**，完全继承：

```python
class EvidenceModality(str, Enum):   # 证据上下文的"证据模态"（ADR-0022 §3.1，本 ADR 不增删）
    VISION = "vision"
    AUDIO = "audio"
    IDENTITY = "identity"            # 身份证据（人脸截图/声纹片段）；保留，不删除
# 注：SENSOR 属 ADR-0021 SourceModality（"信号由哪类传感器产生"），不在此枚举；
#     POSE 不单列，归 VISION（姿态由视觉推导，modality=VISION，kind=pose_*）；
#     UNKNOWN 不引入（历史记录缺 modality 用空列表 [] 表达，见 D8）。
```

逐条论证：

- **保留 `IDENTITY`**：身份证据（人脸截图/声纹片段）是真实证据模态，ADR-0022 已落地；本 ADR 不删除，既有 `IDENTITY` 证据反序列化不受影响。
- **`SENSOR` 不引入**：它属于 `SourceModality`（ADR-0021，描述"信号由哪类**传感器**产生"），与"这条**证据**属于哪个模态"是不同限界上下文。混入会迫使证据携带对己无意义的值。需要记录"证据来自哪个传感器"时，用 `SourceModality`（在 `RiskSignal` 层），而非污染 `EvidenceModality`。
- **`POSE` 不单列，归 `VISION`**：姿态（恐慌回避等）由摄像头帧推导，本质属视觉证据；以 `modality=VISION` + `kind=pose_panic_avoidance` 表达，既保留区分度又不破坏枚举闭合。
- **不引入 `UNKNOWN` 哨兵**：历史 episode 在 ADR-0027 前本就无证据（`evidence_refs` 为空），缺 modality 用 `[]`（空列表）表达（见 D8），无需新增枚举值；避免改变已冻结的值集。

**契约测试（枚举闭合）**：新增 `test_evidence_modality_closure` —— 断言值集恰为 `{VISION, AUDIO, IDENTITY}`、与 `SourceModality` 无交叉 import / 无共享值、既有 `IDENTITY` 证据序列化往返稳定、v1 旧 `IDENTITY` 数据读为 `IDENTITY` 不变。

### D8（新增·评审建议）：Schema Evolution 兼容策略

Memory 是长期数据，新增字段必须保证**历史 Memory 可读**。

- **新字段全部 optional**：`modalities`、`audio_session_id` 缺省时不影响旧记录读取；
- **旧 episode 默认模态**：读取时若 `modalities` 缺失 → `[]`（空列表；v1 episode 本就无证据，ADR-0024 v1 `evidence_refs` 为空，**不引入 `UNKNOWN` 枚举值**，见 D7）；
- **`evidence_refs` 元素类型迁移**：v1 实际 `evidence_refs` 恒为空（ADR-0022 未落地），无真实 `EvidenceRef` 需转；若未来有 v1 残留（list of `records.EvidenceRef` 对象），读取端 coerce：取 `evidence_id` 入 `evidence_refs` 字符串列表，事实落独立 `EvidenceItem`，**`confidence=None`（未知，绝不伪造 1.0）**，`metadata={}`；
- **`record_id` 不变**：迁移只发生在**读取端 coerce**，绝不重写 / 重算已存记录，保证 `record_id`（I1 幂等键）与存储字节稳定；
- **`schema_version` 语义**：v1 = 音频接入前（视觉为主，`evidence_refs` 为 `EvidenceRef`/空）；v2 = 本 ADR 后（`evidence_refs: list[str]` 引用 `EvidenceItem` + `modalities` + `audio_session_id`）。`from_dict` 同时接受 v1/v2 两种形状，向前向后兼容；
- **`visitor_instance_id` 不变式放宽**（配合 D4）：仅当 `audio_session_id is None` 时强制必填；v1 旧记录均有 `visitor_instance_id`，自动满足。

**字段对照与闭合契约（修复自相矛盾，评审反馈 #4）**：统一使用稳定幂等键 `record_id`（**非** `episode_id`）、引用字段 `evidence_refs`（**非** `evidence_items`）。

| 字段 | v1（schema_version=1） | v2（schema_version=2） |
|------|------------------------|------------------------|
| `record_id` | ✅（幂等键，稳定） | ✅（同，不变） |
| `visitor_instance_id` | ✅（必填） | ✅（仅当 `audio_session_id is None` 时必填） |
| `evidence_refs` | `list[str]`（v1 恒空） | `list[str]`（evidence_id 引用独立 `EvidenceItem`） |
| `modalities` | 缺省 → `[]` | `list[str]`（EvidenceModality 值） |
| `audio_session_id` | 缺省 → `None` | `str \| None` |
| `schema_version` | `1` | `2` |

```json
// v1（音频接入前，视觉为主）
{
  "record_id": "ep-2026-0801-visitor_123",
  "visitor_instance_id": "visitor_123",
  "summary": "老人在家 18:45–19:10，无异常",
  "evidence_refs": [],
  "schema_version": 1
}

// v2（本 ADR 后，含音频）
{
  "record_id": "ep-2026-0806-audio_session_001",
  "visitor_instance_id": null,
  "summary": "夜间异常：长时间通话 + 哭腔",
  "evidence_refs": ["ev_audio_001", "ev_audio_002"],
  "modalities": ["audio"],
  "audio_session_id": "audio_session_001",
  "schema_version": 2
}
```

- `EPISODIC_RECORD_DICT_KEYS`：v1 = `{record_id, visitor_instance_id, summary, evidence_refs, schema_version, ...既有键}`；v2 = v1 键集合 ∪ `{modalities, audio_session_id}`。两版键集合均**闭合且显式**，反序列化与契约测试据此确定，杜绝分叉。

---

## 2.1 决策要点（续·D9）

### D9（新增·评审建议）：Audio Evidence 生命周期 / 留存策略（Retention Policy）

**问题**：音频比视觉更敏感——声纹可识别个人、可能含对话内容。ADR-0002 隐私铁律要求"音频不出 Home 端、原始音频不上传"。但当前 `EvidenceItem.uri` 只指向本地片段，**没有定义片段保存多久、何时删除**，且无"失效状态"数据模型——若不显式定义，Memory 将长期滞留 `audio_clip.wav` 而无人清理；若只删文件不清状态，Consumer/Agent 无法区分"已按策略销毁"与"文件丢失/存储故障"，留下悬空敏感 URI。

**方案**：引入**分层留存（tiered retention）** + **独立可变生命周期记录 `EvidenceAssetState`**（评审反馈 #3）。

| 层级 | 内容 | 留存期 | 说明 |
|------|------|--------|------|
| `SHORT` | 原始音频片段（raw clip，`.wav`） | **24h** | 声纹/对话最敏感；仅用于短时复核，过期即删 |
| `MEDIUM` | 特征摘要（能量/谱特征/感知打分，如 `audio_distress_cry=0.96`） | **30d** | 不含波形，可支撑短期模式统计 |
| `LONG` | 语义模式标签（如 `night_crying`、`persistent_telephone`） | **永久** | 已脱敏为描述性标签，可长期支撑认知 |

```python
# RetentionTier 见 D2（与 EvidenceModality 同定义处）
# EvidenceItem：不可变事实记录（见 D2），uri 永不被改写（满足 ADR-0024 I2 单调性 / D8 字节稳定）
@dataclass
class EvidenceItem:
    ...  # 见 D2 完整定义（含 retention_tier / expires_at）
    retention_tier: RetentionTier = RetentionTier.SHORT   # 默认原始片段最短留存
    expires_at: datetime | None = None                   # SHORT/MEDIUM 由留存策略算定期限；LONG=None 永久

# —— 可变生命周期记录（NEW，评审反馈 #3）：与不可变 episode / EvidenceItem 分离 ——
class AssetStatus(str, Enum):
    ACTIVE = "active"                 # 原片在本地、可访问
    EXPIRED = "expired"               # 已按策略删除（或本就不存在），不可访问
    DELETE_FAILED = "delete_failed"   # 删除失败，待重试 / 升级告警（可审计）

@dataclass
class EvidenceAssetState:
    evidence_id: str                  # FK → EvidenceItem.evidence_id（episode 仅引用此 ID）
    status: AssetStatus = AssetStatus.ACTIVE
    asset_uri: str | None = None      # 实际文件位置（可随迁移变化）
    tier: RetentionTier | None = None
    expires_at: datetime | None = None
    deleted_at: datetime | None = None
    retry_count: int = 0
    last_error: str | None = None
    last_attempt: datetime | None = None
    last_alert_at: datetime | None = None
```

**执行语义（修复悬空 URI 与不可审计删除，评审反馈 #3）**：

- **职责分离**：`EpisodicRecord`（不可变）与 `EvidenceItem`（不可变事实）**都不持有"失效标记"**；资产存亡只记录在 `EvidenceAssetState`（可变）。Consumer/Agent 通过 `EvidenceAssetState.status` 判断原片是否可用，**不再靠猜测 `uri` 是否存在**，彻底消除"悬空敏感 URI"歧义。
- **留存执行器**（独立轻量组件，**不在** `DecisionPolicy`/`Consumer` 内）周期性扫描 `EvidenceItem`，对 `expires_at` 到期者执行删除：
  - **幂等删除**：若 `asset_uri` 文件已不存在 → 直接置 `EXPIRED`（不报错，满足幂等）；
  - **成功** → 置 `EXPIRED`，填 `deleted_at`；**绝不删除 `EpisodicRecord` / `EvidenceItem`**（记忆事实与证据元数据保留，只清敏感原片）；
  - **失败**（文件锁/权限）→ 置 `DELETE_FAILED`，`retry_count+=1`、`last_error` 记录、安排退避重试；`retry_count` 超阈值（如 3）→ `last_alert_at` + 升级告警 + 审计日志；**绝不静默吞错**；
  - **用户显式擦除请求**（数据主体权利）→ 强制触发删除并置终态（成功 `EXPIRED` / 失败 `DELETE_FAILED` 仍重试），`EvidenceItem` 事实不动，擦除只记于 `EvidenceAssetState`；
- **降级守恒**：`SHORT` 原片过期后，若 `MEDIUM`/`LONG` 摘要已生成，episode 仍可被检索与解释（解释性推理基于语义标签，不依赖原片）；与 D5"解释粒度到 episode/证据"一致；`status != ACTIVE` 时 Consumer 自动降级到语义层；
- **隐私边界（ADR-0002）**：所有层级均仅本地（Home 端），`uri` 不上传；`expires_at` 用本地 UTC 计算（基于 `captured_at + tier 时长`），无外部时钟依赖，规避时钟偏差；
- **可审计**：`DELETE_FAILED` 必留 `last_error`/`retry_count`/`last_alert_at`，原始音频超期必有迹可查，不会"默默超过 24h 仍在"。

**不变式（必须守）**：

- 每个 `EvidenceItem(AUDIO)` 必带 `retention_tier`（I4 扩展）；
- `LONG` 语义标签一旦写入**不可自动删除**（除非显式用户擦除请求，归数据主体权利实现）；
- 删除只作用于 `EvidenceAssetState` 状态 + 本地 `asset_uri` 文件，**不改写 `record_id` / `evidence_id` / `EvidenceItem`**（与 D8 兼容）；
- 留存执行器失败须**告警 + 重试 + 升级**，**不阻塞主链路**（音频感知/Decision/Memory 不受影响）。

---

## 3. 动机（Rationale）

1. **DecisionPolicy 作为 Memory 门槛（D3）**：避免感知原始打分直接污染 Memory。YAMNet `crying=0.8` 若直写 Memory，事后发现是电视声，无法干净回滚；经 Decision 确认后写入，误判走 `INVALID` 修正路径，Memory 始终"经过确认"。
2. **多模态列表而非单值（D1）**：复合「疑似电信诈骗」= 视觉（老人紧张翻找银行卡）+ 音频（长时间通话 + 哭腔）+ 视觉（恐慌回避姿态，`modality=VISION, kind=pose_*`）多证，单值 `source_modality` 无法表达"复合证据"，未来必然返工。
3. **统一 EvidenceItem（D2）**：双 `EvidenceRef` 是典型架构漂移（同名词、异契约）。统一后 video→VISION / audio→AUDIO / identity→IDENTITY 共用一套证据语义（POSE 归 VISION、SENSOR 属 SourceModality，见 D7），是 Memory 跨模态查询的基石；`evidence_refs` 以 ID 引用，不内嵌证据事实。
4. **AudioSessionId 而非强绑 visitor（D4）**：音频无身份是物理事实；强行绑定制造虚假归属与隐私风险。可选单向绑定（仅视觉在场时）既保留关联能力又不污染身份语义。
5. **CrossModalLink 是关系不是实体（D5）**：合并 episode 会丢失来源模态，破坏 ADR-0024 "Memory 记录经过确认的事件"的可解释性；边式关联支持未来 Agent 解释性推理，可扩展到任意模态组合。
6. **Consumer 仅标签不分数（D6）**：守住 ADR-0025 C1/C2，Memory 不替 Decision 做判断，架构边界不崩塌。
7. **Schema Evolution（D8）**：Memory 是跨版本长期资产，字段演进必须向后兼容，否则 ADR-0027 合并后历史 Memory 全部不可读。

---

## 4. 后果（Consequences）

### 正面
- 音频成为 Memory 一等模态：`modalities` + `EvidenceItem(AUDIO)` 让 episode 可区分、可检索、可解释。
- 跨模态关联层 `CrossModalLink` 落地，为 Agent 解释性推理（"为何判断为诈骗风险"）提供结构化依据。
- 纯音频事件也能进入 Memory（匿名 `audio_session_id`），不依赖视觉在场。
- Consumer 能产出音频模式标签，认知维度从"访客行为"扩展到"声学异常模式"。

### 负面 / 代价
- `EpisodicRecord` 字段扩展 + 不变式放宽（D4/D7/D8），需同步更新 `EPISODIC_RECORD_DICT_KEYS` 闭合契约测试、`EvidenceModality` 枚举闭合测试（D7）与 `records_equal` 比对。
- `evidence_refs` 由 `list[EvidenceRef]` 改为 `list[str]`（ID 引用），废弃双 `EvidenceRef`、`EvidenceItem` 独立存储，波及 `records.py` / `core/event.py` / `audio_adapter.py` / 序列化与 ID 解析路径。
- 新增 `CrossModalLink` 存储与查询索引（轻量，但需持久化与回放测试）。
- 新增 Audio Evidence 留存执行器（D9）+ 可变 `EvidenceAssetState` 生命周期记录（幂等删除 / 重试 / 升级告警 / 用户擦除），需与存储/定时任务对齐，失败须告警+重试不阻塞主链路。
- 身份绑定策略（何时 audio_session 归并到 visitor）需实现期定义权重（ADR-0026 §10 开放项）。

### 必须承担的技术债 / 后续动作
- 实现 `EvidenceItem` 后，关闭 ADR-0022 "EvidenceItem 未落地" 的遗留项。
- `CrossModalEvidence.overlap_with_visitor` 权重策略仍需融合 ADR / 实现期敲定。
- Environment Semantic（ADR-0024 Stage G）是否加 `modality` 维度——留待后续 ADR。
- 音频片段本地留存 / 过期策略（ADR-0026 §7 AudioEvidencePolicy）需与 Memory 的 expiration 体系对齐。

---

## 5. 替代方案（Alternatives）

- **独立 AudioMemory 写入链**：否决。制造视觉/音频记忆孤岛，破坏 ADR-0024 "Memory 不相信感知、只记录经确认事件" 原则，且 DecisionPolicy 门槛失效。
- **单值 `source_modality: str`**：否决（D1 评审修正）。无法表达复合模态 episode。
- **音频 episode 强绑 `visitor_instance_id`**：否决（D4 评审修正）。制造虚假身份归属与隐私风险。
- **合并跨模态 episode 为单一记录**：否决（D5）。丢失来源模态，破坏可解释性。
- **Consumer 产出音频风险分**：否决（D6）。越界 ADR-0025 C1/C2，Memory 替 Decision 做判断。

---

## 6. 实施切片（依赖顺序，评审调整）

> 评审把契约测试 E 提前到跨模态 C 与 Consumer D 之前，先确保 "Audio → Memory" 稳定，再做 "Audio + Vision → Memory Graph" 高级能力。

```
A  EvidenceItem 统一（core/event.py 落地 EvidenceItem（独立存储）+ EvidenceModality（继承 ADR-0022）；
   废弃双 EvidenceRef；evidence_refs 改 list[str] ID 引用；serializer + ID 解析适配）
        │
        ▼
B  Episode Builder Audio-aware（audio 特征 → 专属 summary + 挂 EvidenceItem(AUDIO)
    + 写 modalities + audio_session_id；放宽 visitor_instance_id 不变式；强制 I4）
        │
        ▼
E  契约测试（audio episode 契约：modalities+证据+I4；含音频的回放 baseline；
    EvidenceItem 序列化向后兼容；schema_version v1/v2 双形状读取；承载 §6.1 验收清单
    —— D9 隐私路径 / D4 身份负例 / D5 悬空引用 / D7 枚举闭合 / D8 字段对照）
        │
        ▼
C  CrossModalLink（同访客、时间窗重叠的视觉/音频 episode 关联；轻量索引）
        │
        ▼
D  Consumer Audio-aware（Retrieval 模态过滤 + Aggregation 音频模式标签
    + ReasoningInput 音频证据；守 C1/C2）
```

- **A 是地基**（依赖 D2）：不先统一证据，后续全悬空。
- **B 是核心**：让音频真正以可区分形态落 Memory。
- **E 在 C/D 之前**：先锁死 "Audio → Memory" 契约稳定（含 §6.1 验收清单），再发展图谱能力，降低回归面。
- **C/D 是高级能力**：跨模态关联与消费增强，依赖前序稳定。

---

## 6.1 验收清单（Acceptance Criteria，评审反馈 #5）

实施切片 E 的契约 / 隐私测试必须覆盖以下用例（全部纳入回放 baseline 与 CI）：

**D9 隐私路径（最敏感）**
- **24h 边界**：`SHORT` 原片在 `captured_at + 24h` 后被删除（含 ±jitter 容差）；
- **30d 边界**：`MEDIUM` 特征在 30d 后清理；
- **LONG 不自动清理**：语义模式标签永久保留，除非显式用户擦除请求；
- **删除失败重试**：`DELETE_FAILED` 后按退避重试，`retry_count` 递增；
- **幂等清理**：重复执行不报错、不重复计次；
- **文件已不存在**：直接置 `EXPIRED`，不报错（不依赖 uri 猜测）；
- **用户显式擦除**：触发删除并置终态；`EvidenceItem` 事实不动；
- **原始音频不超期滞留**：`DELETE_FAILED` 必留 `last_error`/`retry_count`/`last_alert_at`，可审计。

**D4 身份负例**
- **纯音频缺少两个身份**：`visitor_instance_id is None` 且 `audio_session_id is None` → 拒绝写入（I4 溯源链必填其一）；
- **禁止反填 visitor**：音频-only episode 不得用视觉 `visitor_instance_id` 反填，制造虚假身份归属。

**D5 悬空引用**
- **引用不存在 episode**：`CrossModalLink.episode_ids` 含未知 `record_id` → 拒绝或隔离（不静默落库）；
- **引用不存在 evidence**：`supporting_evidence_ids` 含未知 `evidence_id` → 拒绝或隔离。

**D7 枚举闭合 / D8 字段对照**
- `EvidenceModality` 值集恰为 `{VISION, AUDIO, IDENTITY}`，与 `SourceModality` 无交叉 import / 无共享值；
- v1 旧 `IDENTITY` 证据读为 `IDENTITY` 不变（向后兼容）；
- `from_dict` 同时接受 v1/v2 两种形状；`EPISODIC_RECORD_DICT_KEYS` 各版本闭合且显式（字段名统一为 `record_id` / `evidence_refs`，无 `episode_id` / `evidence_items` 分叉）；
- 旧数据 `confidence` 缺失 → `None`（不伪造 1.0）；旧 `modalities` 缺失 → `[]`（不引入 `UNKNOWN`）。

---

## 7. 修订记录（Changelog）

- **2026-08-06** 初稿（Proposed）。本 ADR 整合 Owner 评审修正：
  - D1：`source_modality: str` → `modalities: list[EvidenceModality]`（复合模态表达）；
  - D4：纯音频用 `AudioSessionId`，可选单向绑定 visitor，**取消强绑**；
  - 新增 **D8 Schema Evolution**：新字段 optional、旧 episode 默认 `[]`（空列表，不引入 UNKNOWN 枚举）、证据读取端 coerce（`confidence=None` 绝不 1.0）、`record_id` 不变、`schema_version` v1/v2 双形状兼容；
  - 实施顺序调整为 **A → B → E → C → D**（契约测试前置，先稳 Audio→Memory 再做图谱）。
- 方向性已确认：D3（不新增写入链）、D2（统一 EvidenceItem）、D5（CrossModalLink 关系非实体）、D6（Consumer 仅标签不分数）维持不变。
- **2026-08-06（修订·评审反馈 #2）**：
  - **全篇反诈语境修正**：移除「防跌倒/跌倒」示例，改用项目真实域「银发反诈」——复合诈骗 episode = `VISION(紧张翻找银行卡)` + `AUDIO(telephone_persistent 长时间通话 + distress_cry 哭腔)` + `VISION(kind=pose_panic_avoidance 恐慌回避)`，解释性推理示例改为"为何判断为诈骗风险"。
  - **新增 D9 Audio Evidence 生命周期 / 留存策略**：分层留存 `SHORT`(原始片段 24h) / `MEDIUM`(特征摘要 30d) / `LONG`(语义标签 永久)，落地 ADR-0002 隐私铁律；`EvidenceItem` 增 `retention_tier` / `expires_at`（D2 同处定义 `RetentionTier` 枚举）；失效状态记入可变 `EvidenceAssetState`，留存执行器只删本地原片、不删 episode/EvidenceItem、不改 `record_id`。
  - **建议2（CrossModalLink 证据级关联）**：`CrossModalLink` 增可选 `supporting_evidence_ids`，与 `episode_ids` 平行支持证据对粒度；v1 可留空，实现期启用，不阻塞冻结。
- **2026-08-06（修订·评审反馈 #3，五处接口/隐私/契约问题）**：
  - **#1 接口契约（D2）**：`evidence_refs` 改为 **ID 引用**（`list[str]`，evidence_id），`EvidenceItem` 独立存储按 ID 解析——不再内嵌，修复与 ADR-0024:638/663-665 冲突；旧数据 `confidence` 缺失 → `None`（绝不 1.0）。
  - **#2 枚举契约（新增 D7）**：`EvidenceModality` 完全继承 ADR-0022 `{VISION, AUDIO, IDENTITY}`——保留 `IDENTITY`、不增 `SENSOR`（属 SourceModality/ADR-0021）、`POSE` 归 `VISION`（`kind=pose_*`）、不引入 `UNKNOWN`；补枚举闭合契约测试与旧 IDENTITY 兼容规则。
  - **#3 隐私/悬空 URI（D9）**：新增可变 `EvidenceAssetState`（ACTIVE/EXPIRED/DELETE_FAILED + 重试/告警/用户擦除），episode/EvidenceItem 永不变更失效状态；幂等删除、失败重试+升级告警、文件不存在即 EXPIRED、本地 UTC 计时，消除悬空 URI 与不可审计删除。
  - **#4 Schema 自相矛盾（D8）**：统一字段名 `record_id`（非 episode_id）/`evidence_refs`（非 evidence_items）；补 v1/v2 JSON 示例 + 字段对照表 + `EPISODIC_RECORD_DICT_KEYS` 各版本闭合集合；旧 `modalities` 缺失 → `[]`。
  - **#5 测试覆盖（§6.1）**：新增验收清单，覆盖 D9 隐私路径（24h/30d/LONG 不删/失败重试/幂等/文件已不存在/用户擦除）、D4 身份负例（双身份缺失拒绝/禁止反填）、D5 悬空引用（未知 episode/evidence 拒绝或隔离）、D7 枚举闭合、D8 字段对照。
  - **文档索引**：README ADR-0027 摘要补齐 D3/D7/D9，与决策要点一致。
