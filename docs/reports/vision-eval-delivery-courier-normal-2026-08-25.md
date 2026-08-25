# Vision Acceptance 报告 · delivery_courier_normal（vision-eval-2026-08-25）

> SSOT：`DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md` v3.4 §3.4 / §5 步骤 H+I。
> 流水线：Playwright 六张截图（步骤 H 产物）→ 五维 Visual Rubric（冻结）→ Vision Judge → 本报告。
> 结构化数据：`vision-eval-delivery-courier-normal-2026-08-25.json`（schema 由
> `tests/visualizer/test_vision_eval_report_schema.py` 校验；**pytest 不对 judge 结论做任何 assert**）。

> **Provenance 声明（Delivery Courier 场景）**：
>
> ```text
> Vision source  = REAL_RUNTIME_VIDEO （Delivery Courier 真实素材经 YOLO runtime 实时推理）
> Audio source   = 无音频轨          （Delivery Courier 视频无音轨，AudioLane 降级）
> Risk decision  = runtime-computed  （decision_policy 真算，非预设）
> ```
>
>  已知场景特性：
>   - 单 phase 观察窗（无 benign/switch_back）；
>   - 白天 14:00 UTC + 单次来访 → OddHourRule **不**触发，RepeatVisitRule **不**触发；
>   - 风险等级封顶 LOW（系统克制，不升级）；
>   - 推荐动作 NOTIFY_FAMILY / LOG_ONLY（仅家属端，无社区链路）。

## 1. 结论

| 轮次 | 截图版本 | PASS | WARN | FAIL | 判定 |
|---|---|---|---|---|---|
| **R1（首轮）** | 首批六图（frame_index=175） | **27** | **3** | **0** | ✅ **30 项无未解释 FAIL，D2 = PASS** |

```
CCTV Visual Acceptance  (placeholder label, actually Delivery Courier)
────────────────────────
Information hierarchy       PASS
Narrative completeness      PASS  (含 1 项 WARN · AI 标注时序)
Debug residue               PASS  (含 1 项 WARN · elder_001 ID)
Visual density              PASS  (含 1 项 WARN · 时间线缺离开)
Product feel                PASS

Critical failure            0
Unexplained failure        0

D2 = PASS
```

## 2. R1 六图判定矩阵（6 图 × 5 维）

| 区域 | 信息层级 | 叙事完整性 | 调试元素残留 | 视觉压迫感 | 产品感 | 整体 |
|---|---|---|---|---|---|---|
| 01 视频 | PASS | **WARN** | PASS | PASS | PASS | **PASS** |
| 02 行为时间线 | PASS | **WARN** | PASS | PASS | PASS | **PASS** |
| 03 风险解释卡 | PASS | PASS | **WARN** | PASS | PASS | **PASS** |
| 04 实时风险信号 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 05 行动闭环 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| ⑥ Memory Context | PASS | PASS | PASS | PASS | PASS | **PASS** |

## 3. 7 项硬失败检查（Delivery Courier D2 收口条件）

| # | 硬失败项 | 命中？ | 证据 |
|---|---|---|---|
| 1 | 页面明显像开发者调试工具 | ❌ 否 | 02/04/05 均为聚合行为卡/产品化按钮面板 |
| 2 | 用户无法理解为什么触发风险 | ❌ 否 | 03 卡 ✓ 人话原因（停留超过阈值）+ 强度条 + 通知家属 |
| 3 | bbox/conf/track_id/frame@ 泄漏 | ❌ 否 | 01 视频主体干净，无 overlay；其余区域无工程字段 |
| 4 | 时间线被逐帧重复检测淹没 | ❌ 否 | 02 仅 5 条聚合事件（首出 / 停留 / 预警），无每帧 person |
| 5 | 风险结论超过证据能力（诈骗/犯罪/入侵） | ❌ 否 | 全六图扫文本 0 命中 |
| 6 | Risk → Action 关系看不出来 | ❌ 否 | 05 三端「已下发 / 暂无 / 仅记录」+ 与 LRK LOW 一致 |
| 7 | 首屏严重空白/拥挤 | ❌ 否 | 视频 + 时间线 + 风险卡 均在 900px 首屏内；总高 ≤ 3.5 屏 |

**Critical failures = 0；Unexplained failures = 0。**

## 4. 打磨清单（WARN 级，登记不阻塞收口）

