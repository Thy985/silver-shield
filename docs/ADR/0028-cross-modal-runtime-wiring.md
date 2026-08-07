# ADR-0028: 跨模态运行时接线（Cross-Modal Runtime Wiring）

- **Status**: Proposed（review-ready，待 Owner 冻结）
- **Date**: 2026-08-06
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0027（音频记忆集成·D5 CrossModalLink 模型 / D4 AudioSessionId / Slice C 落地）
  - ADR-0026（音频感知链路·§6 CrossModalEvidence / §10 开放项·跨模态关联权重）
  - ADR-0024（Memory 架构·三类记忆模型 / MemoryPolicy / EpisodicRecord）
  - ADR-0019（多模态证据融合：Vision/Audio 双独立感知链 + Evidence Fusion 阶段）
  - ADR-0023（身份连续性：track_id / visitor_instance_id / person_identity_id 三层）
  - ADR-0014（事件 Schema 冻结）/ ADR-0001（仅产事实不裁决）/ ADR-0002（隐私铁律）
- **Phase**: v2 · Phase 3（音频双通道）→ Memory 闭环 → **跨模态 Memory Graph**

---

## 0. 背景与动机（Context）

**系统第一次需要具备「视觉看到的事件」与「声音感知的事件」在 Memory 中形成关联的能力——「第一次」指 Memory Graph 的运行时建边机制，属当前实现版本（v1）的能力边界：v1 仅「同设备 + 时间窗重叠」，不包含任何跨模态身份归并（§0.2）。**

承接 ADR-0019 的「Vision/Audio 双独立感知链」——本 ADR 在 **Memory 层**把双链产物（`EpisodicRecord`）进行**时间维度的合证关联**，**不做证据层融合**（特征/证据融合归 ADR-0026 §6 `CrossModalEvidence`）。二者是不同层次：融合层产出"哪条证据支撑哪条证据"的细粒度结论，本 ADR 只建 episode 级的时空边索引。

前序工作已分别就位，但**互不接线**：

1. **静态能力已落地（ADR-0027 Slice C，PR #145）**：`CrossModalLink`（Node—Edge—Node 关联边）、`CrossModalLinker`（确定性关联器）、`CrossModalLinkStore`（边索引 + 悬空引用校验）——但**没有任何运行时机制触发它**，linker 只存在于单元测试里。
2. **音频→Memory 已打通（PR #148）**：`AudioSessionRecorder` 把音频会话落库为纯音频 `EpisodicRecord`（`visitor_instance_id=None`，持 `audio_session_id`）——与视觉 `EpisodicRecord`（持 `visitor_instance_id`）**共享存储，但彼此孤立**。

结果是：即便同一时刻、同一设备发生了「视觉：老人跌倒」+「音频：撞击声」，Memory 里也是两条互不相干的 episode——**跨模态合证价值（诈骗/跌倒风险的解释性推理）完全丢失**。

```
现状（孤岛）：
  EpisodicRecord A (VISION: person falls 18:30:01-20)
  EpisodicRecord B (AUDIO: impact 18:30:02-15)
  → 无任何关联，Agent 无法回答"跌倒 + 撞击声是否同一事件"

目标（本 ADR）：
  CrossModalLink: A SUPPORTS B, overlap≈2s, confidence≈0.9
  → Memory Graph 出现第一条跨模态边
```

### 0.1 为什么现在做（价值排序）

- 跨模态关联是「Memory 反哺理解」的关键一环（ADR-0024 I4 可解释性）：没有边，视觉与音频是两张平行的孤岛表；
- `CrossModalLinker`/`CrossModalLinkStore` 已冻结（Slice C），只差**触发时机**与**第一版关联规则**——接线成本低、收益立现；
- 音频闭环（PR #148）落库后，Memory 里同时存在两类 episode，是建边的天然时机。

### 0.2 本 ADR 的边界（明确不做）

**第一版只做设备级 + 时间级关联**，刻意不做任何身份/语义判定（后续增量，见 §5 开放项）：

