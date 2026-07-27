---
name: high-not-appearing
title: HIGH 不出现：不是模型问题，是时间尺度错配
category: Failure Case
phase: 1
root_cause: 时间语义建模
refs:
  - scripts/e2e_validate_demo.py  (L79-84 注释)
  - src/silver_demo/sources.py::VideoFileFrameSource  (fps_target→skip)
  - src/silver_demo/gateway.py::run_loop  (frame_loop_interval_s vs fps_target)
---

# 案例：HIGH 风险不出现（时间尺度错配）

## 现象

- 场景配置明明应该稳定触发 HIGH（重复访问 → HIGH + 家属/社区指令），但整段跑完**一个 HIGH 都没有**。
- 调试时检测框正常、目标追踪正常、帧流正常——就是 `visits_in_window` 永远等于 1，规则阈值（如 `repeat_visit_count=2`）永远不达标。
- 第一直觉：「YOLO 没检测到人」「规则配错了」「模型退化」——全部错误。

## 根因

系统里有**两把独立的时间尺子**，被混为一谈：

1. **源采样尺子**（`scenario.fps_target`）→ 决定从视频里「抽哪些帧」：`skip = round(src_fps / fps_target)`。
   它是**取样的密度**，影响「哪些时刻被看到」。
2. **演示时间尺子**（`DemoClock`，每帧推进 `frame_interval_s`）→ 决定「模型认为现在几点」，是规则窗口（`frequency_window_s`，默认 1800s）的计时基准。
3. **播放速度尺子**（`DemoSettings.frame_loop_interval_s`）→ 决定真实跑一帧 sleep 多久，是**实时观感**，不影响 demo-time 语义。

陷阱操作：为了「全速跑」，把 `scenario.fps_target` 设成 `0`。
`sources.py` 里 `fps_target=0 → skip=1`，于是**读出全部原始帧**，且失去了正确的「重入间隔」建模——
demo-time 中同一访客的两次出现被拉得比 `frequency_window` 还远，特征抽取器判定「两次访问不在同一窗口内」，
`visits_in_window` 恒为 1，规则永不触发，HIGH 永不出现。

> 原始注释（scripts/e2e_validate_demo.py）：
> 「绝不能把 scenario.fps_target 改成 0 —— 那会让 skip=1 读全帧，
> demo-time 重入间隔被拉宽超过 frequency_window → visits_in_window 永远=1 → 不出 HIGH。」

## 错误假设

> 「想跑快点就把抽帧率调成 0（不抽帧），反正播放速度由 sleep 控制。」

错。`fps_target` 不是「播放速度」，它是**源时间轴的采样网格**。
把它归零等于把「模型感知时间」的网格打散，规则依赖的时间窗口随之失效。
播放速度另有专门的旋钮（`frame_loop_interval_s`）。

## 修复

把两把尺子分开调：

- **源采样尺子保持正确**：`scenario.fps_target = 8`（使 `skip = src_fps/8`，重入间隔落在 `frequency_window` 内，RepeatVisit 能触发）。
- **播放速度尺子负责快**：`DemoSettings.frame_loop_interval_s = 0.001`（小正数）→ 网关 `run_loop` 跳过 `1/fps_target` 限速回退，全速跑（YOLO 推理主导，GPU ~30ms/帧）。

```python
# gateway.run_loop 里的区间选择（已正确解耦）
interval = self.demo_settings.frame_loop_interval_s
if interval <= 0 and self.scenario.fps_target > 0:
    interval = 1.0 / self.scenario.fps_target   # 仅在未显式限速时回退到源速率
```

**铁律：`fps_target` 永远不要动；要调速只动 `frame_loop_interval_s`。**
这条约束已写进 E2E 脚本的参数 help 文案，作为永久提醒。

## 适用

凡是存在「模拟时间 / 事件时间 / 墙钟时间」三者分离的系统：

- 视频分析、仿真回放、IoT 时序、任何带 `frequency_window` / `sliding window` / `session timeout` 的规则引擎。
- 抽象原则：**源采样率、业务时间轴、播放/处理速度，是三个正交维度。改「快」只动速度旋钮，绝不动时间轴的采样网格。**
- 关联：`05-Demo-Engineering/scenario-management`（确定性触发靠正确建模时间，而非碰运气）。