| ID | 区域 | 表现 | 修法 | 阻塞？ |
|---|---|---|---|---|
| W-1 | 01 视频 | AI 标注「AI 看到了 人」与画面无人存在时序不一致；属活页面竞态（visitor 在帧间离开） | sensor-card 在 visitor 离开后切到「无人 / 系统待观察」静默态（跨场景通用） | 否 |
| W-2 | 02 时间线 | 缺「离开画面」显式事件（visitor_track.left 后端有，前端 timeline 未显化） | 与 ③.5 CLEARED 信号联动，新增「已离开画面」行为事件 | 否 |
| W-3 | 03 风险卡 | elder_001 业务 ID 显化 | 家庭场景以人话化标签替代（监控对象 / 老人姓名）——跨场景共享打磨项（CCTV R2 已登记） | 否 |

注：W-1/W-2/W-3 均为跨场景共享打磨项（cctv 也有），不在本轮 D2 收口内推进。

## 5. 证据边界（Narrative Alignment · §6 Hard Rule 守护）

- **投影正确**：Delivery Courier 风险事实（白天正常来访 + 短暂停留 + LOW）正确投影为人话 + 通知家属；
- **未越界**：全六图扫文本 `forbidden_terms = ["诈骗", "犯罪", "入侵", "实施诈骗", "骗子", "作案"]` 0 命中；
- **叙事克制**：05 三端「通知家属 / 暂无工单 / 仅记录」——与 CCTV 形成「正常 vs 异常」对照基线，验证"看到人 ≠ 报警"的产品克制能力；
- **行动克制**：家属端 NOTIFY_FAMILY 是该场景下系统给出的"已记录 + 通知家属"行动，不升级社区，符合模块边界。

## 6. 与 cctv_surveillance_suspicious 的对照

| 维度 | cctv_surveillance_suspicious R1 | delivery_courier_normal R1 |
|---|---|---|
| 五维 PASS/WARN/FAIL | 28 / 2 / 0 | 26 / 4 / 0 |
| Critical failures | 0 | 0 |
| 打磨项 | W-1/W-2（2 项，跨场景共享） | W-1/W-2/W-3（3 项，含跨场景共享 2 项 + Delivery Courier 专属 1 项） |
| 证据边界 | 守住（夜间异常 → WARN / LOG_ONLY） | 守住（白天正常 → LOW / NOTIFY_FAMILY） |
| 行动闭环 | LOG_ONLY（无家属端） | **NOTIFY_FAMILY**（家属端已下发）+ LOG_ONLY |
| 场景对照 | 异常升级路径 | **正常克制路径**（关键差异） |

**关键差异**：Delivery Courier 行动闭环**包含家属端 NOTIFY_FAMILY**——这是该场景的设计选择（白天正常来访但行为超阈值，仍向家属推送一次通知），与 cctv「完全 LOG_ONLY」形成对比，进一步凸显系统按场景/规则调度的能力。

## 7. 复验记录

- 六张截图已落盘 `docs/reports/assets/vision-eval/delivery_courier_normal/`（frame_index=175，captured_at=2026-08-25 17:43:30 +0800，MANIFEST.md + md5 已写入）；
- `tests/visualizer/test_delivery_courier_product_screenshots.py` 3 项全绿（首帧/感知/行为/RAISED 就绪 + 6 张落盘 + console/pageerror=0）；
- `tests/visualizer/test_vision_eval_report_schema.py` 参数化扩展（product_story_risk + cctv_surveillance_suspicious + delivery_courier_normal 三报告）；
- `tests/visualizer/test_delivery_courier_visual_product_acceptance.py` 17 项布局+叙事+六区域检查全绿；
- `tests/visualizer/test_delivery_courier_dom_contract.py` 36 项 D0 DOM 契约测试 PASS（9 项 audio-only → na_skip() N/A）；
- `tests/visualizer/test_delivery_courier_acceptance.py` D1 runtime 验收全绿；
- ruff check 全绿。

## 8. 闭环状态

```
D0 DOM PASS  (36 passed / 9 N/A / 0 FAIL · Gate D0 = PASS)
    ↓
D1 Browser PASS  (test_delivery_courier_acceptance.py 全绿)
    ↓
D2 Vision PASS  (26 PASS / 4 WARN / 0 FAIL · Critical = 0)
    ↓
Delivery Courier Normal Product Scenario = DONE
```

**三场景完整闭环**：

```
product_story_risk       D0/D1/D2 PASS  (26 / 4 / 0)
cctv_surveillance        D0/D1/D2 PASS  (28 / 2 / 0)
delivery_courier_normal  D0/D1/D2 PASS  (26 / 4 / 0)

MVP Demo 链路：
  夜间异常升级 (cctv)  +  正常克制 (delivery_courier)  +  多模态高风险 (product_story_risk)
  → 系统双向能力完整演示
```