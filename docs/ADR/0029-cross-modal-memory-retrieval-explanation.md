# ADR-0029: 跨模态记忆检索与解释（Cross-Modal Memory Retrieval & Explanation）

- **Status**: Proposed（review-ready，待 Owner 冻结）
- **Date**: 2026-08-07
- **Owner**: SilverShield 技术负责人
- **Related**:
  - ADR-0028（跨模态运行时接线·CrossModalLink/LinkStore/Runtime 已落地，PR #150/#151）
  - ADR-0027（音频记忆集成·D5 CrossModalLink 模型 / D6 音频感知消费）
  - ADR-0024（Memory 架构·三类记忆模型 / I4 可解释性 / I2 单调性）
  - ADR-0025（Memory Consumer 架构·C1 不决策 / C2 只读 / C3 确定性 / C5 溯源）
  - ADR-0010（单一决策中心）/ ADR-0001（仅产事实不裁决）/ ADR-0002（隐私铁律）
  - ADR-0028 开放项 #5（link 进 ReasoningInput 的 device_id 拦截）
- **Phase**: v2 · Phase 3（音频双通道）→ Memory 闭环 → 跨模态 Memory Graph → **跨模态可解释层**

---

## 0. 背景与动机（Context）

**ADR-0028 已让 Memory 第一次长出"跨模态边"——但边建好之后，谁来看、谁来读懂它？**

承接 ADR-0028：视觉 episode 与音频 episode 落库后，由 `CrossModalLinkRuntime` 自动扫描建边，产物是 `CrossModalLink`（Node—Edge—Node：episode A `SUPPORTS` episode B，带 `time_overlap` / `confidence` / `supporting_evidence_ids`）。`CrossModalLinkStore` 提供 `get_links_by_episode` / `all_links` 只读入口。

但当下存在**价值断层**：边在存储里，却**没有任何消费侧把它变成"可被理解的解释"**。Agent（或用户直接查询）仍然无法回答最朴素的问题——

> 「为什么 Memory 把'视觉：老人跌倒'和'音频：撞击声'标记为相关？它们之间的具体关系是什么？」

```
现状（有边无人读）：
  CrossModalLink: ep-fall SUPPORTS ep-impact
                  time_overlap=18:30:01–18:30:15, confidence=0.9
  → 仅存在于 link_store，无任何解释视图 → Agent 读不到、用户问不出

目标（本 ADR）：
  CrossModalContext {
    relationship: SUPPORTS（跨模态支撑）
    peer_episodes: [视觉「老人跌倒」18:30:01–18:30:20,
                    音频「撞击声」18:30:02–18:30:15]
    time_overlap: 18:30:02–18:30:15
    association_strength: 0.90
    explanation: "视觉事件「老人跌倒」与音频事件「撞击声」在时间窗重叠约 14s，
                  在同一部署源上下文相互支撑（SUPPORTS），关联强度 0.90。
                  两者互为合证参考，可作同一上下文理解。"
  }
  → Agent 可读、可解释、可溯源，但不下任何结论
```

### 0.1 为什么现在做（价值排序）

- **建边只是手段，解释才是价值**：ADR-0028 解决"Memory 能不能关联"，本 ADR 解决"关联能不能被理解"——后者才是 I4 可解释性（ADR-0024）对 Agent 的最终交付；
- **零新增判断成本**：解释层是**纯只读投影**（复用 ADR-0028 的 link 与 episode），不引入任何模型、不写 Memory、不改 Graph；
- **直接关闭 ADR-0028 开放项 #5**：`CrossModalLink` 不存 `device_id`（ADR-0028 D1），但 Agent 解释时需要知道"是否同上下文"——本 ADR 提供**红化的 `shared_deployment_context: bool`**（不暴露原始安装标识），从设计上杜绝 `device_id` 经 link 链路泄入 Reason（ADR-0025 §3.1 隐私边界）；
- **与 `MemoryQuery.compose_context` 互补而非重叠**：`MemoryQuery` 回答"单访客为何报警"（按 `visitor_instance_id` 投影），本 ADR 回答"这两个跨模态事件什么关系"（图级边投影）——两者是不同粒度的解释视图，共同构成 Memory 的可解释面。