```
✅ 做：same_device（同设备）+ time_overlap（时间窗重叠）
❌ 不做：人脸 / ReID / 身份归并（哪个音频属于哪个访客）
❌ 不做：语义推理（"撞击声 + 跌倒 = 老人受伤"这类结论）
❌ 不做：证据级关联（supporting_evidence_ids，Slice C v1 已留空）
❌ 不做：跨设备关联（入户门摄像头 + 客厅麦克风分属不同设备）
```

**范围铁律（review 修订）**：本 ADR 只做 **Memory Graph 运行时**——输入 `EpisodicRecord`，输出 `CrossModalLink`。**程序化视频 / 合成视频生成（Remotion / OpenCV / Synthetic Video Generator）明确不属于本 ADR**——那属于 **Perception Validation Infrastructure**（`Camera → YOLO → Tracker → Event` 层），与 Memory Graph 是两个不同问题。若一并纳入会导致 scope 爆炸（CrossModal Runtime Wiring + Synthetic Vision Generator + YOLO Validation + Tracker Validation），破坏工程节奏。D6 的 fixture 是**声明式 episode 数据**（直接构造 `EpisodicRecord`），不是视频生成。

---

## 1. 决策（Decision）

在 `MemoryHook` 落库点接入一个**可选注入**的 `CrossModalLinkRuntime`：每次 `EpisodicRecord` 成功写入 `MemoryStore` 后，运行时扫描全部 episode，对「同设备 + 时间窗重叠」或「共享身份键」的跨模态 pair 产出 `CrossModalLink` 写入 `CrossModalLinkStore`。

```
MemoryHook.record（视觉/音频两路共用落库点）
    │  upsert_episodic(record) 成功
    ▼
CrossModalLinkRuntime.on_episode_recorded(record)     ← 新增（可选注入，None 零行为变化）
    │  store.all_episodic() 全量扫描
    ▼
CrossModalLinker.link(episodes)                        ← 既有（Slice C），新增 same_device 同源判定
    ▼
CrossModalLinkStore.add(link, known_episode_ids)       ← 既有（Slice C，悬空引用校验）
    ▼
Memory Graph 出现跨模态边
```

---

## 2. 决策要点（D1–D6）

### D1：`EpisodicRecord` 增加可选 `device_id`（same_device 的载体）

**问题**：第一版规则要 `same_device`，但 `EpisodicRecord` **没有 device 字段**（grep 零匹配；`VisitorEvent.source_video` 是来源视频元数据，语义不同）。

**决策**：`EpisodicRecord` 新增可选字段 `device_id: str | None = None`（**MINOR schema 演进，D8 风格向后兼容**）：

- **视觉路径（review 修订）**：`device_id` 从 **`WarningEvent.device_id`** 拉取——`signal_adapter` 已把 `device_id` 写入 `PerceptionEvent.device_id`（`signal_adapter.py:77-81`）→ `DecisionPolicy` 透传至 `WarningEvent.device_id`（`warning.py:124`）→ `MemoryHook.record(device_id=...)` → `project_episode` 透传 → `EpisodicRecord.device_id`。**不采用 `ev.source_video`**——它当前承载场景名（如 `CAVIAR/OneStopEnter1cor`，`pipeline.py:224/:420`），不是部署源标识，直接采用会导致"同一 episode 的 device_id 跟着 demo 场景漂移"，与 ADR-0021 语义不一致。
- **音频路径**：`AudioSessionRecorder` 已有 `device_id`（`home_entry_01`）→ `MemoryHook.record(device_id=...)` 透传。
- **v1 兼容**：旧记录 `device_id=None`；`from_dict` 缺省 None；`EPISODIC_RECORD_DICT_KEYS` 增 `"device_id"`（契约测试同步）。`same_device` 判定对 `None` 不成立（不关联）→ 渐进可用，存量数据零破坏。
- **v1 序列化形状（review 确认）**：`to_dict` 统一发全键（`device_id` 字段**存在但为 None**，与 ADR-0027 §D8「旧事件 modalities=[]/audio_session_id=null」同风格），v2 schema 演进在 ADR-0027 §D8 字段对照表补一行——**禁止**把 `device_id` 写成非 None 默认值（会悄悄破坏旧回放基线）。
- **隐私**：`device_id` 是安装标识（如 `home_entry_01`），非个人身份；与 ADR-0002 兼容（不进 Reason 判定，仅参与关联索引）。
- **关联边不携带 device_id（review 确认）**：`CrossModalLink` **不新增** `device_id` 字段——边仅含 `episode_ids`（`cross_modal_link.py:91-97` 现状），`device_id` 只存在于 episode 侧。即使未来 `MemoryConsumer` 把 link 引入 Reasoning Input，也不会经 link 字段泄漏 device_id；此约束写入文档以防未来 link schema 扩展时误加。**已存在的对抗点**：MemoryConsumer 读 episode 时（`retrieval.py` 注释已声明 device_id 不进 ReasoningInput，ADR-0025 §3.1 隐私边界）——link 的引入不得绕过该边界（§5 开放项登记「link 进 ReasoningInput 时的 device_id 拦截」）。

