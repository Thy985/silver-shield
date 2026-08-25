# Vision Acceptance 报告 · cctv_surveillance_suspicious（vision-eval-2026-08-25）

> SSOT：`DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md` v3.4 §3.4 / §5 步骤 H+I。
> 流水线：Playwright 六张截图（步骤 H 产物）→ 五维 Visual Rubric（冻结）→ Vision Judge → 本报告。
> 结构化数据：`vision-eval-cctv-surveillance-suspicious-2026-08-25.json`（schema 由
> `tests/visualizer/test_vision_eval_report_schema.py` 校验；**pytest 不对 judge 结论做任何 assert**）。

> **Provenance 声明（CCTV 场景）**：
>
> ```text
> Vision source  = REAL_RUNTIME_VIDEO （CCTV 素材经 YOLO runtime 实时推理）
> Audio source   = 无音频轨          （CCTV 视频无音轨，AudioLane 降级）
> Risk decision  = runtime-computed  （decision_policy 真算，非预设）
> ```
>
> 已知场景特性：
>   - 单 phase 观察窗（无 benign/switch_back）；
>   - 风险等级上限 WARN（无音频叠加，单模态视觉证据不达 HIGH）；
>   - 推荐动作 LOG_ONLY/MONITOR（无电话/家属/社区链路）。

## 1. 结论

| 轮次 | 截图版本 | PASS | WARN | FAIL | 判定 |
|---|---|---|---|---|---|
| **R1（首轮）** | 首批六图（frame_index=86） | **28** | **2** | **0** | ✅ **30 项无未解释 FAIL，D2 = PASS** |

```
CCTV Visual Acceptance
────────────────────────
Information hierarchy       PASS
Narrative completeness      PASS
Debug residue               PASS  (含 2 项 WARN · 打磨清单)
Visual density              PASS
Product feel                PASS

Critical failure            0
Unexplained failure        0

D2 = PASS
```

## 2. R1 六图判定矩阵（6 图 × 5 维）

| 区域 | 信息层级 | 叙事完整性 | 调试元素残留 | 视觉压迫感 | 产品感 | 整体 |
|---|---|---|---|---|---|---|
| 01 视频 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 02 行为时间线 | PASS | PASS | **WARN** | PASS | PASS | **PASS** |
| 03 风险解释卡 | PASS | PASS | **WARN** | PASS | PASS | **PASS** |
| 04 实时风险信号 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 05 行动闭环 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| ⑥ Memory Context | PASS | PASS | PASS | PASS | PASS | **PASS** |

## 3. 7 项硬失败检查（CCTV D2 收口条件）

| # | 硬失败项 | 命中？ | 证据 |
|---|---|---|---|
| 1 | 页面明显像开发者调试工具 | ❌ 否 | 02/04/05 均为聚合行为卡/产品化按钮面板 |
| 2 | 用户无法理解为什么触发风险 | ❌ 否 | 03 卡 ✓ 人话原因（待核实到访 / 行为特征（视觉））+ 强度条 |
| 3 | bbox/conf/track_id/frame@ 泄漏 | ❌ 否 | 01 视频主体干净，无 overlay；其余区域无工程字段 |
| 4 | 时间线被逐帧重复检测淹没 | ❌ 否 | 02 仅 3 条聚合事件（首出 / 待核 / 预警），与「出现→重复→停留→风险→行动」叙事链对齐 |
| 5 | 风险结论超过证据能力（诈骗/犯罪/入侵） | ❌ 否 | 全六图扫文本 0 命中「诈骗/犯罪/入侵/实施诈骗/骗子」 |
| 6 | Risk → Action 关系看不出来 | ❌ 否 | 05 三端（家属/社区/日志）状态明示「暂无/已记录」+ LOW，与 LRK 一致 |
| 7 | 首屏严重空白/拥挤 | ❌ 否 | 视频 + 时间线 + 风险卡 均在 900px 首屏内；总高 ≤ 3.5 屏 |

**Critical failures = 0；Unexplained failures = 0。**

## 4. 打磨清单（WARN 级，登记不阻塞收口）

| ID | 区域 | 表现 | 修法 | 阻塞？ |
|---|---|---|---|---|
| W-1 | 02 时间线 / 05 行动 | 时间线 `ev.detail` 仍含英文枚举 `实时风险信号: behavioral(vision)`；`_REASON_ZH` 已定义但 timeline 构建路径未走该映射 | `live_stream.js` line 761 附近，detail 输出前过 `_REASON_ZH[r] \|\| r`；或上游 `live_adapter.py` 构造 `ev.detail` 时预先翻译 | 否 |
| W-2 | 03 风险卡 | 风险等级英文枚举 LOW 裸显（`"LOW 风险"`）；`_LEVEL_ZH = { HIGH: '高', MEDIUM: '中', LOW: '低' }` 已定义但 `live_stream.js:1599` 未走映射 | `levels.map(l => _LEVEL_ZH[l] \|\| l).join(' / ') + ' 风险'` | 否 |

注：W-1/W-2 同根（live_stream.js 渲染前未走映射）——可合并修复；不在本轮 D2 收口内推进。

## 5. 证据边界（Narrative Alignment · §6 Hard Rule 守护）

- **投影正确**：CCTV 风险事实（行为异常 / 视觉风险信号 / WARN/LOG_ONLY）正确投影为人话；
- **未越界**：全六图扫文本 `forbidden_terms = ["诈骗", "犯罪", "入侵", "实施诈骗", "骗子", "作案"]` 0 命中；
- **叙事链**：02 时间线三条聚合事件（首次出现 → 待核实到访 → 生成风险预警）形成完整「出现→判断→风险」叙事，未越界至「诈骗/入侵」定性；
- **行动克制**：05 仅 LOG_ONLY / 暂无命令下发 / 暂无工单 —— 与 CCTV 模块边界一致（无电话/家属/社区链路）。

## 6. 与 product_story_risk 的对照

| 维度 | product_story_risk R2 | cctv_surveillance_suspicious R1 |
|---|---|---|
| 五维 PASS/WARN/FAIL | 26 / 4 / 0 | 28 / 2 / 0 |
| Critical failures | 0 | 0 |
| 打磨项 | W-1~W-4（4 项） | W-1~W-2（2 项） |
| 证据边界 | 守住（人话原因 / ✓ 标记） | 守住（聚合行为 / LOW / LOG_ONLY） |

CCTV 因单模态（无音频）、无电话/家属链路，行动闭环更克制（仅 LOG_ONLY），打磨项更少。

## 7. 复验记录

- 六张截图已落盘 `docs/reports/assets/vision-eval/cctv_surveillance_suspicious/`（frame_index=86，captured_at=2026-08-25 16:54:54 +0800，MANIFEST.md + md5 已写入）；
- `tests/visualizer/test_cctv_product_screenshots.py` 3 项全绿（首帧/感知/行为/RAISED 就绪 + 6 张落盘 + console/pageerror=0）；
- `tests/visualizer/test_vision_eval_report_schema.py` 参数化扩展（product_story_risk + cctv_surveillance_suspicious 双报告）；
- `tests/visualizer/test_cctv_visual_product_acceptance.py` 17 项布局+叙事+六区域检查全绿；
- ruff check 全绿。

## 8. 闭环状态

```
D0 DOM PASS  (33 passed / 9 N/A / 0 FAIL)
    ↓
D1 Browser PASS  (test_cctv_surveillance_acceptance.py 22 项全绿)
    ↓
D2 Vision PASS  (28 PASS / 2 WARN / 0 FAIL · Critical = 0)
    ↓
CCTV Surveillance Suspicious Product Scenario = DONE
```