### 0.2 本 ADR 的边界（明确不做——**核心纪律**）

**本 ADR 是"检索 + 解释"层，不是"判断"层。一句话铁律：解释只读事实、只描述关系、只给溯源，绝不产出结论。**

```
❌ 不做判断 / 结论 / 风险分：
   - 解释文本不得出现「这很可能是诈骗」「应当升级」之类结论句；
   - 关联强度（association_strength）明确是"关联可信度"不是"风险分"
     （复用 cross_modal_link.py:85「confidence：关联可信度 [0,1]，非风险分」语义）；
   - 任何"是否异常"的最终裁决仍归 ReasoningEngine / DecisionPolicy（ADR-0010 单一决策中心）。
❌ 不泄露 device_id：
   - CrossModalContext 不得含 device_id 字段；
   - 是否"同上下文"以红化 bool（shared_deployment_context）表达，绝不回显原始安装标识。
❌ 不写 Memory / 不修改 Graph：
   - 检索 + 解释全程只读（C2）；不调 store 写方法，不调 linker 重算。
❌ 不做语义融合 / 证据级关联：
   - supporting_evidence_ids（ADR-0027 Slice C v1 留空）本 ADR 不臆造、不消费；
   - 多跳图遍历（ep-A 链 ep-B 链 ep-C）v1 不做（1-hop 直接边）。
❌ 不接 LLM / 不实现 Agent：
   - 与 MemoryQuery 同纪律（query.py:7「不实现 Agent、不接 LLM，仅做结构化组合」）；
   - explanation 是数据派生的确定性模板文本，不是生成式文案。
```

---

## 1. 决策（Decision）

新增一个**纯只读**的跨模态可解释层 `memory/cross_modal_explainer.py`，包含三件套：

1. **`CrossModalRetrieval`**——图级只读检索：按 episode / visitor / device / time-window 四个轴从 `CrossModalLinkStore` + `MemoryStore` 取回相关 `CrossModalLink`（device_id 仅作 join key，绝不外泄）；
2. **`CrossModalExplainer` + 契约 `CrossModalContext` / `CrossModalEpisodeRef`**——把一条 `CrossModalLink`（连同其两端 episode）投影为**结构化、隐私安全、确定性**的解释视图（含自然语言 `explanation` 文本）；
3. **`ReasoningInput.cross_modal_contexts` 扩展（可选）**——`RuleBasedMemoryConsumer` 可选注入检索+解释组件，把当前访客相关的跨模态解释**作为描述性上下文**附给 `ReasoningInput`（默认空、零行为变化、不破坏 C1/C2）。

```
消费侧接线（可选注入，None 零行为变化）：

RuleBasedMemoryConsumer.consume(current_event)
    │ Retrieval → Aggregation → ContextBuilder（既有，不变）
    ▼
ReasoningInput（既有 8 字段）
    │ 若注入 cross_modal_retrieval + cross_modal_explainer：
    │   retrieval.get_links_for_visitor(current_event.visitor_instance_id)
    │     → explainer.explain(link) for each
    ▼
ReasoningInput(cross_modal_contexts=(ctx1, ctx2, ...))   ← 新增（默认空）
    │  ReasoningEngine.infer 读取（参考推理，非决策）
    ▼
ReasoningResult（既有；explanation 可引用 cross_modal_contexts）
```

---

## 2. 决策要点（D1–D5）

### D1：`CrossModalRetrieval`——图级只读检索（四轴）

**问题**：`CrossModalLinkStore` 现仅有 `get_links_by_episode(record_id)` / `all_links()`——没有"按访客 / 按设备 / 按时间窗"取边的视角；Agent 要理解"某访客相关的所有跨模态关系"还得自己 join。