**命名与语义（review 修订）**：v1 保留字段名 `device_id`——与既有术语一致（`PerceptionPipeline.device_id`、`AudioSessionRecorder.device_id`、pipeline 装配的 `device_id="home_entry_01"`），避免引入第三套命名。**字段语义明确为「部署源标识（deployment source）」，如 `home_entry_01` / `living_room_mic_01`，不是硬件 UUID**。已知局限（review 确认）：未来一个 episode 可能来自**多个来源**（`fall` 事件 = camera01 + mic02 + pose_model 融合），届时 `device_id: str` 演进为 `source_ids: list[str]`（或 `origin_device_ids`）——**本 ADR 不抢答**，v1 单值 + 文档明示演进路径即可。

### D2：`CrossModalLinker` 同源判定——`candidate_context`（身份键 或 设备键；**review 修订：audio_session_id 不参与跨模态身份**）

**问题**：现有 `_same_subject` 认「共享 `visitor_instance_id` **或** 共享 `audio_session_id`」——两处问题：

1. **纯视觉 episode（仅 visitor_id）+ 纯音频 episode（仅 audio_session_id）无共享键 → 永不关联**（本 ADR 要补的核心场景）；
2. **（review 修订）`audio_session_id` 不应作为跨模态关联依据**：它是**音频会话身份**（`AudioSession001: 18:00–18:30 客厅声音` 是音频管道的**时间窗标识**），**不是世界实体身份**。同会话可能覆盖多个独立事件（18:20 门口访客恰好落在会话窗内），用会话 id 关联会把「同一音频窗的不同事件」误判为同一上下文；且用会话 id 做跨模态身份会**削弱 D4 匿名设计**（音频 episode 的身份本应只由 `audio_session_id` 承载，绝不外溢为跨实体判定键）。`audio_session_id` 保留在音频域内（音频 episode 自身聚合 / I4 溯源），**不用于 CrossModal identity**。

**决策**：同源判定命名为 `candidate_context`（候选上下文），两路并列：

```
candidate_context(a, b) = 共享 visitor_instance_id         （视觉身份，均非 None 且相等）
                       OR (a.device_id == b.device_id        （新增：同设备，均非 None）
                           AND a.device_id is not None)
```

- **`audio_session_id` 从身份判定中移除**——**取代 Slice C `_same_subject` 的 audio_session 分支**（`CrossModalLinker._same_subject` 重写为 `candidate_context`，Slice C 测试与 `memory_baseline_cross_modal.json` 中「复合 VISION+AUDIO 与纯音频共享 audio_session_id → SUPPORTS」用例**同步更新**：该场景若不再建边，改为依赖 `device_id` 键）；
- 关系语义不变：modalities 集合不同 → `SUPPORTS`（跨模态支撑）；相同 → `CO_OCCURS`；
- **时间窗重叠是硬 gate**（无论哪路同源判定，都必须 `min(leave) > max(enter)` 才建边）——`device_id` 只提供「同源候选」，不豁免时间 gate。
- **命名边界（review 确认）**：`candidate_context` 是 **linker 内部方法名 / 模块私有符号**，**不写入 `EpisodicRecord` 字段**（避免与潜在的同名字段需求冲突）；若未来需要把"候选上下文"暴露为字段，命名归 §5 开放项。

**受影响文件清单（review 确认，Slice A 强制子任务，遗漏任一则 CI 回归）**：

