---
name: pipeline-state-pollution
title: 循环播放后无 warning：Tracker 状态未 reset
category: Failure Case
phase: 1
root_cause: 跨帧状态生命周期
refs:
  - src/silver_demo/gateway.py::_rebuild_pipeline  (L345)
  - 02-Code-Patterns/lifecycle-management
---

# 案例：Pipeline 状态污染（循环播放后区域变空白）

## 现象

- Demo 第一次循环播放：风险卡（②③区）、行为时间线正常出现。
- 切到下一轮循环 / 切换场景重放后：**区域 ②③④ 变为空白**，没有新的 warning，没有行为事件。
- 直觉以为是「前端没刷新」「WS 断了」或「模型没检测到」——但日志显示 pipeline 仍在正常产出帧和检测框。

## 根因

流水线里存在**跨帧累积的隐式状态**，它们活在「帧」之外，活在「组件实例」之内：

- 目标追踪器（`VisitorTracker`）：track_id 与历史轨迹绑定。
- 特征抽取器（`FeatureExtractor`）：维护 `frequency_window`（如 30 分钟）内的访问计数。
- 规则引擎（`RuleEngine`）：重复访问计数、odd_hour 窗口。
- 决策引擎（`DecisionEngine`）：已触发风险去重。

这些状态在第一轮循环里被「填满」了。第二轮直接复用同一批组件实例时，它们认为「一切都已经见过」——
新一轮的同一访客被当成「旧轨迹延续」，访问计数早已超过阈值且被去重，于是**不再产生任何新 warning**。
本质上：组件被复用，但组件内部的「时间起点」没有被重置。

## 错误假设

> 「detector 复用就够了，循环重放只是把帧重新喂一遍，状态会自然结束。」

错。帧是流，但**追踪/计数/窗口状态是有记忆的累加器**，不会因为「喂新的一轮帧」而自动归零。
「复用模型权重」和「重置分析状态」是两件事，必须显式分开处理。

## 修复

引入 `_rebuild_pipeline(scenario)`：每次循环重启 / 切换场景时，**重建状态型组件、复用无状态型组件**。

```python
def _rebuild_pipeline(self, scenario):
    # ① 重建 DemoClock —— 模拟时间回到 scenario.start_time
    self.clock = DemoClock(start=scenario.start_time, interval_s=scenario.frame_interval_s)
    # ② 重建 pipeline，但复用已加载的 detector（避免重新加载 YOLO 权重）
    self.pipeline = PerceptionPipeline.from_settings(
        self.hp_settings,
        detector=self.pipeline.detector,   # 关键：detector 实例复用
        device_id=scenario.source,
        now_provider=self.clock,
        frame_interval_s=scenario.frame_interval_s,
    )
    # ③ 重建后重应用场景级规则覆盖（rule_overrides）
    self._apply_scenario_rule_overrides()
```

要点：**重建 `PerceptionPipeline` 与 `DemoClock` 即可清空 Tracker/FeatureExtractor/RuleEngine/DecisionEngine 的内部累加状态**，因为这些都是 pipeline 的组成部分；而 `detector` 作为入参传入、实例不变，保证 track_id 一致性（`model.track(persist=True)` 要求同实例）。

`reset_demo()` 直接复用 `switch_source(同场景)`，零新增代码路径。

## 适用

凡是满足以下条件的系统，都会踩：

- 有「目标追踪 / 滑动窗口 / 访问计数 / 去重」等**跨帧累加状态**；
- 又支持「循环重放 / 场景切换 / 复位重启」复用同一运行时。

抽象原则：**「复用模型」≠「复用分析状态」。任何有记忆的组件，在生命周期边界（循环/场景/会话重启）必须显式重置，且重置点要和「加载重资产（模型权重）」解耦。**
对应可复用模式见 `02-Code-Patterns/lifecycle-management`（RuntimeSession 骨架的 `_rebuild_pipeline`）。
