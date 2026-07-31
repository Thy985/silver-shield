# ADR-0006: YOLO track_id 封装为银龄盾自己的 VisitorTrack 领域对象（P0-5）

- 状态：Accepted
- 日期：2026-07-19
- 决策者：Owner
- 相关：`docs/08_roadmap.md`（P0-5）、`src/home_perception/detection/{detector,tracker,schemas}.py`、
  ADR-0001（只产事实）、ADR-0003（imgsz=480 实时预算）

## 背景（Context）

P0-3/P0-4 后，每帧 YOLO 独立推理，无法回答"是否为同一个人"——而门前踩点识别（MVP 创新点）依赖
`Person ID=001 @ 09:10 enter / 09:15 leave / 09:20 enter …` 的连续性。需要跨帧关联。

同时，ultralytics 的 `model.track()` 已内置 ByteTrack / BoT-SORT 并直接回填 `track_id`，
因此本模块**不需要自研跟踪器**；真正的工程价值在于：把 YOLO 的 frame-level `track_id`
**提升为银龄盾可理解的访客生命周期对象**，并与后续 P0-6 的 `VisitorEvent` 衔接。

## 决策（Decision）

1. **跟踪算法 = ByteTrack**（MVP）。理由：固定摄像头 / 单区域 / CPU 部署 / 人员停留分析场景，
   ByteTrack 足够；BoT-SORT 的 ReID 价值（人离开又回来、长时间遮挡、多摄像头）不在 MVP 范围
   （见 Owner P0-5 决策）。
2. **`model.track(persist=True)`**：保证跟踪器在多次 `detect()` 调用间保持内部状态；
   **`YOLODetector` 实例必须在相机循环里复用**，不得每帧重建（否则 `track_id` 不断重置）。
3. **封装领域对象**：新增 `detection/schemas.py` 的 `VisitorTrack` + `detection/tracker.py` 的
   `VisitorTracker`，把 YOLO `track_id` 转换为银龄盾自己的**访客在场状态**（active/left、
   进入/最近时间、累计帧数、最近 bbox/置信度）。
4. **`VisitorTrack` 只代表当前摄像头会话内的同一人，不做跨天身份保持**。跨天重识别（VisitorFeature /
   VisitorHistory / 外观 embedding / 人工确认）属 P0-6 / P1，不在本层引入。
5. **`VisitorTracker` 职责单一**：只维护在场/离场状态，不做风险判断、重复访问判断、陌生人判断
   （那些是 analysis 层 P0-6 / P1 的事）。离场判定用 `absence_gap_s` 兜底检测器偶发漏检的 ID 闪烁。

## 动机（Rationale）

- **重点不是"调 ByteTrack"，而是领域抽象**：把帧级输出提升为银龄盾可理解的访客生命周期，
  使后续 `VisitorEvent`、`repeat_visit`、`long_duration` 等可直接消费，而不依赖 YOLO/ultralytics 细节。
- **`persist=True` 是 P0-5 核心**：否则跨帧无法关联，整个"连续身份"目标落空。
- **ByteTrack 契合 MVP**：轻量、无 ReID、CPU 友好；不为 MVP 不需要的多摄/长遮挡能力承担复杂度与算力。
- **不提前做跨天身份**：避免引入 embedding/特征库等重依赖与隐私负担，符合 ADR-0001 的边界与 MVP 可部署性。

## 后果（Consequences）

- ✅ 本模块产出"访客在场状态"这一稳定领域对象，P0-6 的 `VisitorEvent` 可直接由它生成。
- ✅ 跨帧 `track_id` 一致（真实 `person.jpg` 链路 + 纯单测双重验证）；开启 ByteTrack 推理开销 ≈ 0ms。
- ✅ 边界清晰：`VisitorTracker` 不含任何风险语义，符合 ADR-0001 的"只产事实"。
- ⚠️ `track_id` 仅当前摄像头会话内有效；跨天/跨设备重识别需 P0-6/P1 单独建设。
- ⚠️ 遮挡降级可能导致 ID 跳变（属可接受范围）；`absence_gap_s` 用于容忍漏检闪烁，需按实测调参。
- 📌 约束后续：改跟踪算法（如切 BoT-SORT）或引入跨天身份，须新开 ADR 并评审。

## 替代方案（Alternatives）

- **BoT-SORT（带 ReID）**：否决（MVP）。ReID 的多摄/长遮挡价值不在当前场景，且增加算力与复杂度。
- **自研跟踪器 / ReID**：否决。ultralytics 已内置且够用，自研超出 MVP 范围、违背"不过度设计"。
- **直接在 analysis 里消费 YOLO track_id，不做领域封装**：否决。会让上层耦合 YOLO 细节，
  `VisitorEvent` 难以稳定定义；领域封装正是本 ADR 要固化的价值。
- **每帧 new YOLO() 再 track**：否决。`track_id` 会每帧重置，跨帧关联彻底失效。