```
src/home_perception/memory/cross_modal_link.py   _same_subject → candidate_context 重写（移除 audio_session 分支）
tests/memory/test_cross_modal_link.py            4 条 audio_session 关联用例重写/删除
  （含 test_shared_audio_session_cross_modal_supports :303-322、
     test_pure_audio_links_via_audio_session_not_visitor :335-341）
tests/memory/test_memory_replay_cross_modal.py   整套基线用例更新（audio_session 键 → device_id 键）
tests/fixtures/memory_baseline_cross_modal.json  重生成（当前 1 条 SUPPORTS 边 ep-ev-cmp-xcm ↔ ep-xcm，
                                                  time_overlap 18:38–18:45，依赖 audio_session 键）
```

### D3：时间重叠阈值 `min_overlap_seconds`（可配，默认 0）

```
if overlap_seconds > min_overlap_seconds:  create_link()
```

- `CrossModalLinker.__init__` 新增 `min_overlap_seconds: float = 0.0`（默认与 Slice C 行为一致：严格重叠即关联）；
- 用户场景示例：vision 10–20s + audio 12–15s → overlap 3s；阈值 2s 时 `3 > 2` → 建边；
- 灰度期可收紧（如 `>= 2s` 过滤瞬时重叠噪音），阈值是**纯关联过滤**，不是风险分（C1 语义不变）。

### D4：运行时接线点——`CrossModalLinkRuntime`（可选注入，零行为变化）

**决策**：新增 `memory/cross_modal_runtime.py`：

```python
class CrossModalLinkRuntime:
    """跨模态关联运行时：episode 落库后自动扫描建边（ADR-0028 D4）。

    只读 EpisodicRecord（C2），只写 CrossModalLinkStore（边索引，非 MemoryRecord）；
    失败隔离：建边异常只记日志，绝不阻断落库主链路（AGENTS.md §2.5）。
    """

    def __init__(self, store: MemoryStore, link_store: CrossModalLinkStore,
                 linker: CrossModalLinker | None = None,
                 *, min_overlap_seconds: float = 0.0, enabled: bool = True):
        ...

    def on_episode_recorded(self, record: EpisodicRecord) -> list[CrossModalLink]:
        """落库后触发：全量扫描 → linker → link_store.add（悬空校验）。
        返回本次新写入的边（空列表 = 无关联）。"""
```

- **触发位置**：`MemoryHook.record` 落库成功后调用（`memory_hook` 构造时可选注入 `cross_modal_runtime`；缺省 None → 不触发，**零行为变化**——与 `MemoryConsumerHook`/`MemoryHook` 的既有可选注入模式一致）；
- 视觉与音频两路共用 `MemoryHook.record` → 自动覆盖两路（音频 episode 落库即与既有视觉 episode 建边，反之亦然）；
- **失败隔离**：`CrossModalLinkRuntime` 内部异常 → 记日志 + 返回空列表，绝不向上抛（不影响 episode 落库）；
- **metrics 边界（review 确认）**：`CrossModalLinkRuntime` 失败**仅日志告警，不计入 `metrics.errors`**——`errors` 计数属于 Memory 落库通道的契约（`MemoryHook.record` 内「投影/落库异常 → errors+=1」），建边是旁路增量，计入会污染落库成功率口径、破坏 `MemoryHook` 的 0 行为变化语义。

### D5：`MemoryStore.all_episodic()` 全量取数 + v1 全量扫描策略

**问题**：linker 需要扫描「全部 episode」找跨模态 pair，但 `MemoryStore` 只有 `get_episodic_by_visitor`（按访客，纯音频 episode 的 visitor 为 None 取不到）与 `snapshot()`（dict 视图）。

**决策**：

- `MemoryStore` **抽象接口新增抽象方法** `all_episodic() -> list[EpisodicRecord]`（review 确认：现抽象类 `store.py:30-55` 无该方法，**必须**作为抽象方法列入 Slice A 强制子任务；`InMemoryStore` 实现为 `list(self._episodic.values())`）；**同步更新** `RuleBasedRetrieval` 注释（`retrieval.py:35-50` 现写「v1 EpisodicRecord 无 device_id 字段」，D1 落地后该注释与现实漂移）；
- **v1 全量扫描**：每次落库后 `linker.link(store.all_episodic())`——episode 数量级（百级）下 O(n²) 可接受；增量索引（按 device/时间桶）留待 §5 开放项；
- 幂等：`link_id` 确定性 → `CrossModalLinkStore.add` 幂等 upsert（同内容返回 False，不重复建边）；
- **迁移期占位（review 确认）**：未来 `MemoryStore` 扩展 SQLite/远程后端时（AGENTS.md §0「v1 持久化不对称」），`all_episodic()` 实现必须提供 **LIMIT + 游标化扫描**占位接口（避免「一次落库 = 一次全表扫描」的 N+1 风险）——本 ADR 仅登记契约，实现归 Memory 持久化课题。

