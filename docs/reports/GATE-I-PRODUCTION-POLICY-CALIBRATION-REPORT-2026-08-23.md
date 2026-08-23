# Gate I · Production Policy Calibration 报告（2026-08-23）

> **性质**：Owner 指定的小而严参数网格评测。只对**既有机制**调参评测，
> 不为触发率改任何机制/架构（红线遵守，见 §7）。产品配置零改动。
>
> **方法**：真实推理采集一次（YAMNet ONNX 音频事件流 17 资产 + YOLO11n CPU
> vision RAISED 时间线 10 配对），随后 7 组参数配置**纯离线重放**（同一事件流
> 喂真实 `RealTimeAudioRiskEvaluator`；combined 链经裸 `PerceptionPipeline`
> 实例直调真实 `_synthesize_combined_risk`——同 classify/link 纯函数 + 同
> 配对即消费语义，非重写实现）。确定性可复现。

## 1. 网格配置（Owner 指定）

| 组 | N (raise_min_count) | T (raise_window_s) | window (temporal) | 定位 |
| --- | --- | --- | --- | --- |
| baseline | 1 | None | 2.0 | Gate G 验收态锚点 |
| A1 | 2 | 4 | 2.0 | 敏感性对照 |
| **A2** | **3** | **4** | **2.0** | 生产候选 |
| **A3** | **3** | **5** | **2.0** | **首测候选** |
| B1 | 3 | 4 | 1.0 | temporal 对照 |
| B2 | 3 | 5 | 1.0 | temporal 对照 |
| C1 | 2 | 4 | 1.0 | temporal 对照 |

M（notify_min_kinds）= None 恒定（Owner 拍板维持不可达）。

## 2. 真值锚点（指标定义基础）

| 锚点 | 资产 | 语义 |
| --- | --- | --- |
| expect_raise | case_b_mix（canonical+embed）、voice_stressed_16k | 压力相位存在，stress 相关 RAISE 合理 |
| forbid | case_a 系列、voice_normal、ambient、far_end、micro_events、repeated_visit ×3、stranger_visit | 无风险真值 → RAISE+ 即 **false_raise** |
| forbid_tel_only | telephone_persistent_16k | 电话本身非风险（manifest 原则 #1）→ 单独 RAISE 即「电话一响就升级」红线 |
| ambiguous_insufficient | act_a/b/c_mix | 证据不足场景，单列审阅不计 false |

## 3. 主结果矩阵

| 组 | RAISE | FALSE（tel-only） | MISSED | SINGLE-EV | COMBINED | NOTIFY |
| --- | --- | --- | --- | --- | --- | --- |
| baseline (N=1) | 65 | **7**（**1**） | 0 | — | **4** | 0 |
| A1 (N=2,T=4) | 51 | 4（0） | 0 | 0 | 3 | 0 |
| A2 (N=3,T=4) | 38 | 3（0） | 0 | 0 | **0** | 0 |
| A3 (N=3,T=5) | 42 | 3（0） | 0 | 0 | **0** | 0 |
| B1 (N=3,T=4,w1.0) | 38 | 3（0） | 0 | 0 | 0 | 0 |
| B2 (N=3,T=5,w1.0) | 42 | 3（0） | 0 | 0 | 0 | 0 |
| C1 (N=2,T=4,w1.0) | 51 | 4（0） | 0 | 0 | 0 | 0 |

- **MISSED=0 全线**：expect_raise 资产在所有配置下均有 stress 相关 RAISE（无漏报）。
- **SINGLE-EV=0（N≥2 全线）**：判级持续性维度生效——不存在单事件升级。
- **NOTIFY=0 全线**：M=None 结构性不可达，验证通过。
- **fallback_raise=0 全线**：`audio_anomaly_other` 零升级，双保险第二道有效。
- **「电话一响就升级」被 N≥2 消除**：tel-only false 从 baseline 的 1 次降为 0。

## 4. 发现 1（最重要）：N–T–window 耦合效应，N=3 与 ESCALATE 在候选窗内互斥

case_b 系列的 RAISE 时刻随 N 推移，与 vision RAISED（t=0，人物入场）的距离
随之拉大：

| 配置 | N | case_b RAISE 时刻 | Δt to vision | w=2.0 配对 | combined |
| --- | --- | --- | --- | --- | --- |
| baseline | 1 | 1.26s | 1.26s | ✓ | 4 |
| A1/C1 | 2 | 1.96s | 1.96s | ✓（**余量 0.04s**） | 3 |
| A2/A3/B1/B2 | 3 | 2.66s | 2.66s | ✗ | **0** |