**决策**：新增 `CrossModalRetrieval`（与 `MemoryQuery` 同层级的只读查询组件，不接 ABC——v1 单实现，避免与 `Retrieval` ABC 语义混淆；`Retrieval` ABC 是 episode 召回，本类是对 link 图的召回）：

```python
class CrossModalRetrieval:
    """跨模态图只读检索（ADR-0029 D1）。

    只读 CrossModalLinkStore + MemoryStore，绝不写 Memory / 不改 Graph（C2）；
    所有返回确定性排序（按 link_id 升序），同输入同输出（C3）。
    """

    def __init__(self, link_store: CrossModalLinkStore, memory_store: MemoryStore) -> None:
        ...

    def get_links_for_episode(self, record_id: str) -> list[CrossModalLink]:
        """某 episode 参与的全部边（委托 link_store.get_links_by_episode）。"""

    def get_links_for_visitor(self, visitor_instance_id: str) -> list[CrossModalLink]:
        """某访客相关的全部边：memory_store.get_episodic_by_visitor → episode_ids
        → 按 link_id 去重合并 link_store.get_links_by_episode 结果。"""

    def get_links_for_device(self, device_id: str) -> list[CrossModalLink]:
        """某部署源相关的全部边：memory_store.all_episodic() 过滤 device_id
        → episode_ids → 去重合并 links。device_id 仅作 join key，绝不出现在返回值。"""

    def get_links_in_window(self, start: datetime, end: datetime) -> list[CrossModalLink]:
        """时间窗内全部边：all_links 过滤 time_overlap 与窗口相交（重叠即命中）。"""
```

- **确定性（C3）**：四轴返回均按 `link_id` 升序，join 去重用 `set` + 排序，同输入两次结果一致；
- **device_id 不出现（D1 隐私）**：`get_links_for_device` 用 `device_id` 做 MemoryStore 过滤，但返回值只是 `CrossModalLink`（本身无 device_id 字段，ADR-0028 D1），`device_id` 字符串不进入任何输出契约；
- **复用既有基元**：`get_episodic_by_visitor` / `all_episodic()`（ADR-0028 D5 已落地）直接可用，无新存储 API。

### D2：`CrossModalExplainer` + `CrossModalContext` 契约（纯只读解释）

**问题**：`CrossModalLink` 是机器索引（link_id / episode_ids / relationship / time_overlap / confidence），人/Agent 读不懂"这到底是什么关系"。需要一层**投影**把它变成可解释结构——且必须**隐私安全**（无 device_id）、**非判断**（只描述）。

**决策**：新增 `CrossModalContext` / `CrossModalEpisodeRef` 两个 `frozen` 数据契约 + `CrossModalExplainer`：

```python
@dataclass(frozen=True)
class CrossModalEpisodeRef:
    """跨模态边的一端 episode（解释投影，隐私红化）。"""
    record_id: str
    modality: EvidenceModality            # VISION / AUDIO（已知安全字段）
    summary: str                          # EpisodicRecord.summary（已是 human-readable，安全）
    enter_time: datetime
    leave_time: datetime
    has_visitor_identity: bool            # visitor_instance_id 是否非 None（红化状态，绝不回显 id）
    # device_id 故意排除（ADR-0028 D1 + 本 ADR §0.2 隐私边界）

@dataclass(frozen=True)
class CrossModalContext:
    """一条跨模态边的可解释投影（ADR-0029 D2，纯描述、非判断、隐私安全）。"""
    link_id: str
    relationship: CrossModalRelationship          # SUPPORTS / CO_OCCURS（描述性枚举，非判定）
    peer_episodes: tuple[CrossModalEpisodeRef, ...]  # 两端 episode（确定性序）
    time_overlap: tuple[datetime, datetime] | None   # 重叠窗口；无重叠为 None
    association_strength: float                    # = link.confidence，语义重命名为"关联强度"（非风险分）
    shared_deployment_context: bool               # 两端 device_id 非 None 且相等 → True（红化，无原始 id）
    explanation: str                              # 确定性自然语言描述（数据派生，非生成式）
    # C5 溯源：
    source_link_id: str                           # = link_id
    source_episode_ids: tuple[str, ...]           # = link.episode_ids
```