**Performance Boundary（review 修订，写死防遗忘）**：

```
episode 数 < 10_000  →  O(n²) 全量扫描可接受（v1 现状，无需优化）
episode 数 ≥ 10_000  →  必须迁移时间桶 / device 分桶索引（阻断性要求，非可选优化）
```

- 阈值 10_000 是**硬边界**：超过即触发索引迁移设计（时间桶 + device 桶，把扫描范围从全量收敛到邻域），不允许「先顶着 O(n²) 再观察」；
- v1 阶段（百级 episode）O(n²) 是刻意取舍——`linker` 纯内存计算，单次扫描毫秒级，收益（Memory Graph）远大于成本；
- **监测契约（review 确认）**：`MemoryStore.all_episodic()` 在 episode ≥ 10_000 时返回 `MemoryScaleWarning`（或抛 `MemoryScaleError`），由 `CrossModalLinkRuntime` 决定**降级跳过本轮建边**或**触发索引迁移回调**；具体机制（告警载体 / 迁移回调签名）归 §5 开放项——本 ADR 只定「阈值 + 必须响应」的契约。

### D6：最小 Synthetic Episode Fixture（声明式场景 → 直接验证 Memory Graph）

**问题**：真实音视频对齐测试成本高（需同步时间戳），且「先验证 Memory Graph」不需要真实媒体。

**决策**：声明式 scenario fixture（YAML，`tests/fixtures/cross_modal_scenarios.yaml`）：

```yaml
scenarios:
  - name: vision_fall_audio_impact
    device_id: home_entry_01
    vision:
      - start: 10.0        # 相对时刻（秒），下同
        end: 20.0
        event: fall
        visitor: visitor_a
    audio:
      - start: 12.0
        end: 15.0
        event: impact
    expected:
      link: true
      relationship: supports        # 跨模态（VISION vs AUDIO）→ SUPPORTS
      min_overlap_seconds: 2.0
```

- **翻译规则**（测试工具，确定性无随机）：`vision` 条目 → 带 `visitor_instance_id` 的视觉 `EpisodicRecord`（`modalities=[VISION]`）；`audio` 条目 → 纯音频 `EpisodicRecord`（`visitor=None`、`audio_session_id` 由 `event` 派生、`modalities=[AUDIO]`）；两者均带 `device_id`；
- **二进制边界（review 确认，与 §0.2 闭环）**：fixture **不生成 / 不消费任何视频或音频二进制**（不调用 Remotion / OpenCV / 合成音频生成），仅构造领域对象（`EpisodicRecord`）——与 §0.2「程序化视频明确排除」形成闭环；
- **断言**：`expected.link: true` → `CrossModalLinkStore` 出现恰好 1 条边，`relationship == supports`，`time_overlap` 与期望重叠一致；
- **负例必配**（测试有效性铁律）：`link: false` 场景——每个「建边」断言都有「不建边」对照；
- **验证对象**：`Episode → CrossModalLinker → CrossModalLink`（用户指定的直接链路），不依赖真实媒体，纯 Memory Graph 验证。

**fixture 必含场景清单（review 确认，≥ 1 正例 + 3 负例，写入 Slice B 验收）**：

