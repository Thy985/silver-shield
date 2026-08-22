# ADR-0039: Runtime Entry Contract（RuntimeFrameContext 单容器进给）

- 状态：Accepted（Owner 于 2026-08-22 ADR Preflight Review 拍板 Option B）
- 日期：2026-08-22
- 决策者：Owner
- 相关：
  - `docs/reports/ADR-PREFLIGHT-REVIEW-2026-08-22.md`（Q1 论证全文）
  - `docs/reports/AUDIO-RISK-RUNTIME-AUDIT-CORRECTION-2026-08-22.md`（audio runtime 入口缺失取证）
  - ADR-0021（RiskSignal / FrameResult.risk_signals 字段）、ADR-0026（AudioPerceptionEvent）、
    ADR-0041（signal 级时间对齐——本 ADR 的 case_time 显式化是其时钟统一前置条件）、
    AGENTS.md §1.2（单一职责）、§1.3（显式配置注入）

---

## 背景（Context）

当前 `Pipeline.process_frame(frame, frame_index)` 仅接收视觉输入；音频走独立的
`gateway._feed_live_audio` 只做 evidence 投影，完全不进 risk 链。
`adapt_audio_event`（integration/audio_adapter.py）桥接已就绪但 runtime 入口未通电。

同时 `FrameResult` 已是多模态输出容器（含 `risk_signals` 字段）——runtime 层早已按
"dataclass 进出"思路设计输出侧，仅入口侧未对齐。

若以逐参扩签方式接入音频（`process_frame(frame, frame_index, audio_events=...)`)，
未来每加一种模态都要改签名，违反开闭原则；且 `case_time` 在 gateway 内三处重复计算
（frame_index × frame_interval_s），无单一事实来源。

### Owner 修订意见（2026-08-22）

> 不要在 Context 里过早预留一堆 `thermal_events / imu_events / door_sensor_events` 字段。
> 否则今天的「不要把每种模态硬塞进函数签名」会变成明天的「把所有模态硬塞进 Context」。
>
> **Context 是扩展边界，不是无限字段垃圾桶。**

## 决策（Decision）

引入 `RuntimeFrameContext` frozen dataclass 作为 `Pipeline.process_frame` 唯一入参：

```python
@dataclass(frozen=True)
class RuntimeFrameContext:
    video_frame: Any | None          # np.ndarray 或 None（纯音频帧场景）
    frame_index: int
    case_time: float                 # frame_index * frame_interval_s，显式化
    audio_events: tuple[AudioPerceptionEvent, ...] = ()
```

约束：

1. **只含上述四字段**。不预留 thermal / imu / door_sensor 等任何未来模态占位字段；
   第二种非音视频模态真正进入 Runtime 时，再通过新 ADR 扩展 Context；
2. `process_frame(ctx: RuntimeFrameContext) -> FrameResult`——输入输出均为单一 dataclass，
   与 `FrameResult` 对称；
3. `case_time` 为显式字段，gateway 删除三处重复计算，成为时钟域唯一来源
   （ADR-0041 时钟统一的前置条件）；
4. 旧签名保留一个版本周期作 deprecated 别名，随后移除；
5. schema 测试钉死字段集合（新增/删减字段必须炸测试并走 ADR）。

## 动机（Rationale）

- **Runtime 输入模型统一**：解决的是"Runtime 输入是什么"，而非"给函数多塞一个参数"；
- **OCP**：新模态扩展走 ADR 改 Context，不改方法签名与全部调用方；
- **对称设计**：与 `FrameResult`（out）构成 in/out 容器对，架构一致；
- **迁移成本最低**：`process_frame` 全仓调用点仅 gateway.py 一处；
- **确定性**：ctx 显式携带全部输入，VM-8 重放幂等不依赖隐藏缓冲。

## 后果（Consequences）

### 正面
- 多模态扩展有明确边界（Context = 扩展边界），签名稳定；
- `case_time` 单一事实来源，为 signal 级时间对齐（ADR-0041）铺路；
- 测试可独立构造 ctx，无需真实视频帧即可测纯音频帧路径（`video_frame=None`）。

### 负面 / 约束
- gateway 单点改造 + 旧签名过渡期维护；
- `RuntimeFrameContext` 成为新公共契约，需配套 contract test；
- 未来加模态必须走 ADR（有意为之的摩擦，防字段垃圾桶化）。

## 替代方案（Alternatives）

| 方案 | 描述 | 否决原因 |
|------|------|---------|
| A：关键字参数扩签 | `process_frame(frame, frame_index, audio_events=())` | 违反 OCP；参数随传感器数量线性增长 |
| C：双入口 | 保留原签名 + 新增 `process_frame_with_audio` | 组合爆炸；调用方混乱；违反 Pipeline 单一编排 |
| D：内部缓冲队列 | Gateway 缓存 audio，下次 process_frame 自动消费 | 时序错配不可定位；破坏 VM-8 确定性重放 |
| 过早预留扩展位 | ctx 内预置 thermal/imu/door 空字段 | Owner 明确否决：Context 是扩展边界，不是无限字段垃圾桶 |

## 与既有 ADR 的关系

- **ADR-0021**：`FrameResult.risk_signals` 已预留，本 ADR 补齐入口侧对偶；
- **ADR-0041**：`case_time` 显式化是 signal 级时钟统一的锚点；
- **AGENTS.md §1.2/§1.3**：符合单一职责与显式依赖原则。