```python
class CrossModalExplainer:
    """跨模态解释器（ADR-0029 D2，纯只读投影，C3 确定性纯函数）。"""

    def explain(self, link: CrossModalLink, memory_store: MemoryStore) -> CrossModalContext:
        """link → CrossModalContext：查两端 episode（按 link.episode_ids）→
        构造 CrossModalEpisodeRef → 推导 shared_deployment_context（红化） →
        生成确定性 explanation 文本。"""

    def explain_for_episode(self, record_id: str, *,
                            retrieval: CrossModalRetrieval,
                            memory_store: MemoryStore) -> list[CrossModalContext]:
        """便捷：取某 episode 相关 links → 逐个 explain → 确定性排序返回。"""
```

- **`shared_deployment_context` 红化（解决 ADR-0028 开放项 #5）**：解释器读两端 episode 的 `device_id`，**仅在两者均非 None 且相等时置 True**，绝不把原始 `device_id` 字符串写进 `CrossModalContext`——从数据结构上保证 `device_id` 不经 link 链路泄入 Reason（ADR-0025 §3.1）；
- **`association_strength` 语义重命名**：直接取 `link.confidence`，但契约字段名显式叫"关联强度"，与 `risk_score` / `decision` 在语义上划清（C1 友好）；值仍是 [0,1]，非风险分；
- **`relationship` 是描述性枚举**：`CrossModalRelationship`（SUPPORTS / CO_OCCURS）只陈述"关系类别"，不是"结论"——解释文本据此生成中性描述（D3）；
- **纯函数（C3）**：`explain` 不读墙钟、不随机、不写状态；同 `(link, memory_store 状态)` 两次产出逐字段一致（审计 / 回放一致）；
- **溯源（C5）**：`source_link_id` + `source_episode_ids` 让每条解释可追到具体 link 与 episode（与 `SourceRef` 思路一致，但不引入 `ReasoningResult` 依赖）。

### D3：关系词汇 → 解释映射（描述性，非判断）

**决策**：关系枚举到解释语言的**单向映射表**（确定性，无模型）：

| `relationship` | 中文描述 | explanation 句首锚定 |
|---|---|---|
| `SUPPORTS` | 跨模态支撑 | "视觉事件「…」与音频事件「…」在时间窗重叠约 Ns，在同一部署源上下文相互支撑（SUPPORTS）" |
| `CO_OCCURS` | 同主体合证 | "同一访客的两次事件在时间上相邻合证（CO_OCCURS）" |

- **句法铁律（§0.2）**：模板只陈述**事实**（什么事件、何时、重叠多久、强度多少、是否同上下文），**不得**出现"疑似 / 可能 / 应当 / 建议"等判断词；`explanation` 以句号闭合的**陈述句**收尾；
- **`association_strength` 显式标注"关联强度"**：文本含"关联强度 X.XX"，不复用"置信度/风险"措辞，避免与风险语义混淆；
- **多跳排除（v1）**：`explain` 只投影**直接边**（link 的两端 episode）；若某 episode 同时是另一条 link 的端点（图多跳），v1 不递归展开——多跳遍历归 §5 开放项。

**确定性生成示例**（输入 = ADR-0028 首条边）：

```
视觉事件「老人跌倒」（18:30:01–18:30:20）与音频事件「撞击声」（18:30:02–18:30:15）
在时间窗重叠约 14s，在同一部署源上下文相互支撑（SUPPORTS），关联强度 0.90。
两者互为合证参考，可作同一上下文理解。
```

（无 device_id 回显；无"疑似诈骗"之类结论；可溯源到 link_id + 两端 episode_id。）

### D4：`ReasoningInput.cross_modal_contexts` 扩展 + Consumer 可选注入

