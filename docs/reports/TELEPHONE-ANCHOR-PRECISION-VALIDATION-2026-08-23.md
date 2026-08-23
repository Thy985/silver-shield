# Telephone Anchor Precision Validation · hard-negative 验证报告

- **日期**：2026-08-23
- **性质**：数据集动作 + 只读取证验证（合成素材不入库；**零 src 变更**）
- **Owner 指令**：建立 `telephone_persistent + normal_speech` 等 hard-negative 组合，重新验证锚点 precision；达标后才值得冻结 Gate I 的 N/T/window
- **守护命题（Owner 定调）**：**「电话存在」不是「风险存在」**——`telephone_persistent alone → MONITOR`，不得升级

---

## 1. 结论速览

| 验证面 | 结果 | 达标 |
| --- | --- | --- |
| 端到端升级安全（`tel alone → MONITOR` 不升级） | 18 个事件全部 MONITOR，零 RAISE/NOTIFY/ESCALATE | ✅ **PASS** |
| 锚点 precision（Tier0 感知层） | **50%**（3 TP / 1 suspicious / 2 FP） | ❌ 未达标 |
| 锚点召回在典型通话形态下 | **hn2 形态完全丢失锚点；hn1 形态被 cry 假阳性覆盖通话区间** | ❌ 结构性盲区 |
| negative/hard-negative 回归 | normal→9 cry、ambient→tel、micro→2 cry、短 stress→tel 全部复现 | ❌ |

**一句话**：「电话存在 ≠ 风险存在」在**决策层**已经由结构保证（ceiling 双保险实证通过）；但在**感知层**远未成立——锚点事件一半是假的，且恰恰在最典型的「通话中有人说话」形态下失效。Gate I 参数冻结继续挂起。

---

## 2. 验证集设计

### 2.1 合成 hard-negative 集（ffmpeg amix，`normalize=0` 保电平；16k PCM16 mono）

| 素材 | 配方 | 产品语义 |
| --- | --- | --- |
| `hn1_tel_normal_full.wav` | tel(-6dB) + voice_normal(0dB) 同起 | 老人边打电话边说话（同起形态） |
| `hn2_tel_normal_partial.wav` | tel(-6dB) 全程 + voice_normal(0dB) 延迟 6s 进入 | 通话中段开始交谈（渐进形态） |
| `hn3_tel_ambient.wav` | tel(-4dB) + ambient(-10dB) | 正常通话 + 底噪 |
| `hn4_tel_micro.wav` | tel(-4dB) + micro_events(-8dB) | 正常通话 + 身体摩擦 |
| `hn5_normal_alone.wav` | voice_normal 原样 | negative 对照 |

### 2.2 回归组（已知缺陷复现）

`telephone_persistent_16k`（正对照）、`ambient_living_room_16k`、`tts_raw/seg4_stress`、`micro_events_16k`。

### 2.3 取证方法

与审计同款 pipeline 调用面（`AudioDetector.detect` → 逐段 `extract` → `AudioRule.evaluate` 默认阈值）。TP/FP 口径：**不含 tel 层的素材产出任何 tel 事件即 FP**（含 <0.5s 微段）；含 tel 层素材的长段 tel 为 TP，<0.5s 微段单列 suspicious。

---

## 3. 结果矩阵

| 素材 | 期望 | 实际 Tier0 输出 | 判定 |
| --- | --- | --- | --- |
| telephone_persistent_16k | 仅锚点 | `2.12-15.00 tel` | ✅ TP |
| **hn1** tel+normal 同起 | 仅锚点 | `1.42-1.62 tel`(0.2s 微段, suspicious)；**`1.96-15.00 distress_cry`** | ❌ 锚点区间被 cry 塌缩整体覆盖 |
| **hn2** tel 全程+normal 中段 | 仅锚点 | **`2.12-15.00 (none)`** | ❌ 锚点完全丢失（召回崩塌） |
| hn3 tel+ambient | 仅锚点 | `2.12-15.00 tel`，无其他 kind | ✅ PASS |
| hn4 tel+micro | 仅锚点 | `2.12-15.00 tel`，无其他 kind | ✅ PASS |
| hn5 normal 单独 | 无事件 | 9 × distress_cry | ❌ 已知塌缩基线（P2） |
| ambient 底噪 | 无事件 | `0.00-15.00 tel` | ❌ P1 复现 |
| seg4_stress | 无事件 | `0.62-0.90 tel`（0.28s 段） | ❌ P1 复现 |
| micro_events | 无事件 | 2 × distress_cry | ❌ P2 复现 |

**统计**：anchor precision = 3/(3+1+2) = **50%**；其他 kind 假阳性 12（全部 distress_cry）。

## 4. 端到端升级安全验证（PASS）

将全部 18 个感知事件按时间序喂入 `RealTimeAudioRiskEvaluator`（默认 `AudioEvidenceConfig()`：N/T/M=None 升级零可达 + `ceiling_monitor_only=True`）：

```text
strength distribution: {'monitor': 18}
ceiling check: PASS (all <= MONITOR)
```

