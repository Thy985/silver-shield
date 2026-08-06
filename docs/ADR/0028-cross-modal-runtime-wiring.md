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

**系统第一次需要具备「视觉看到的事件」与「声音感知的事件」在 Memory 中形成关联的能力。**

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

- **视觉路径**：`MemoryHook.record` 新增可选 `device_id` 参数 → `project_episode` 透传 → `EpisodicRecord.device_id`；pipeline 视觉路径传 `ev.source_video`（demo 中恒 == device_id，ADR-0021 语义）。
- **音频路径**：`AudioSessionRecorder` 已有 `device_id`（`home_entry_01`）→ `MemoryHook.record(device_id=...)` 透传。
- **v1 兼容**：旧记录 `device_id=None`；`from_dict` 缺省 None；`EPISODIC_RECORD_DICT_KEYS` 增 `"device_id"`（契约测试同步）。`same_device` 判定对 `None` 不成立（不关联）→ 渐进可用，存量数据零破坏。
- **隐私**：`device_id` 是安装标识（如 `home_entry_01`），非个人身份；与 ADR-0002 兼容（不进 Reason 判定，仅参与关联索引）。

### D2：`CrossModalLinker` 同源判定扩展——`same_device` 与身份键并列

**问题**：现有 `_same_subject` 仅认「共享 `visitor_instance_id` 或共享 `audio_session_id`」——**纯视觉 episode（仅 visitor_id）+ 纯音频 episode（仅 audio_session_id）无共享键 → 永不关联**，正是本 ADR 要补的核心场景。

**决策**：同源判定升级为「同一上下文」（身份键 **或** 设备键），两路并列：

```
same_context(a, b) = 共享 visitor_instance_id            （既有：视觉身份）
                  OR 共享 audio_session_id               （既有：音频原生身份，D4）
                  OR (a.device_id == b.device_id          （新增：同设备）
                      AND a.device_id is not None)
```

- 关系语义不变：modalities 集合不同 → `SUPPORTS`（跨模态支撑）；相同 → `CO_OCCURS`；
- `_same_subject` 更名为 `_same_context`（同名即改语义，测试同步）；
- **时间窗重叠是硬 gate**（无论哪路同源判定，都必须 `min(leave) > max(enter)` 才建边）——`same_device` 只提供「同源候选」，不豁免时间 gate。

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
- **失败隔离**：`CrossModalLinkRuntime` 内部异常 → 记日志 + 返回空列表，绝不向上抛（不影响 episode 落库）。

### D5：`MemoryStore.all_episodic()` 全量取数 + v1 全量扫描策略

**问题**：linker 需要扫描「全部 episode」找跨模态 pair，但 `MemoryStore` 只有 `get_episodic_by_visitor`（按访客，纯音频 episode 的 visitor 为 None 取不到）与 `snapshot()`（dict 视图）。

**决策**：

- `MemoryStore` 抽象接口新增 `all_episodic() -> list[EpisodicRecord]`（`InMemoryStore` 实现为 `list(self._episodic.values())`；现有实现若缺省则 fail loud `NotImplementedError`）；
- **v1 全量扫描**：每次落库后 `linker.link(store.all_episodic())`——episode 数量级（百级）下 O(n²) 可接受；增量索引（按 device/时间桶）留待 §5 开放项；
- 幂等：`link_id` 确定性 → `CrossModalLinkStore.add` 幂等 upsert（同内容返回 False，不重复建边）。

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
- **断言**：`expected.link: true` → `CrossModalLinkStore` 出现恰好 1 条边，`relationship == supports`，`time_overlap` 与期望重叠一致；
- **负例必配**（测试有效性铁律）：`link: false` 场景（同设备但时间不重叠 / 不同设备同时刻 / 时间重叠但 `min_overlap_seconds` 未达阈值）——每个「建边」断言都有「不建边」对照；
- **验证对象**：`Episode → CrossModalLinker → CrossModalLink`（用户指定的直接链路），不依赖真实媒体，纯 Memory Graph 验证。

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
- **跨设备关联**（入户门摄像头 + 客厅麦克风）：需要设备拓扑知识，超出设备级 v1。

---

## 6. 实施切片（实施顺序，冻结后执行）

- **Slice A（本 ADR 核心）**：`EpisodicRecord.device_id`（D1）+ `MemoryStore.all_episodic()`（D5）+ `CrossModalLinker._same_context` 扩展与 `min_overlap_seconds`（D2/D3）+ `CrossModalLinkRuntime`（D4）+ `MemoryHook` 可选注入 + 两路 `device_id` 透传。
- **Slice B（验证）**：最小 Synthetic Episode Fixture（D6）——声明式 scenario → 直接验证 `Episode → CrossModalLinker → CrossModalLink`；含正/负例对照（同设备重叠 / 同设备不重叠 / 异设备重叠 / 阈值未达）。
- **Slice C（契约）**：`EPISODIC_RECORD_DICT_KEYS` + `device_id` 序列化契约测试 + 回放基线更新（如 `memory_baseline_cross_modal.json` 增 device_id 字段）。

### 验收清单（Acceptance Criteria）

1. 视觉 episode 落库 → 与同设备时间重叠的音频 episode 自动建 `SUPPORTS` 边（D4/D2）；
2. 同设备但不重叠 / 异设备但重叠 → 不建边（D2/D3 负例，变异可检出）；
3. `device_id=None` 旧记录不参与 `same_device` 判定（D1 向后兼容）；
4. `MemoryHook` 未注入 runtime 时，落库行为与历史逐字段一致（D4 零行为变化）；
5. 声明式 scenario fixture 全绿：`link: true/false` 断言与期望一致（D6）；
6. `CrossModalLinkStore.add` 悬空引用校验继续生效（Slice C 契约不回退）。

---

## 7. 修订记录（Changelog）

- **2026-08-06**：初稿（Proposed）。D1–D6 决策要点基于 Slice C（#145）与音频闭环（#148）已落地实现；第一版规则收敛为「same_device + time_overlap」，明确不做身份/语义判定。