**问题**：解释层产出的 `CrossModalContext` 目前无处可去——Agent（`ReasoningEngine`）读不到。`ReasoningInput` 是 Consumer→Reasoning 的唯一契约载体。

**决策**：

1. **契约扩展（最小、兼容）**：`ReasoningInput` 新增可选字段（默认值空，不影响既有 8 字段语义）：

   ```python
   @dataclass(frozen=True)
   class ReasoningInput:
       current_event: CurrentEvent
       historical_context: tuple[EpisodicRecord, ...]
       visitor_profile: VisitorProfile | None
       risk_pattern: RiskPattern | None
       evidence_refs: tuple[str, ...] = ()
       previous_actions: tuple[ActionRecord, ...] = ()
       conflicts: tuple[ConflictFlag, ...] = ()
       modalities: tuple[EvidenceModality, ...] = ()
       cross_modal_contexts: tuple[CrossModalContext, ...] = ()  # ADR-0029 D4：新增（默认空）
   ```

   - **C1 仍成立**：`CrossModalContext` 不含 `risk_score` / `decision` / `warning` / `recommended_action`（见 D2 字段集），故 `REASONING_INPUT_FIELD_WHITELIST` 增 `cross_modal_contexts` 后，`test_reasoning_input_has_no_decision_fields`（`test_invariants.py:122`）仍绿；`_c1.py` 白名单同步更新（Slice B 强制子任务）。
   - **C2 仍成立**：字段是 `CrossModalContext` 不可变投影，Consumer 不写 Memory。

2. **Consumer 可选注入（零行为变化）**：`RuleBasedMemoryConsumer.__init__` 新增两个可选参数 `cross_modal_retrieval` / `cross_modal_explainer`（默认 `None`）；`consume` 在既有管道产 `ReasoningInput` 后，若两者均非 None，则：

   ```python
   links = self._cross_modal_retrieval.get_links_for_visitor(current_event.visitor_instance_id)
   contexts = tuple(
       self._cross_modal_explainer.explain(link, self._memory_store)  # 需 memory_store 引用
       for link in sorted(links, key=lambda l: l.link_id)
   )
   # 以 cross_modal_contexts=contexts 重建 ReasoningInput（其余字段逐字段一致）
   ```

   - **零行为变化**：未注入时 `cross_modal_contexts=()`，产出的 `ReasoningInput` 与历史**逐字段一致**（对应契约测试 `test_consume_unchanged_without_cross_modal`，Slice C 新增）；
   - **`MemoryStore` 引用**：`RuleBasedMemoryConsumer` 现不持 `MemoryStore`（经 `Retrieval` 间接访问）。注入解释器需 `memory_store` 查 episode——经可选参数 `memory_store: MemoryStore | None = None` 传入（仅用于 explain 查 peer episode；不写）；缺省 None 时即便注入了 retrieval 也跳过解释（防御）；
   - **附加而非裁决**：Consumer 只把描述性 context 挂上，**不**修改 `historical_context` / `conflicts` / 任何既有字段，不产结论。

3. **`MemoryQuery.compose_context` 增强（开放项，不在 v1 范围）**：未来可让单访客"为何报警"视图就地嵌入相关 `CrossModalContext`（在返回 dict 增 `cross_modal` 键）——v1 不做，避免与 Consumer 路径重复；归 §5。

### D5：确定性 + 可溯源（C3 / C5 契约）

- **C3 确定性**：`CrossModalRetrieval` 四轴返回按 `link_id` 升序；`CrossModalExplainer.explain` 是纯函数（不读墙钟、不随机）；`explanation` 文本由固定模板 + 数据字段拼接，同输入两次逐字符一致；
- **C5 溯源**：`CrossModalContext.source_link_id` / `source_episode_ids` 锚定到具体 link 与 episode；`explanation` 中引用的每个事实（事件摘要、时间窗、强度）均可追到 `peer_episodes` / `association_strength` 字段；
- **structlog-safe**：`CrossModalContext` / `CrossModalEpisodeRef` 提供 `to_dict`（datetime→ISO、枚举→value、tuple→list），与 `CrossModalLink.to_dict` 同构；
- **错误隔离**：解释器查 episode 若遇 `episode_ids` 中某 id 在 `memory_store` 缺失（理论不应发生——link 已悬空校验，ADR-0028 D5），抛 `RetrievalError` 类分层异常（不静默、不向上抛裸异常）；不回写任何状态。

