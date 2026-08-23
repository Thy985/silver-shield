# Precision Gate V2 · P1-a(min duration)修复后回归验证

- **日期**:2026-08-23
- **本 PR 变更**:`src/home_perception/audio/rule.py`(telephone 分支最短持续时间约束)+ 专项回归锁测试;**零 Policy 变更**(Tier0 感知层内聚修复)
- **授权链**:Owner「根据结果决定是否做 P1」→ Gate V1/Batch B 实证 min duration 覆盖 7/8 FP(跨 TTS 分布)→ Owner「继续下一步」(执行序列①)
- **验证集**:Batch A(17)+ Batch B(12)= 29 资产全量重取证

---

## 1. 变更内容

`RuleThresholds` 新增 `telephone_min_duration_s: float = 1.0`;`evaluate` 的 telephone 分支增加时长条件:

```python
if (
    narrow
    and features.speech_rate < self.t.telephone_rate
    and (features.duration <= 0 or features.duration >= self.t.telephone_min_duration_s)
):
```

- 利用 `AudioFeatures.duration`(= 段时长,extract 时已计算),**不改 evaluate 签名**——规避 `AudioRule` 子类覆写(`test_audio_tier1.py` 的 `_AlwaysPerceive` 等)的破坏;
- `duration <= 0` 视为未知(旧 fixture/手工构造路径),向后兼容;
- 阈值可配置,为验收定标留口。

**测试**:新增 `tests/test_audio_rule_p1a_min_duration.py`(6 用例:微段拒绝/长段保持/边界 1.0s/未知时长兼容/自定义阈值/正样本召回参数化);全量回归 **2279 tests, 0 failures, 0 errors, 4 skipped**;`ruff check src tests` 全绿。

## 2. Gate V2 结果(29 资产)

| 指标 | V1/BatchB 基线 | **V2 实测** | 判定 |
| --- | --- | --- | --- |
| telephone_precision | 61.54%(A 批口径) | **88.89%**(8/(8+1),A+B 合并口径) | ✅ 与 V1 预测值精确一致 |
| 微段 FP(0.14~0.22s) | 7 个(N4×3、HN1、B2、B8、B11) | **0 个** | ✅ **7/7 全部拦截** |
| FP 资产 | 6 个 | **1 个**(仅 N2_ambient) | ✅ 符合预期(第三机制待立项) |
| 素材级 recall(检出 tel) | 8/14 | **8/14** | ➖ 缺陷 B 未修,7 MISS 与基线完全一致(HN1/HN2/HN6/B9/B10/B11/B12,全部 tel+人声形态) |
| 正样本召回 | P1-P4/HN3/HN4/HN5 全 TP | **保持全 TP** | ✅ 无召回损失 |

> 口径说明:V2 取证脚本曾输出「recall 14/14」,系 hit 判定未检查事件 kind 的脚本口径错误,已修正为上表口径(检出 tel 事件才算 hit)。

## 3. 残余问题(本 PR 范围外,路径不变)

| 残余 | 状态 | 归属 |
| --- | --- | --- |
| N2_ambient → tel(15s 底噪,rms 0.061) | 唯一剩余 FP;能量 floor 原理性无效(V1 已证) | ③第三机制设计提案(谱平坦度/窄带纯度)待 Owner 立项 |
| 7 个 MISS(tel+人声形态锚点丢失) | 与基线一致,本 PR 不触碰 | ④缺陷 B 方向拍板(判据重构/语义收窄/结合) |
| cry 塌缩(P2) | 事件量与基线一致(未放大未缩小) | tremor 重定义,perception-only 不阻塞 |

## 4. 达标进度

```text
Gate 达标线:negative 零 tel ✅(除 ambient 1 点) | hard-negative 无微段 FP ✅ | precision ≥90% ➖ 88.89%(差 ambient 单点) | recall=100% ➖ 57.1%(待缺陷 B)
        ↓
③ ambient 第三机制 → precision 100%
        ↓
④ 缺陷 B 拍板 → recall 100%
        ↓
Gate H v2 → Gate I v2 → 冻结 Decision Contract
```

**Gate I 参数重估前提更新:precision 侧仅剩 ambient 单点;recall 侧等待缺陷 B 决策。**

## 5. 卫生说明

`_gate_v2.py`/`_gatev2_result.json`/`_gatev2_run.log`/`_pytest_*.log`/`_lint.log` 为一次性产物,随本 PR 提交前清理;Gate 素材(gitignore 内)本地留存供复核。本报告 + rule.py 改动 + 测试为入库产物。