```yaml
scenarios:
  # 正例：同设备 + 时间重叠（含 min_overlap_seconds 阈值判定）
  - name: vision_fall_audio_impact
    device_id: home_entry_01
    vision: [{start: 10, end: 20, event: fall, visitor: visitor_a}]
    audio: [{start: 12, end: 15, event: impact}]
    expected: {link: true, relationship: supports, min_overlap_seconds: 2.0}

  # 负例 1：同设备但时间不重叠
  - name: same_device_no_overlap
    device_id: home_entry_01
    vision: [{start: 10, end: 12, event: visit, visitor: visitor_a}]
    audio: [{start: 40, end: 45, event: impact}]
    expected: {link: false}

  # 负例 2：时间重叠但异设备
  - name: overlap_different_device
    device_id: home_entry_01          # vision 用
    vision: [{start: 10, end: 20, event: fall, visitor: visitor_a}]
    audio_device_id: living_room_mic_01  # audio 用不同 device
    audio: [{start: 12, end: 15, event: impact}]
    expected: {link: false}

  # 负例 3：时间重叠但未达 min_overlap_seconds 阈值
  - name: overlap_below_threshold
    device_id: home_entry_01
    vision: [{start: 10, end: 11, event: visit, visitor: visitor_a}]
    audio: [{start: 11, end: 12, event: impact}]     # overlap=1s < 2s
    expected: {link: false, min_overlap_seconds: 2.0}

  # 负例 4（D2 review 核心安全网）：共享 audio_session_id 但异设备 → 不建边
  - name: vision_audio_session_overlap_different_device
    device_id: home_entry_01
    vision: [{start: 10, end: 20, event: fall, visitor: visitor_a}]
    audio_device_id: living_room_mic_01
    audio: [{start: 12, end: 15, event: impact, audio_session: shared_session_001}]
    expected: {link: false}   # 会话 id 相同也不建边（audio_session_id 不参与跨模态身份）
```

---

## 3. 动机（Rationale）

1. **先验证图，再谈语义**：跨模态关联的终极价值是解释性推理（"跌倒 + 撞击声"→ 风险），但第一步必须是**可靠地建边**。设备 + 时间是最低成本、零身份假设的可靠信号——不依赖任何模型，确定性可复现。
2. **接线成本极低**：linker/store 已冻结（Slice C），本 ADR 只新增「触发时机 + device 载体 + 一条同源判定」，全部可选注入，存量行为零变化。
3. **两路落库点天然收敛**：`MemoryHook.record` 是视觉/音频共用落库点（PR #148 后音频也走它），在此触发 linker 自动覆盖两路，无需调用方分别接线。
4. **渐进演化路径清晰**：设备级（本 ADR）→ 身份级（共享 audio_session/visitor，Slice C 已有）→ 语义级（融合 ADR，ADR-0026 §10）——三层能力可叠加，不互相推翻。

---

## 4. 后果（Consequences）

### 正面

- Memory 首次具备跨模态关联能力：Agent 可回答"这两个感知事件是否同一上下文"；
- 为 ADR-0026 §6 `CrossModalEvidence` 的 `overlap_with_visitor` 提供运行时事实基础；
- 全部组件可选注入，默认关闭，历史行为逐字段不变。

### 负面 / 代价

- `EpisodicRecord` schema +1 可选字段（`device_id`），`EPISODIC_RECORD_DICT_KEYS` 契约测试更新；
- 全量扫描 O(n²)（v1 可接受，episode 量级百级；增量索引归开放项）；
- 设备级关联有**误关联风险**（同设备不同人/不同事件的跨模态 pair 会被建边）——这是刻意的 v1 取舍：宁可多边（supports 弱证据）也不漏掉合证线索，语义裁决留给下游（Reasoning / 决策）。

### 必须承担的技术债 / 后续动作