---

## 3. 动机（Rationale）

1. **解释是 Memory 价值闭环的最后一环**：ADR-0024 I4 可解释性的承诺是"Agent 能回答基于哪个事件"——跨模态边若无人读，等于没建；
2. **成本极低、边界极清**：全部基于 ADR-0028 已落地的 link + episode，纯只读投影，不引入模型、不写存储、不加运行时扫描；
3. **从结构上关闭隐私漏洞**：`shared_deployment_context: bool` 红化设计，使"是否同上下文"可解释而 `device_id` 永不外泄——正面解决 ADR-0028 开放项 #5，而非留待未来"记得拦截"；
4. **与既有解释视图正交**：`MemoryQuery`（单访客）与本 ADR（图边）是不同粒度的解释，互不耦合、可独立演进；
5. **C1/C2 天然兼容**：解释层只描述、不决策、不写，与 ADR-0025 全部硬边界同向，无冲突。

---

## 4. 后果（Consequences）

### 正面

- Memory 首次具备**跨模态关系的可读解释**：Agent 能理解"视觉X 与 音频Y 什么关系、何时、强度多少、从哪来"；
- 正面关闭 ADR-0028 开放项 #5（`device_id` 经 link 泄入 Reason 的隐患），以红化 bool 根治；
- 全部组件可选注入、默认空，存量行为与契约逐字段不变（C1/C2/C3 全绿）；
- 为 `ReasoningEngine` 提供新的描述性上下文输入（跨模态合证是解释性推理核心），且不新增任何判定字段。

### 负面 / 代价

- `ReasoningInput` 字段 +1（白名单同步更新，`_c1.py` 维护成本微增）；
- `RuleBasedMemoryConsumer` 构造签名 +2 可选参数（retrieval / explainer / memory_store），依赖注入图略扩；
- 解释器查 peer episode 需 `MemoryStore` 引用传入（Consumer 原本不直接持 store）——v1 经可选参数解决，未来可考虑 `CrossModalRetrieval` 直接缓存 episode 投影以减少 Consumer 对 store 的耦合（§5 开放项）。

### 必须承担的技术债 / 后续动作

- 多跳图遍历（ep-A→B→C 解释）；
- 证据级关联解释（`supporting_evidence_ids` 填充后，解释"哪条证据支撑哪条证据"）；
- `MemoryQuery.compose_context` 嵌入 `cross_modal`（单访客视图就地展示相关边）；
- 解释模板的本地化 / 多语言（v1 中文硬编码，归 i18n 课题）。

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **多跳图遍历**：`explain` v1 仅直接边；图多跳（A-B-C 链）的解释聚合策略（是否递归展开、如何避免环路）归后续增量；
- **证据级关联解释**：`supporting_evidence_ids`（ADR-0027 Slice C v1 留空）填充后的细粒度解释——归融合层（ADR-0026 §6 `CrossModalEvidence`）产出后再设计；
- **`MemoryQuery` 嵌入跨模态**：单访客"为何报警"视图是否就地展示相关 `CrossModalContext`——v1 不做（避免与 Consumer 路径重复）；
- **Consumer 对 `MemoryStore` 的耦合**：v1 经可选 `memory_store` 参数让解释器查 peer episode；更干净的做法是 `CrossModalRetrieval` 在构造时缓存 episode 投影（只读快照），使 Consumer 完全不持 store——机制与取舍归开放项；
- **解释文本 i18n**：v1 中文模板硬编码；多语言归 i18n 课题；
- **跨设备关联的解释**：ADR-0028 明确 v1 不做跨设备关联，故 `shared_deployment_context` 仅覆盖同设备情形；未来若放开跨设备（需设备拓扑），解释层需新增"拓扑相关"维度，归 ADR-0028 对应开放项。

