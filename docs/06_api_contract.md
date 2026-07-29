# 06 · 接口契约（与中心的对接口）

> 本模块是 **Home 端的事实生产者**。以下契约需与 AI 中心（Understand/Predict）及
> 业务服务（Spring Boot）共同确认；变更须升版本并经评审。

## 6.1 传输方式

- **默认 MQTT**（轻量、适合边缘↔中心）：topic `silvershield/home/{device_id}/events`。
- **回退 HTTP POST**：`POST /api/v1/perception/events`，JSON body 同下。
- 中心不可达时本地环形缓冲（`output.buffer`），恢复后补发，保证事件不丢。

## 6.2 消息信封（Envelope）

所有上报消息统一信封，便于中心路由与去重：

```json
{
  "schema_version": "1.0",
  "source": "home_perception",
  "device_id": "home_entry_01",
  "sent_at": 1718000000.123,
  "events": [ "<PerceptionEvent>", "..." ]
}
```

## 6.3 反向通道（中心 → 本模块）

- **白名单 / 已知访客回写**：中心判定某 track_id 为家属/已知后，下发"可信访客"标签，
  本模块后续对该 track_id 不再产生 `visit_pending_verify`。
- **阈值动态下发**（可选，增强版）：中心按 RiskTwin 个性化阈值回写停留/频次阈值。
- 实现方式：订阅 `silvershield/home/{device_id}/control`（MQTT）或轮询业务接口。

## 6.4 与中心数据对象映射

| 本模块输出 | 中心对象 | 说明 |
| --- | --- | --- |
| PerceptionEvent(门前标签) | `VisitorEvent` | 进入/离开时间、停留、重复次数、确认标签 |
| EvidenceRef(片段 URI) | `WarningEvent.evidence` | 触发中高风险时保存的必要片段 |
| track_id + 出现频次 | RiskTwin.近期事件 | 支撑重复来访与趋势 |
| is_odd_hour / repeat_count | RiskTwin.动态风险因子 | 个性化加权 |

## 6.5 设备状态事件

复用萤石平台事件订阅（门铃、人形、上下线），转换为本模块内部状态，
并作为 `device.status` 事件上报中心（在线/离线/取流失败），支撑处置闭环的健康判断。

## 6.6 兼容性约定

- 字段**只增不改**：新增可选字段不影响旧消费者；删除/改语义必须升 `schema_version` 并通知中心。
- 时间统一用 Unix 秒（浮点，毫秒精度）；时区以设备本地时区记录，中心做归一。
- 所有字符串标签取值限定在 `07_event_schema.md` 枚举内，禁止自由文本当标签。