- `CrossModalLinkStore` 持久化（v1 内存，重启即空）——与 `InMemoryStore` 同生命周期，Memory 持久化课题一并解决；
- 增量索引 / 时间桶（避免全量扫描）；
- 证据级关联（`supporting_evidence_ids` 填充，Slice C v1 留空）。

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **身份归并权重**（何时"同设备 + 重叠"升级为"同一访客"）：归 `CrossModalEvidence.overlap_with_visitor`（ADR-0026 §10 开放项）；
- **误关联抑制**：阈值自适应（如短 overlap 高置信惩罚）——需真实数据观测后定；
- **跨设备关联**（入户门摄像头 + 客厅麦克风）：需要设备拓扑知识，超出设备级 v1；
- **`audio_session_id` 的正确归属（review 修订确认）**：只用于音频域内部——音频 episode 自身聚合（一段会话内的多个音频事件合并）与 I4 溯源（`source_event_ids`）；**不作为跨模态身份键**（D2 已移除）。若未来需要「复合 episode（VISION+AUDIO）与纯音频 episode 引用同一会话」的关联，走 `device_id` + 时间 gate（本 ADR 机制），或由融合层（ADR-0026 §6 `CrossModalEvidence`）显式产出——不恢复会话 id 作为隐式身份键；
- **`device_id` 多源演进**：未来 `fall` 事件可能来自 camera01 + mic02 + pose_model 融合（多来源单 episode）——`device_id: str` 演进为 `source_ids: list[str]`，本 ADR v1 不抢答（D1 命名说明）；
- **`device_id` 透传点收敛（review 确认）**：视觉从 `WarningEvent.device_id` 拉取、音频从 `AudioSessionRecorder.device_id` 拉取，**不经由 `source_video`**（D1 已决策）；若未来 `WarningEvent.device_id` 语义变化（如多设备融合），透传点需重新收敛，归本开放项；
- **link 进 ReasoningInput 的 device_id 拦截（review 确认）**：`CrossModalLink` 自身不存 `device_id`（D1 约束），但 `MemoryConsumer` 未来若把 link 引入 ReasoningInput（跨模态合证是 Agent 解释性推理核心），episode 侧 `device_id` 不得经 link 链路泄入 Reason——需在 Consumer 层显式剥离（ADR-0025 §3.1 隐私边界），机制归开放项。
- **关系词汇语义收紧（`SUPPORTS` 偏强 + 关联发现 vs 语义支持判断，ADR-0029 审查 follow-up）**：当前 `CrossModalRelationship` 仅 `CO_OCCURS` / `SUPPORTS`，且 `SUPPORTS` 由“modalities 集合不同”机械判定（D2 决策），**本质是“关联发现（association discovery）”——只回答“两事件是否同上下文 + 时间重叠”，不回答“是否相互支持”**。审查指出 `SUPPORTS` 词面偏强（暗示“音频支撑视觉”的语义/因果支持），建议后续：① 收紧 `SUPPORTS` 生成条件（仅当跨模态合证满足更严阈值 / 无矛盾信号）；② 引入中间关系 `TEMPORALLY_ALIGNED`（“时间对齐但语义中立”）作为更弱的默认跨模态边；③ 在 `CrossModalLinker` 文档与注释中明确：**Link Runtime = 关联发现，不是语义支持判断**；语义裁决留给 Reasoning / Decision（ADR-0010）。此收紧**不改动 v1 已冻结实现行为**（baseline 契约锁定），仅约束后续 Link Runtime 演进，正面防止“Decision 逻辑提前泄漏进 Link”（ADR-0029 审查要点：若 `SUPPORTS` 太松，Link 已偷偷完成一部分语义判断）；该后续动作由 ADR-0029 解释层直接透传 relationship（不重新解释），故与解释层解耦。

---

## 6. 实施切片（实施顺序，冻结后执行）

- **Slice A（本 ADR 核心）**：`EpisodicRecord.device_id`（D1，透传点=视觉 `WarningEvent.device_id` / 音频 `AudioSessionRecorder.device_id`）+ **`MemoryStore` 抽象类新增 `all_episodic()` 抽象方法**（D5）+ `CrossModalLinker.candidate_context` 重写（**移除 audio_session 分支，含受影响文件清单**，D2）+ `min_overlap_seconds`（D3）+ `CrossModalLinkRuntime`（D4）+ `MemoryHook` 可选注入 + **`RuleBasedRetrieval` 注释同步更新**（"v1 无 device_id"已过时）。
- **Slice B（验证）**：最小 Synthetic Episode Fixture（D6）——声明式 scenario（**≥ 1 正例 + 4 负例清单见 D6**）→ 直接验证 `Episode → CrossModalLinker → CrossModalLink`；**重写 Slice C 的 4 条 audio_session 关联用例**（`test_cross_modal_link.py`）并**按 `MEMORY_UPDATE_BASELINE=1` 约定重生成 `memory_baseline_cross_modal.json`**（当前 1 条 SUPPORTS 边改依赖 device_id 键；`test_memory_replay_cross_modal.py` 整套基线同步）。
- **Slice C（契约）**：`EPISODIC_RECORD_DICT_KEYS` + `device_id` 序列化契约测试（v1 形状：字段存在但 None）+ 回放基线更新（`memory_baseline_cross_modal.json` 增 device_id 字段）。