---

## 6. 实施切片（实施顺序，冻结后执行）

- **Slice A（核心检索+解释）**：`memory/cross_modal_explainer.py` 实现 `CrossModalRetrieval`（D1 四轴）+ `CrossModalExplainer` / `CrossModalContext` / `CrossModalEpisodeRef`（D2/D3/D5）；纯只读、确定性、隐私红化；附带 `tests/memory/test_cross_modal_explainer.py`（四轴检索正确性 + explain 确定性 + 关系映射 + `shared_deployment_context` 红化 + 无 device_id / 无禁止判定字段断言 + 负例：多跳不处理）。
- **Slice B（契约扩展）**：`ReasoningInput.cross_modal_contexts` 字段 + `to_dict`/`from_dict` + `REASONING_INPUT_FIELD_WHITELIST`（`_c1.py`）增 `cross_modal_contexts` + docstring 同步；`tests/memory/consumer/test_invariants.py::test_reasoning_input_has_no_decision_fields` 仍绿（C1 不回退）。
- **Slice C（Consumer 接线）**：`RuleBasedMemoryConsumer` 可选注入 `cross_modal_retrieval` / `cross_modal_explainer` / `memory_store`（D4）；`consume` 在注入时附加 `cross_modal_contexts`；`tests/memory/consumer/test_orchestrator.py` 新增 `test_consume_unchanged_without_cross_modal`（零行为变化锚点）+ `test_cross_modal_contexts_attached_when_injected`（注入后正确附加、且与既有字段独立）。

### 验收清单（Acceptance Criteria）

1. `get_links_for_episode / _for_visitor / _for_device / _in_window` 四轴返回正确且按 `link_id` 确定性排序（D1）；
2. `explain` 产出 `CrossModalContext`：`relationship` 映射正确、`association_strength == link.confidence`、`shared_deployment_context` 红化正确（同设备 True / 异设备 False / 无 device None→False）、`explanation` 确定性可复现（D2/D3）；
3. `CrossModalContext` **不含** `device_id` 字段、**不含** `CONSUMER_FORBIDDEN_FIELDS` 任一（C1 兼容性，D2）；
4. `ReasoningInput.cross_modal_contexts` 默认空；`test_reasoning_input_has_no_decision_fields` 仍绿（白名单同步，D4/Slice B）；
5. `RuleBasedMemoryConsumer` 未注入时 `consume` 产出 `ReasoningInput` 与历史逐字段一致（D4 零行为变化，Slice C）；
6. 注入后 `cross_modal_contexts` 含当前访客相关 link 的解释，且与 `historical_context` / `conflicts` 等字段独立、不修改任何既有字段（D4）；
7. 错误隔离：解释器遇 episode 缺失抛分层异常，不静默、不回写（D5）；
8. 多跳 / 证据级关联显式**不处理**（负例或文档固化，不静默退化）；
9. Slice A/B/C 全量 pytest 全绿（AGENTS.md「全量测试全绿」基线，不允许回归）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3「未授权改架构决策文件」）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-07**：初稿（Proposed）。基于 ADR-0028 已落地的 `CrossModalLink` / `CrossModalLinkStore` / `CrossModalLinkRuntime`（PR #150/#151），设计纯只读的**跨模态检索 + 解释层**：`CrossModalRetrieval`（四轴图检索）+ `CrossModalExplainer` / `CrossModalContext`（隐私安全、确定性、非判断解释）+ `ReasoningInput.cross_modal_contexts` 可选扩展。核心是"解释而非判断"——正面关闭 ADR-0028 开放项 #5（device_id 经 link 泄入 Reason），以红化 `shared_deployment_context: bool` 根治。