**机制**：N 持续性维度要求第 N 个同 kind 事件到达才 RAISE；distress_cry 到达
间隔 P50≈0.92s（Gate H）→ N 每加 1，RAISE 延迟 ≈0.7–0.9s → 滑出 temporal
window。**N=3 + w≤2.0 组合下 ESCALATE 结构性不可达**（RAISE-only 生产态）。

对 Owner 的三路线（**决策留 Owner**）：

1. **N=2 + w=2.0**：ESCALATE 可达但余量仅 0.04s——事件间隔的微小分布漂移
   即失效，脆弱性不可接受为生产默认。
2. **N=3 + 扩窗 ≥3s**：需修订 ADR-0041 候选空间（Owner 专属）；Gate H 数据
   （native P75=2.40）与本次 raise 延迟数据（N=3 → 2.66s）共同指向 w≈3–3.5s
   才能同时容纳持续性与配对。
3. **N=3 + w≤2.0，接受 RAISE-only**：ESCALATE 暂不可达（多模态组合升级
   关闭），生产语义退化为单模态 RAISE + combined 观测记录。最保守，
   与「宁少升级」取向一致。

## 5. 发现 2：AudioKind 类别塌缩实锤（回答 Owner §6 疑问）

塌缩取证（真实事件流逐事件检查 `scored_labels`）：

| 证据 | 数值 |
| --- | --- |
| distress_cry 事件总数（本次采集 17 资产） | 58 |
| 其中带 Tier1（YAMNet）labels | **0（0%）** |
| Tier0 特征规则直出 | **58（100%）** |
| **纯正常语音 voice_normal_16k 产出的 distress_cry** | **9 个** |
| fallback（`audio_anomaly_other`） | 0 |

**结论**：87.3% 的 distress_cry 占比是 **Tier0 特征规则把 normal/stressed
语音统一映射为 distress_cry** 的类别塌缩，既非现实分布，也非 fallback 产物
（fallback 路径计数为零，机制本身清白）。

**后果链（发现 3）**：case_a（LOW 真值，纯 normal 语音）在**所有 N/T 配置下
都 false_raise**（N=1/2/3 分别计入 7/4/3 个 false 的主要构成）——
**判级参数无法修复 kind 错标**。参数校准能消除「单事件升级」和
「电话一响就升级」，但不能消除「normal 语音被标成 distress_cry 后持续触发」。
这从数据上验证了硬门控 1（class_map/标签真实性拍板前 MONITOR ceiling 不解除）
的必要性，也支持 Owner 判断：**kind-specific production policy 在塌缩修复前
不可设计**（如 distress_cry → HIGH 的映射必须搁置）。

## 6. 对 ADR-0041 / ADR-0042 的候选建议（决策留 Owner）

| 参数 | Gate I 数据结论 | 状态 |
| --- | --- | --- |
| window | N=2 需 ≥2.0s（余量 0.04s）；N=3 需 ≥3s。候选空间与 N 强耦合 | 机制 ✅ / 数值候选 ✅ / 生产冻结 ❌ |
| N | N≥2 消除单事件升级与电话误升；N=3 更强但触发 ESCALATE 互斥 | **N=2 或 N=3 均有数据支撑，取决于路线选择** |
| T | T=4 与 T=5 在全部指标上无差异（raise 时刻相同）——当前数据无法区分，T=4 起步即可 | 候选 ✅ |
| M | NOTIFY=0 验证通过；维持 None | ✅ 冻结于 None |
| confidence | Tier0/Tier1 双源异质（Gate H §2.3）+ 本次 0% Tier1 参与——**confidence 阈值维度当前对 distress_cry 完全无区分力**，契约分离（rule confidence ≠ model confidence）应在数据契约层落实 | 待 Owner 拍板契约修订 |

## 7. 红线遵守声明

- 本 Gate 零 `src/` / 配置变更；分析态（ceiling 解除 / escalate 开启）仅存在于
  采集脚本内存，生产默认不变（硬门控 1/2 未触碰）。
- 未为触发率调整任何机制：combined 归零被如实报告为「结构性互斥」，
  而非通过放宽窗口/修改配对语义来制造 ESCALATE 触发。
- 类别塌缩取证结论支持维持硬门控 1：标签真实性未验证前，MONITOR ceiling
  生产默认不解除。

## 8. 复现与交付物

- 评测工具：一次性脚本（未入库）；事件流缓存 `data/cache/gate_i/cache.json`
  （gitignore 内）；原始结果 `_gate_i_result.json`（本地，关键数字已全部
  内嵌本报告）。
- 网格可复现：同一缓存上重跑 7 配置为纯计算（毫秒级），参数见 §1。