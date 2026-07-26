# ADR-0023: 身份连续性系统 · 具体设计（Concrete Design for ADR-0020）

- **状态**：Proposed
- **日期**：2026-07-26
- **范围**：v2 / 后 MVP 的**身份连续性设计**；把 ADR-0020（短期追踪身份与长期访客身份分离）的"方向"落为三层身份概念 / 解析接口 / 契约影响，并**明确不冒充真实身份**。当前 MVP 不实现。
- **决策者**：Owner
- **相关**：ADR-0020（身份分离方向）、ADR-0006（track_id 会话级）、ADR-0014（三级冻结治理）、ADR-0021（实时风险流，Phase 1）、ADR-0022（证据链，Phase 2）

---

## 1. 背景

已核实现状（`analysis/event_builder.py:163-174`）：`VisitorEvent.visitor_id` 实为 ByteTrack `track_id` 的会话级 UUID，其上没有稳定"人"身份。若在其上直接建 Memory/Profile，会把"track_id"当"人"，导致跨帧/跨天身份碎片化。

ADR-0006 Decision 4 已预警 `track_id` 是会话级。ADR-0020 提出分离。本 ADR 落为三层概念，并**特别约束**：demo 阶段的 session merge **不得冒充**真实身份。

---

## 2. 目标与非目标

**目标**
- 明确区分三层身份概念，预留长期身份扩展点。
- 引入 `IdentityResolver` 接口，v1 用 SessionMerge 产出**会话内**稳定 id（非真实身份）。
- 事件引用稳定身份 id（可用时），`track_id` 仅作溯源。

**非目标（本 ADR 不做）**
- 不实现 ReID / 跨摄像头 / 跨天真实身份识别（Phase 4）。
- 不把 `SessionMergeResolver` 的输出宣称为真实 `person` 身份。
- 不新增 `EventType`、不改 5 类标签。

---

## 3. 决策（具体设计）

### 3.1 三层身份概念（核心）

| 概念 | 含义 | 生命周期 | 当前状态 |
| --- | --- | --- | --- |
| `track_id` | 当前视觉跟踪对象（ByteTrack） | 单帧→离场（会话内） | 已有（ADR-0006） |
| `visitor_instance_id` | 一次访问（一个 visit session） | 进入→离开 | 现有 `visitor_id` 语义对齐为此（重命名/别名） |
| `person_identity_id` | 跨访问的稳定身份 | 跨天/跨摄像头 | **v1 留空（None）**，Phase 4 ReID/Memory 才填充 |

> 现在：`track_id → visitor_instance_id` 已存在（约等 `visitor_id`）。未来：`track_id → visitor_instance_id → person_identity_id`。**`person_identity_id` 在 v1 必为 None，绝不可由 session merge 伪造。**

### 3.2 `IdentityResolver`（NEW，`detection/identity.py`，L2 接口，MINOR）

```python
@dataclass
class IdentityResult:
    visitor_instance_id: str
    person_identity_id: Optional[str] = None   # v1 恒为 None

class IdentityResolver(ABC):
    @abstractmethod
    def resolve(self, track_id: int, ctx: Dict[str, Any]) -> IdentityResult: ...
```

### 3.3 v1：`SessionMergeResolver`（**不冒充身份**）

```python
class SessionMergeResolver(IdentityResolver):
    """同一摄像头 + 同一会话内合并 track_id → 稳定 visitor_instance_id。
    本质只是'当前 session 内猜测同一个人'，NOT 真实身份。"""
    def resolve(self, track_id, ctx):
        return IdentityResult(visitor_instance_id=self._merge(track_id, ctx))
        # person_identity_id 不返回（恒 None）
```

- `confirm(person_identity_id, track_id)` 钩子预留（供 P0-11 家属确认回填 / Phase 4 真实身份）。
- **约束**：任何代码不得把 `visitor_instance_id` 当作 `person_identity_id` 使用；Memory/Profile 在 `person_identity_id` 为 None 时按 `visitor_instance_id` 建临时画像，标注"未确认身份"。

### 3.4 事件透传

- `event_builder` 分配 `visitor_instance_id`（即现有 `visitor_id` 语义）并透传；`BehaviorState`/`RiskSignal`/`VisitorEvent` 均带 `visitor_instance_id`（MINOR 可选字段）。
- 新增可选 `person_identity_id: Optional[str]`（MINOR，v1 全 None），`track_id` 保留溯源。
- Memory/Profile（ADR-0018 历史事件流下游）引用 `person_identity_id`（可用时）；不可用时效 `visitor_instance_id` 临时归并。

### 3.5 冻结契约影响（SemVer 映射，对齐 ADR-0014）

| 改动 | 契约层级 | SemVer | 说明 |
| --- | --- | --- | --- |
| `IdentityResolver`(ABC) / `IdentityResult` | L2 接口 | MINOR | 增量接口，实现可替换 |
| `VisitorEvent/PerceptionEvent/WarningEvent.visitor_instance_id`（可选，对齐现有 visitor_id 语义） | L1 字段 | MINOR | 可选字段，向后兼容 |
| 新增可选 `person_identity_id: Optional[str]` | L1 字段 | MINOR | v1 全 None，预留扩展 |
| `track_id` 语义 / 5 类 `EventType` | L1 | 不变 | 冻结保留 |

**红线**：不得把 `visitor_instance_id` 当 `person_identity_id` 用；不得 v1 伪造真实身份；不得改 `EventType`。

### 3.6 分阶段（Phase 4：身份系统化；v1 设计现在落地）

- **现在（设计）**：本 ADR 定三层概念 + `IdentityResolver` 接口 + SessionMerge v1（Phase 1/2 可顺带接入透传）。
- **Phase 4（实现）**：ReID / 跨天 Memory 产出真实 `person_identity_id`；`confirm()` 钩子接通家属确认 / 中心画像。

---

## 4. 动机（Rationale）

- **身份是长期难题**：不要让 demo 阶段的 session merge 冒充 identity，避免未来跨天把不同人误认为同一 P001。
- **三层清晰**：`track_id`/`visitor_instance_id`/`person_identity_id` 解耦，扩展点明确。
- **全部增量**：均为 MINOR；`track_id` 与 5 类标签零破坏。

---

## 5. 后果（Consequences）

**正面**：稳定身份扩展点；Memory/Profile 去重正确；不破坏冻结。

**负面 / 约束**：`IdentityResolver` 有错合/错分风险，需阈值 + 家属确认（与 P0-11 闭环契合）；v1 仅 session 级，跨天仍为 None。

---

## 6. 替代方案（Alternatives）

- **`track_id` 直接当 `person_id` 建 Memory**：否决。会话级闪烁导致身份碎片化（ADR-0006/0020）。
- **v1 即承诺 `person_identity_id` 真实身份**：否决。无 ReID/Memory，"真实身份"是伪命题，会污染下游判断。
- **新增 `EventType` 表达身份状态**：否决。身份是元数据不是访客语义标签。

---

## 7. 与既有 ADR 的关系

- **ADR-0020**：本 ADR 是其**具体实现**——三层概念 + `IdentityResolver` + SessionMerge v1。
- **ADR-0006**：`track_id` 会话级前提；本 ADR 在其上叠加长期层。
- **ADR-0014**：全部 MINOR（§3.5），不破三级冻结；新接口走 L2 登记。
- **ADR-0021 / ADR-0022**：实时信号与证据可带 `visitor_instance_id`/`person_identity_id`，身份解析在 Phase 4 才影响 `person_identity_id` 非 None。