### 验收清单（Acceptance Criteria）

1. 视觉 episode 落库 → 与同设备时间重叠的音频 episode 自动建 `SUPPORTS` 边（D4/D2）；
2. 同设备但不重叠 / 异设备但重叠 / 阈值未达 → 不建边（D2/D3 负例，变异可检出；fixture 清单 D6）；
3. **共享 audio_session_id 但异设备 / 无 device 键 → 不建边**（D2 review 修订：会话 id 不参与跨模态身份；fixture `vision_audio_session_overlap_different_device` 固化）；
4. `device_id=None` 旧记录不参与 `same_device` 判定（D1 向后兼容）；
5. `MemoryHook` 未注入 runtime 时，落库行为与历史逐字段一致（D4 零行为变化）——**对应契约测试**：`tests/runtime/test_memory_hook.py::test_record_no_cross_modal_runtime_unchanged`（新增）；
6. 声明式 scenario fixture 全绿：`link: true/false` 断言与期望一致（D6）；
7. `CrossModalLinkStore.add` 悬空引用校验继续生效（Slice C 契约不回退）；
8. `all_episodic()` ≥ 10_000 时触发索引迁移告警（D5 Performance Boundary / MemoryScaleWarning 契约）；
9. **Slice C 既有用例全量回归**：`test_cross_modal_link.py`（4 条 audio_session 用例重写后）、`test_memory_replay_cross_modal.py`、`memory_baseline_cross_modal.json`（重生成）全绿——按 AGENTS.md §8「全量测试全绿」基线，不允许回归。

---

## 7. 修订记录（Changelog）

> **修订权属（review 确认，呼应 AGENTS.md §6.3 第 8 条「未授权改架构决策文件」）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-06**：初稿（Proposed）。D1–D6 决策要点基于 Slice C（#145）与音频闭环（#148）已落地实现；第一版规则收敛为「same_device + time_overlap」，明确不做身份/语义判定。
- **2026-08-06（review 修订）**：① D2 核心修正——`audio_session_id` **不再作为跨模态身份键**（会话身份≠世界实体身份，削弱 D4 匿名），同源判定收敛为 `candidate_context = visitor_instance_id OR device_id`，取代 Slice C `_same_subject` 的 audio_session 分支（基线测试同步更新）；② D5 增 **Performance Boundary**（episode < 10_000 O(n²) 可接受 / ≥ 10_000 必须迁移分桶索引，写死防遗忘）；③ D1 明确 `device_id`=部署源标识（非硬件 UUID）+ 未来 `source_ids` 多源演进路径；④ §0.2 范围铁律——**程序化视频/合成视频生成明确排除**（属 Perception Validation Infrastructure，非 Memory Graph），避免 scope 爆炸。
- **2026-08-06（review 修订 2）**：⑤ D1 视觉透传点修正——`device_id` 从 **`WarningEvent.device_id`** 拉取（不经 `source_video`，防 demo 场景漂移）；link 不携带 `device_id`；v1 序列化形状=字段存在但 None；⑥ D2 显式列出受影响文件清单（Slice A 强制子任务：`cross_modal_link.py` 重写 + `test_cross_modal_link.py` 4 用例 + `test_memory_replay_cross_modal.py` + 基线重生成）；`candidate_context` 命名边界（内部方法名，不写入字段）；⑦ D5 抽象方法确认 + `RuleBasedRetrieval` 注释同步 + `MemoryScaleWarning` 监测契约 + SQLite 迁移期 LIMIT/游标占位；⑧ D4 `metrics.errors` 边界（建边失败不计入）；⑨ D6 fixture 显式枚举 ≥1 正 + 4 负场景清单 + 二进制边界声明 + `vision_audio_session_overlap_different_device` 安全网场景；⑩ 验收清单扩至 9 条（含 Slice C 全量回归、`test_memory_hook.py` 零行为变化锚点）；⑪ §0 明确 v1 能力边界 + 承接 ADR-0019 双链（不做证据层融合）。
