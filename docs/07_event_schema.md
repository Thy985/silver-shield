# 07 · 事件 Schema（VisitorEvent）

> 与《架构设计完善版》"门前风险输出"标签、核心数据对象 `VisitorEvent` 对齐。
> 本模块**只输出标签/事件**，不直接输出"诈骗人员"结论。

## 7.1 事件类型枚举（EventType）

| 取值 | 含义 | 风险倾向 |
| --- | --- | --- |
| `visit_normal` | 普通来访（白名单/已知访客） | 低，仅记录 |
| `visit_pending_verify` | 待核验来访（非白名单陌生访客出现在门前） | 中，提示家属核验 |
| `abnormal_dwell` | 异常停留（门前停留超阈值） | 中，疑似等待/观察 |
| `repeat_visit` | 重复来访（短时内多次出现，疑似踩点） | 中→高，持续接触信号 |
| `high_risk_approach` | 高风险接近（尾随/反复靠近又离开/强行靠近） | 高，安全风险上升 |

> `is_odd_hour` / `repeat_count` 为叠加标记，可与上述任一类型组合，用于中心加权，
> 但**不改变事件类型本身**（类型必须是可枚举的稳定标签）。

## 7.2 PerceptionEvent 字段

```json
{
  "device_id": "home_entry_01",
  "event_type": "repeat_visit",
  "score": 0.72,
  "timestamp": 1718000000.123,
  "track_id": 17,
  "bbox": [120, 80, 260, 420],
  "location": "入户门",
  "repeat_count": 4,
  "is_odd_hour": true,
  "evidence": [
    { "kind": "snapshot", "uri": "data/evidence/2026-06-10/17-abcd.jpg", "timestamp": 1718000000.0 },
    { "kind": "clip",     "uri": "data/evidence/2026-06-10/17-abcd.mp4", "timestamp": 1718000000.0 }
  ],
  "meta": {
    "dwell_s": 512,
    "enter_at": 1717999488.0,
    "leave_at": 1718000000.0,
    "rule": "RepeatVisitRule"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `device_id` | str | ✅ | 设备内部 ID（来自 devices.yaml） |
| `event_type` | enum | ✅ | 见 7.1 |
| `score` | float 0~1 | ✅ | 本模块侧风险置信度（规则/ML 组合） |
| `timestamp` | float | ✅ | 事件发生的 Unix 秒 |
| `track_id` | int? | — | 跟踪 ID，用于跨帧/跨次关联 |
| `bbox` | [x1,y1,x2,y2]? | — | 像素坐标，取证定位用 |
| `location` | str? | — | 安装区域（入户门/客厅…） |
| `repeat_count` | int? | — | 短时内同一访客出现次数 |
| `is_odd_hour` | bool | — | 是否处于异常时段（夜间/独处） |
| `evidence` | EvidenceRef[] | — | 取证引用（仅中高风险或受指令时填充） |
| `meta` | dict | — | 规则名、时长等可追溯上下文 |

## 7.3 EvidenceRef

| 字段 | 说明 |
| --- | --- |
| `kind` | `snapshot`（截图）或 `clip`（短片段） |
| `uri` | 本地相对路径或对象存储 URL（COS） |
| `timestamp` | 取证时间戳 |

## 7.4 取值约束（强制）

- `event_type` 必须是 7.1 枚举之一；**禁止出现 `fraud` / `scammer` 之类结论性标签**。
- `score` 仅表示门前信号强度，**不等于最终诈骗概率**；最终概率由中心综合判定。
- 证据仅在 `visit_pending_verify`（中）、`abnormal_dwell`（中）、`repeat_visit`/`high_risk_approach`（高）
  时采集；`visit_normal` 默认不存像素。
- 截图/片段中出现的**无关路人、门牌/银行卡等敏感区域应遮挡/模糊**后再存（隐私合规）。
