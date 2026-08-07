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
```

### 0.1 为什么现在做（价值排序）

- **建边只是手段，解释才是价值**：ADR-0028 解决"Memory 能不能关联"，本 ADR 解决"关联能不能被理解"——后者才是 I4 可解释性（ADR-0024）对 Agent 的最终交付；
- **零新增判断成本**：解释层是**纯只读投影**（复用 ADR-0028 的 link 与 episode），不引入任何模型、不写 Memory、不改 Graph；
- **直接关闭 ADR-0028 开放项 #5**：`CrossModalLink` 不存 `device_id`（ADR-0028 D1），但 Agent 解释时需要知道"是否同上下文"——本 ADR 提供**红化的** **`shared_deployment_context: bool`**（不暴露原始安装标识），从设计上杜绝 `device_id` 经 link 链路泄入 Reason（ADR-0025 §3.1 隐私边界）；
- **与** **`MemoryQuery.compose_context`** **互补而非重叠**：`MemoryQuery` 回答"单访客为何报警"（按 `visitor_instance_id` 投影），本 ADR 回答"这两个跨模态事件什么关系"（图级边投影）——两者是不同粒度的解释视图，共同构成 Memory 的可解释面。

### 0.2 本 ADR 的边界（明确不做——**核心纪律**）

**本 ADR 是"检索 + 解释"层，不是"判断"层。一句话铁律：解释只读事实、只描述关系、只给溯源，绝不产出结论。**

**层 / 数据边界（消费侧只看 Context，不看 Link）**：

|   |
| - |

层

|   |
| - |

数据

|   |
| - |

职责

|   |
| - |

Memory Graph

|   |
| - |

`CrossModalLink`

|   |
| - |

边的内部存储（link_id / episode_ids / relationship / overlap / confidence）

|   |
| - |

Retrieval

|   |
| - |

Link 查询（以 episode 为主）

|   |
| - |

取回相关 link

|   |
| - |

Explanation

|   |
| - |

`CrossModalContext`

|   |
| - |

把 link 投影为结构化、隐私安全、可解释的上下文

|   |
| - |

Reasoning

|   |
| - |

`CrossModalContext`

|   |
| - |

作为 `ReasoningInput.cross_modal_contexts` 输入

|   |
| - |

Decision

|   |
| - |

`RiskSignal`

|   |
| - |

唯一决策产物（ADR-0010 单一决策中心）

> **关键边界**：`RuleBasedMemoryConsumer` 消费的是 **`CrossModalContext`**，不是 `CrossModalLink`。Link 是 Graph 内部结构，Context 才是 Reasoning 输入——两者不许混用（见 D4 / §2.1 C6）。

---

## 1. 决策（Decision）

新增一个**纯只读**的跨模态可解释层 `memory/cross_modal_explainer.py`，包含检索 / 解释 / 渲染组件：

1. **`CrossModalRetrieval`**——图级只读检索：**v1 以** **`get_links_for_episode(episode_id)`** **为唯一对外主路径**（当前事件 → 相关 link → contexts），辅以 `get_links_in_window(start, end)` 作为**可选内部能力**；`get_links_for_visitor` **延期（不在 v1）**——visitor→episodes→links 的 join 属 `MemoryQuery` 职责（单访客视图本就由其投影），`CrossModalRetrieval` 只做"episode → links"，避免与 `MemoryQuery` 出现两个都能查 visitor 的查询系统；`get_links_for_device` **不在 v1 范围**（避免演变为"家庭全局知识查询系统"）；device_id 仅作 join key，绝不外泄；
2. **`CrossModalExplainer`** **+ 契约** **`CrossModalContext`** **/** **`CrossModalEpisodeRef`**——把一条 `CrossModalLink`（连同其两端 episode）投影为**结构化、隐私安全、确定性**的 `CrossModalContext`（**仅结构化事实，不含自然语言**）；另设独立 **`ExplanationRenderer`** 负责把 `CrossModalContext` 渲染为自然语言（D3，i18n 友好 seam）；
3. **`ReasoningInput.cross_modal_contexts`** **扩展（可选）**——`RuleBasedMemoryConsumer` 消费的是 `CrossModalContext`（**不是** `CrossModalLink`），把当前访客相关的跨模态解释作为描述性上下文附给 `ReasoningInput`（默认空、零行为变化、不破坏 C1/C2）。

```
```

---

## 2. 决策要点（D1–D5）

### D1：`CrossModalRetrieval`——图级只读检索（v1 = episode 主路径 + window 可选；visitor/device 延期）

**问题**：`CrossModalLinkStore` 现仅有 `get_links_by_episode(record_id)` / `all_links()`——没有"按时间窗"取边的视角；而"某访客相关的跨模态关系"本可由 `MemoryQuery`（visitor → episodes → links）完成，不属本组件职责。

**决策**：新增 `CrossModalRetrieval`（与 `MemoryQuery` 同层级的只读查询组件，不接 ABC——v1 单实现，避免与 `Retrieval` ABC 语义混淆；`Retrieval` ABC 是 episode 召回，本类是对 link 图的召回）。**v1 以 episode 为主路径，visitor/device 查询延期（归 `MemoryQuery` / 后续能力）**：

- **主路径** **`get_links_for_episode(episode_id)`**：给定"当前事件"的 episode_id，从 `CrossModalLinkStore.get_links_by_episode` 取回所有相关 link——这是最自然、最常用的入口（当前事件 → 关联跨模态事件 → 解释）；
- **可选内部能力** **`get_links_in_window(start, end)`**：供"某时间窗全部跨模态关系"场景复用，v1 实现但**不作为对外主 API**（检索面收敛）；
- **`get_links_for_visitor(visitor_instance_id)`** **延期（不在 v1）**：visitor 是业务实体、link 是事件实体，二者 join（visitor → episode → link）属 `MemoryQuery` 职责（`MemoryQuery` 已按 `visitor_instance_id` 投影单访客视图），**不是** `CrossModalRetrieval` 的职责；v1 不做，避免与 `MemoryQuery` 出现两个都能查 visitor 的查询系统（C2 边界清晰）——`CrossModalContext` 经 `MemoryQuery` 取回后再映射到 link 即可；
- **`get_links_for_device(device_id)`** **延期（不在 v1）**：按 device 查"家庭全局跨模态关系"容易演变成"家庭全局知识查询系统"，属于未来能力（需明确产品需求与拓扑建模），v1 不做；device_id 仅作为 `get_links_for_episode` 的底层 join key（读两端 episode 的 device_id 用于 `shared_deployment_context` 红化，D2），绝不外泄；
- **确定性（C3）**：各轴返回均按 `link_id` 升序，join 去重用 `set` + 排序，同输入两次结果一致；
- **复用既有基元**：`get_episodic_by_visitor` / `all_episodic()`（ADR-0028 D5 已落地）直接可用，无新存储 API。

### D2：`CrossModalExplainer` + `CrossModalContext` 契约（纯结构化，无自然语言）

**问题**：`CrossModalLink` 是机器索引（link_id / episode_ids / relationship / time_overlap / confidence），人/Agent 读不懂"这到底是什么关系"。需要一层**投影**把它变成可解释结构——且必须**隐私安全**（无 device_id）、**非判断**（只描述）。

**决策**：新增 `CrossModalContext` / `CrossModalEpisodeRef` 两个 `frozen` 数据契约 + `CrossModalExplainer`。`CrossModalContext` 是**纯结构化事实**（`frozen`），**只承载可机器消费的事实字段，不含任何自然语言字符串**：

```
```

- **`shared_deployment_context`** **红化（解决 ADR-0028 开放项 #5）**：解释器读两端 episode 的 `device_id`，**仅在两者均非 None 且相等时置 True**，绝不把原始 `device_id` 字符串写进 `CrossModalContext`——从数据结构上保证 `device_id` 不经 link 链路泄入 Reason（ADR-0025 §3.1）；
- **`link_confidence`** **（替代** **`association_strength`** **，更贴近来源、降低误用）**：直接取 `link.confidence`，语义是"**Link Runtime 对建立这条边的置信程度**"，**不是**"事件关联强度"——命名贴近来源，降低未来被误用作决策阈值的风险（如 `if ctx.link_confidence > 0.8: alert()` 仍属下游 Decision 层职责，本层不鼓励）；值 [0,1]，非风险分；字段名显式与 `risk_score` / `decision` 划清（C1 友好）；
- **`relationship`** **是给定标签（不重新解释语义）**：`CrossModalContext` 直接透传 link 的 `relationship`（`CrossModalRelationship`：SUPPORTS / CO_OCCURS），**解释层不重新解释 / 升级语义**——关系词汇的语义收紧（如 `SUPPORTS` 严格化、可能新增 `TEMPORALLY_ALIGNED`）归 ADR-0028 后续（见 ADR-0028 §5 开放项）；解释层只陈述"关系类别"，不是"结论"；
- **纯函数（C3）**：`explain` 不读墙钟、不随机、不写状态；同 `(link, memory_store 状态)` 两次产出逐字段一致（审计 / 回放一致）；
- **溯源（C5）**：`source_link_id` + `source_episode_ids` 让每条解释可追到具体 link 与 episode（与 `SourceRef` 思路一致，但不引入 `ReasoningResult` 依赖）；
- **C6（解释层禁止风险语义，硬约束）**：`CrossModalContext` **MUST NOT** 包含 `risk_score` / `risk_level` / `alert` / `warning` / `decision` / `recommendation` 任一字段（见 §2.1）；自然语言由 `ExplanationRenderer`（D3）产出，不进 Context。该约束由 `tests/memory/test_cross_modal_explainer.py::test_context_has_no_risk_semantics` 钉死，未来任何实现若向 Context 加风险语义将直接失败。

### D3：`ExplanationRenderer`——结构化 Context → 自然语言（i18n 友好 seam）

**问题**：自然语言描述若固化进 `CrossModalContext`（D2），未来模型 / 语言 / 国际化迭代会污染契约。正确做法是 **Context 与 Renderer 分离**——Context 只存事实（D2），Renderer 单独负责"事实 → 人话"。

**决策**：新增 `ExplanationRenderer`（独立组件，与 `CrossModalExplainer` 解耦），其唯一职责是把 `CrossModalContext` 渲染为自然语言：

**关系词汇 → 描述映射（确定性，无模型）**：

|   |
| - |

`relationship`

|   |
| - |

中文描述（中性陈述）

|   |
| - |

句首锚定

|   |
| - |

`SUPPORTS`

|   |
| - |

跨模态支撑

|   |
| - |

视觉事件「…」与音频事件「…」在时间窗重叠约 Ns，在同一部署源上下文相互支撑

|   |
| - |

`CO_OCCURS`

|   |
| - |

同主体合证

|   |
| - |

同一访客的两次事件在时间上相邻合证

- **句法铁律（§0.2 / C6）**：模板只陈述**事实**（什么事件、何时、重叠多久、强度多少、是否同上下文），**不得**出现"疑似 / 可能 / 应当 / 建议"等判断词；渲染输出以句号闭合的**陈述句**收尾；
- **`link_confidence`** **显式标注"建边置信"**：文本含"建边置信 X.XX"，不复用"关联强度/风险"措辞，避免与风险语义混淆；
- **i18n seam（解耦收益）**：`ExplanationRenderer` 是唯一的文本出处，未来多语言只需替换渲染器（或注入 locale），`CrossModalContext` 契约零改动——这正是把 `explanation` 抽离出 Context 的核心动机；
- **关系词汇覆盖 + fail-closed**：`ExplanationRenderer` 映射表必须覆盖 `CrossModalRelationship` **全部枚举值**（契约测试 `test_renderer_covers_all_relationships`），对未知 / 新增关系值**抛** `ValueError`（不静默降级）——关系词汇集随 ADR-0028 后续收紧可能演进（如新增 `TEMPORALLY_ALIGNED`），渲染器不得默认可疑值；
- **多跳排除（v1）**：`render` 只消费 `explain` 产出的直接边（两端 episode）；图多跳（A-B-C）不在此渲染，归 §5。

**确定性生成示例**（输入 = ADR-0028 首条边，经 `explain` → `CrossModalContext` → `render`）：

```
```

（无 device_id 回显；无"疑似诈骗"之类结论；可溯源到 `source_link_id` + 两端 `episode_id`。）

### D4：`ReasoningInput.cross_modal_contexts` 扩展 + Consumer 可选注入

**问题**：解释层产出的 `CrossModalContext` 目前无处可去——Agent（`ReasoningEngine`）读不到。`ReasoningInput` 是 Consumer→Reasoning 的唯一契约载体。

**决策**：

1. **契约扩展（最小、兼容）**：`ReasoningInput` 新增可选字段（默认值空，不影响既有 8 字段语义）：
   ```
   ```
   - **C1 仍成立**：`CrossModalContext` 不含 `risk_score` / `decision` / `warning` / `recommended_action`（见 D2 字段集），故 `REASONING_INPUT_FIELD_WHITELIST` 增 `cross_modal_contexts` 后，`test_reasoning_input_has_no_decision_fields`（`test_invariants.py:122`）仍绿；`_c1.py` 白名单同步更新（Slice B 强制子任务）。
   - **C2 仍成立**：字段是 `CrossModalContext` 不可变投影，Consumer 不写 Memory。
2. **Consumer 可选注入（零行为变化）**：`RuleBasedMemoryConsumer.__init__` 新增两个可选参数 `cross_modal_retrieval` / `cross_modal_explainer`（默认 `None`）；`consume` 在既有管道产 `ReasoningInput` 后，若两者均非 None，则（**注意：注入的解释器产出** **`CrossModalContext`** **而非** **`CrossModalLink`；\*\*\*\*`ExplanationRenderer`** **是展示侧 seam，由调用方在需要自然语言时单独调用，不进 Consumer**）：
   ```
   ```
   - **零行为变化**：未注入时 `cross_modal_contexts=()`，产出的 `ReasoningInput` 与历史**逐字段一致**（对应契约测试 `test_consume_unchanged_without_cross_modal`，Slice C 新增）；
   - **`MemoryStore`** **引用**：`RuleBasedMemoryConsumer` 现不持 `MemoryStore`（经 `Retrieval` 间接访问）。注入解释器需 `memory_store` 查 episode——经可选参数 `memory_store: MemoryStore | None = None` 传入（仅用于 explain 查 peer episode；不写）；缺省 None 时即便注入了 retrieval 也跳过解释（防御）；
   - **附加而非裁决**：Consumer 只把描述性 context 挂上，**不**修改 `historical_context` / `conflicts` / 任何既有字段，不产结论。
3. **`MemoryQuery.compose_context`** **增强（开放项，不在 v1 范围）**：未来可让单访客"为何报警"视图就地嵌入相关 `CrossModalContext`（在返回 dict 增 `cross_modal` 键）——v1 不做，避免与 Consumer 路径重复；归 §5。

### D5：确定性 + 可溯源（C3 / C5 契约）

- **C3 确定性**：`CrossModalRetrieval` 各轴返回按 `link_id` 升序；`CrossModalExplainer.explain` 是纯函数（不读墙钟、不随机）；`ExplanationRenderer.render` 输出由固定模板 + `CrossModalContext` 字段拼接，同输入两次逐字符一致；
- **C5 溯源**：`CrossModalContext.source_link_id` / `source_episode_ids` 锚定到具体 link 与 episode；`ExplanationRenderer` 文本中引用的每个事实（事件摘要、时间窗、强度）均可追到 Context 对应字段；
- **structlog-safe**：`CrossModalContext` / `CrossModalEpisodeRef` 提供 `to_dict`（datetime→ISO、枚举→value、tuple→list），与 `CrossModalLink.to_dict` 同构；
- **错误隔离**：解释器查 episode 若遇 `episode_ids` 中某 id 在 `memory_store` 缺失（理论不应发生——link 已悬空校验，ADR-0028 D5），抛 `RetrievalError` 类分层异常（不静默、不向上抛裸异常）；不回写任何状态。

### 2.1 硬约束 C6：解释层禁止风险语义（Invariant）

为防止未来实现漂移（例如向 `explanation` 写"老人跌倒风险较高"），将 D2 的纪律提升为**正式不可变约束**：

- `CrossModalContext` **MUST NOT** 携带任何风险 / 决策语义字段：`risk_score` / `risk_level` / `alert` / `warning` / `decision` / `recommendation`；
- `ExplanationRenderer` 产出的自然语言**MUST NOT** 含“疑似 / 可能 / 应当 / 建议 / 风险”等判断词（句法铁律已在 D3 固化，渲染器复用同一模板纪律）；
- **C6（派生·因果不可暗示）**：`CrossModalContext` / `ExplanationRenderer` **MUST NOT** 暗示因果——`SUPPORTS` 是“两事件相互支撑的**事实陈述**”，**不表示**“音频事件**导致**视觉事件”。渲染模板不得出现“导致 / 引起 / 因为”等因果词；`support ≠ cause` 由契约测试 `test_context_does_not_imply_causality` 钉死——防止“撞击声支撑跌倒”被误读为“撞击声导致跌倒”（后者已是 Decision 层越界）；
- 唯一合法的“判断”出口是 `RiskSignal` → `DecisionEngine`（ADR-0010 单一决策中心），解释层任何产物都不得越界；
- 该约束由契约测试钉死：`test_context_has_no_risk_semantics`（Context 字段白名单断言）+ `test_renderer_output_has_no_judgment_words`（渲染器输出断言）+ `test_context_does_not_imply_causality`（因果不暗示）；
- 这与 §0.2 层 / 数据边界表一致：Explanation 层产 `CrossModalContext`（事实），Decision 层产 `RiskSignal`（结论），两者在架构上物理隔离。

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
- 多语言渲染：v1 仅中文 `ExplanationRenderer` 实现；未来多语言只需新增 locale 渲染器（Context 契约不变，D3 i18n seam）。

---

## 5. 开放问题（Open Questions，本 ADR 不抢答）

- **多跳图遍历**：`explain` v1 仅直接边；图多跳（A-B-C 链）的解释聚合策略（是否递归展开、如何避免环路）归后续增量；
- **证据级关联解释**：`supporting_evidence_ids`（ADR-0027 Slice C v1 留空）填充后的细粒度解释——归融合层（ADR-0026 §6 `CrossModalEvidence`）产出后再设计；
- **`MemoryQuery`** **嵌入跨模态**：单访客"为何报警"视图是否就地展示相关 `CrossModalContext`——v1 不做（避免与 Consumer 路径重复）；
- **Consumer 对** **`MemoryStore`** **的耦合**：v1 经可选 `memory_store` 参数让解释器查 peer episode；更干净的做法是 `CrossModalRetrieval` 在构造时缓存 episode 投影（只读快照），使 Consumer 完全不持 store——机制与取舍归开放项；
- **解释文本 i18n**：`ExplanationRenderer` 已是 i18n seam（D3），v1 仅中文实现；多语言只需新增 locale 渲染器，Context 契约零改动；
- **跨设备关联的解释**：ADR-0028 明确 v1 不做跨设备关联，故 `shared_deployment_context` 仅覆盖同设备情形；未来若放开跨设备（需设备拓扑），解释层需新增“拓扑相关”维度，归 ADR-0028 对应开放项。
- **Consumer 注入收敛（架构优化，非 v1 必须）**：v1 让 `RuleBasedMemoryConsumer` 直接持 `cross_modal_retrieval` + `cross_modal_explainer` + `memory_store` 三依赖（D4），略违反 ADR-0025 C2（Consumer 本应只经 `Retrieval` 间接访问 Memory，不直接感知 Graph 结构）。更干净的做法是引入 `CrossModalContextProvider`（或 `MemoryContextAssembler`）：`episode_id → retrieve → explain → CrossModalContext`，Consumer 只接受**可选** `context_provider`，完全不感知 CrossModal 内部结构。v1 不强制，归冻结节后重构（单独 PR）；本 ADR 仅记录该优化方向，不影响 v1 实现。

---

## 6. 实施切片（实施顺序，冻结后执行）

- **Slice A（核心检索+解释）**：`memory/cross_modal_explainer.py` 实现 `CrossModalRetrieval`（D1：主路径 `get_links_for_episode` + 可选内部能力 `get_links_in_window`；`get_links_for_visitor` / `get_links_for_device` **延期**——visitor join 属 `MemoryQuery`）+ `CrossModalExplainer`（D2 结构化 Context，`link_confidence` 替代 `association_strength`）+ `ExplanationRenderer`（D3 渲染 + 关系词汇覆盖 fail-closed）+ `CrossModalContext` / `CrossModalEpisodeRef`（D5）；纯只读、确定性、隐私红化；附带 `tests/memory/test_cross_modal_explainer.py`（episode 主路径 + window 内部能力检索正确性 + explain 结构化确定性 + Renderer 关系映射 + `shared_deployment_context` 红化 + **C6 无风险语义 / 无因果暗示断言** + 无 device_id 断言 + 负例：多跳不处理）。
- **Slice B（契约扩展）**：`ReasoningInput.cross_modal_contexts` 字段 + `to_dict`/`from_dict` + `REASONING_INPUT_FIELD_WHITELIST`（`_c1.py`）增 `cross_modal_contexts` + docstring 同步；`tests/memory/consumer/test_invariants.py::test_reasoning_input_has_no_decision_fields` 仍绿（C1 不回退）。
- **Slice C（Consumer 接线）**：`RuleBasedMemoryConsumer` 可选注入 `cross_modal_retrieval` / `cross_modal_explainer` / `memory_store`（D4）；`consume` 在注入时附加 `cross_modal_contexts`；`tests/memory/consumer/test_orchestrator.py` 新增 `test_consume_unchanged_without_cross_modal`（零行为变化锚点）+ `test_cross_modal_contexts_attached_when_injected`（注入后正确附加、且与既有字段独立）。

### 验收清单（Acceptance Criteria）

1. **D1 episode 主路径**：`get_links_for_episode` 返回正确且按 `link_id` 确定性排序；`get_links_in_window` 作为可选内部能力返回正确；`get_links_for_visitor` / `get_links_for_device` 在 v1 **不存在**（延期，不实现——visitor join 归 `MemoryQuery`）；
2. **D2 结构化 Context**：`explain` 产出 `CrossModalContext` **仅结构化事实**（`relationship` / `source_episode` / `target_episode` / `overlap_seconds` / `link_confidence` / `shared_deployment_context` / `source_link_id` / `source_episode_ids`），`link_confidence == link.confidence`（语义=建边置信，非事件关联强度）、`shared_deployment_context` 红化正确（同设备 True / 异设备 False / 无 device None→False）、**无** **`explanation`** **字段**；
3. **D3 Renderer 解耦**：`ExplanationRenderer.render(context)` 产出确定性自然语言，`SUPPORTS`/`CO_OCCURS` 描述正确、句法铁律（无判断词）、与 Context 解耦（i18n seam）；
4. **C6 硬约束**：`CrossModalContext` 不含 `risk_score` / `risk_level` / `alert` / `warning` / `decision` / `recommendation` 任一；`ExplanationRenderer` 输出无判断词；`test_context_has_no_risk_semantics` + `test_renderer_output_has_no_judgment_words` 钉死（§2.1）；
5. `CrossModalContext` **不含** `device_id` 字段、**不含** `CONSUMER_FORBIDDEN_FIELDS` 任一（C1 兼容性，D2）；
6. `ReasoningInput.cross_modal_contexts` 默认空；`test_reasoning_input_has_no_decision_fields` 仍绿（白名单同步，D4/Slice B）；
7. `RuleBasedMemoryConsumer` 未注入时 `consume` 产出 `ReasoningInput` 与历史逐字段一致（D4 零行为变化，Slice C）；
8. 注入后 `cross_modal_contexts` 含当前访客相关 link 的解释（Context，非 Link），且与 `historical_context` / `conflicts` 等字段独立、不修改任何既有字段（D4）；
9. 错误隔离：解释器遇 episode 缺失抛分层异常，不静默、不回写（D5）；
10. 多跳 / 证据级关联显式**不处理**（负例或文档固化，不静默退化）；
11. Slice A/B/C 全量 pytest 全绿（AGENTS.md「全量测试全绿」基线，不允许回归）。

---

## 7. 修订记录（Changelog）

> **修订权属（呼应 AGENTS.md §6.3「未授权改架构决策文件」）**：本 ADR 处于 Proposed 阶段由 Owner 评审；**冻结（Accepted）后的修订由 Owner 追加新条目，AI 不修改修订记录**。

- **2026-08-07**：初稿（Proposed）。基于 ADR-0028 已落地的 `CrossModalLink` / `CrossModalLinkStore` / `CrossModalLinkRuntime`（PR #150/#151），设计纯只读的**跨模态检索 + 解释层**：`CrossModalRetrieval`（四轴图检索）+ `CrossModalExplainer` / `CrossModalContext`（隐私安全、确定性、非判断解释）+ `ReasoningInput.cross_modal_contexts` 可选扩展。核心是"解释而非判断"——正面关闭 ADR-0028 开放项 #5（device_id 经 link 泄入 Reason），以红化 `shared_deployment_context: bool` 根治。这份文档怎么样？
- **2026-08-07（审查修订）**：依据 Owner 架构审查收紧三点——(1) **D1 episode 主路径**：`get_links_for_episode` 升为主 API，`get_links_for_visitor` / `get_links_in_window` 降为内部能力，`get_links_for_device` 延期（避免演变为“家庭全局知识查询系统”）；(2) **D2/D3 抽取** **`ExplanationRenderer`**：`CrossModalContext` 改为纯结构化事实（移除 `explanation` 自然语言字段），自然语言渲染独立为 `ExplanationRenderer`（i18n seam，关系词汇→描述映射迁入）；(3) **新增 C6 硬约束**：解释层禁止风险语义（`CrossModalContext` 不得含 `risk_score` / `risk_level` / `alert` / `warning` / `decision` / `recommendation`，渲染器输出不得含判断词），由契约测试钉死。同步吸收审查的层 / 数据边界表（Consumer 消费 Context 而非 Link）。
- **2026-08-07（审查修订 2）**：依据 Owner 第二轮架构审查再收紧——(1) **`association_strength` → `link_confidence`**：字段名更贴近来源（“Link Runtime 对建边的置信程度”而非“事件关联强度”），降低未来被误用作决策阈值（`if ctx.link_confidence > 0.8: alert()`）的名字诱导风险；(2) **C6 新增因果不可暗示**：`support ≠ cause`，`SUPPORTS` 是事实陈述非因果，渲染器不得含“导致/引起”词，契约测试 `test_context_does_not_imply_causality` 钉死；(3) **visitor 查询延期**：`get_links_for_visitor` 从“内部能力”进一步降为**不在 v1**——visitor→episode→link 的 join 属 `MemoryQuery` 职责，避免与 `MemoryQuery` 出现两个查 visitor 的查询系统（C2 边界）；v1 仅 `get_links_for_episode` 主路径 + 可选 `get_links_in_window`；(4) **`relationship` 不重新解释语义**：解释层直接透传 link 的 relationship，词汇语义收紧（SUPPORTS 严格化 / 可能新增 `TEMPORALLY_ALIGNED`）归 ADR-0028 后续开放项；(5) **Consumer 注入收敛**记录为架构优化方向（引入 `CrossModalContextProvider`），v1 不强制；(6) `ExplanationRenderer` 关系词汇覆盖 + fail-closed（未知值抛 `ValueError`）。