即使感知层 12 个 cry 假阳性 + 3 个 tel 误报全部发生，决策输出仍为零升级。**Owner 核心验收命题（`telephone_persistent → Audio RiskSignal → DecisionInput → MONITOR`，而不是 `RAISED → NOTIFY_FAMILY`）在当前门控下实证成立。**

---

## 5. 缺陷分层与新发现

### 缺陷 A（P1 · 锚点 precision，已量化）

| 误报源 | 复现 | 机制 |
| --- | --- | --- |
| ambient 底噪 → tel（15s 整段） | 本次 ✅ | 能量 VAD 无绝对能量下限 |
| 短 stress 段（0.28s）→ tel | 本次 ✅ | tel 分支无最短持续时间校验 |
| hn1 微段（0.2s）→ tel | 新增样本 | 同上（0.2s 人声瞬态 narrow+rate<0.8 命中电话分支） |

### 缺陷 B（新发现 · 锚点召回的结构性盲区）⚠️ 本报告最重要发现

**「正在通话 + 老人在说话」恰是电话诈骗场景的典型形态**（老人与骗子交谈）。本验证证明当前锚点判据在该形态下双向失效：

- **hn1 形态（同起）**：mix 后 VAD 将人声与 tel 并为单一长段（1.96-15.00），段内特征被人声主导 → crying 三条件命中 → **cry 假阳性直接骑在整个锚点区间上**；
- **hn2 形态（中段进入）**：人声抬升 speech_rate 使 `rate<0.8` 失效 → telephone 分支不命中 → **锚点归零**。

这不是阈值问题而是判据设计问题：`narrow & rate≈0` 描述的是「无人声干扰的持续窄带音」（≈**铃音/免提漏音阶段**），而「老人正在与人通话」必然破坏该条件。可选修复方向（**需 Owner 拍板，属机制设计**）：

1. **判据重构**：telephone 分支引入「窄带能量持续占比」维度（窄带成分在段内的持续时间比例），允许叠加宽带人声后仍可判锚点；
2. **产品语义收窄**：接受锚点只描述「铃音/免提持续音阶段」，交谈阶段的通话证据改由 far_end_speech（远端带限人声，宽带逃逸特性天然不受 cry 塌缩影响）承担 corroborating 角色——与 Evidence Matrix §3.4 的 v2 备忘衔接；
3. **两者结合**：近端窄带持续（判据 1）∨ 远端人声存在（far_end 特征）⇒ 「通话进行中」复合事实。

### 缺陷 C（P2 · cry 语义塌缩，持续存在）

12 个 cry 假阳性横跨 hn1/hn5/micro。因 distress_cry 已定 perception-only（不进 Policy），不阻塞锚点修复，但污染 UI/Memory 语义，仍需按审计 §6.1-P2 修复（tremor 重定义等）。

---

## 6. P1 修复 Task Contract 草案（待 Owner 审批后实施）

> 按 AGENTS §9.2 四问格式。Risk: Medium（Tier0 规则机制变更，触及判级行为）。

1. **What changes?**
   - `vad.py`（EnergyVadBackend）：新增绝对能量下限参数（候选 0.02~0.03 rms，最终值以本验证集扫描定标），低于阈值的原始帧不计入语音段；
   - `rule.py`（RuleThresholds + evaluate）：telephone 分支新增最短持续时间约束（候选 ≥2.0s，可配置），消除 0.2~0.3s 微段/短段 tel；
   - 配套单测：`tests/` 新增 ambient/seg4/hn 微段三个回归用例（红→绿）。
   - **不含**缺陷 B 的判据重构（另行 ADR/Owner 讨论）；**不含** tremor/narrow（P2）。
2. **How to verify?**
   - 本验证集（5 合成 + 4 回归）重跑取证脚本：预期 ambient/seg4/micro/hn5 零 tel/cry 产出；hn1 微段消失；hn3/hn4/tel_alone 保持 TP；
   - 全量 pytest + ruff 通过；2295 基线不回退。
3. **What feedback signals exist?**
   - 成功：anchor precision 50% → 100%（本集合口径：零 FP_anchor/suspicious）；失败信号：tel_alone/hn3/hn4 召回丢失（阈值定标过严的反向指标）。
4. **What is done?**
   - 上述验证集全绿 + 测试入库 + 报告 v2 更新。Gate I 重跑前提（§7）届时重新评估。

## 7. Gate I 参数冻结路径（更新）

```text
① P1 修复落地（Task Contract 待批）→ 本验证集重跑达标
        ↓
② 缺陷 B 方向拍板（判据重构 or 语义收窄 or 结合）→ 通话交谈形态锚点可靠
        ↓
③ （并行可做）hard-negative 集扩充：long_duration / different speaker /
   handset / speakerphone / TV-background speech（变体树其余分支）
        ↓
④ Gate H v2（分布统计在新语义上重建）
        ↓
⑤ Gate I v2（N/T/window 七配置 + 三层验收场景）
        ↓
⑥ 冻结 telephone_risk Decision Contract
```

## 8. 卫生说明

合成素材位于 `dataset/_canonical/audio_mix/telephone_risk/hardneg/`（gitignore 覆盖，可由本报告配方再生成，不入库）；取证脚本与 JSON 为一次性工具随 PR 清理。本报告为唯一入库产物。