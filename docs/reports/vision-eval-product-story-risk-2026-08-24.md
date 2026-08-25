# Vision Acceptance 报告 · product_story_risk（vision-eval-2026-08-24）

> SSOT：`DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md` v3.5 §3.4 / §5 步骤 I。
> 流水线：Playwright 六张截图（步骤 H 产物）→ 五维 Visual Rubric（冻结）→ Vision Judge → 本报告。
> 结构化数据：`vision-eval-product-story-risk-2026-08-24.json`（schema 由
> `tests/visualizer/test_vision_eval_report_schema.py` 校验；**pytest 不对 judge 结论做任何 assert**）。

> **Provenance 声明（v3.8 补记 · Owner 裁决 2026-08-24）**：本报告验收对象与六图截图均为
> **Telephone Risk Product Story Simulation**（产品闭环呈现能力验收），非真实世界端到端能力证明：
>
> ```text
> Audio semantic source = SYNTHETIC_REPLAY   （validation fixture 声明式注入，不经 AudioPipeline 推理）
> Vision source        = REAL_RUNTIME_VIDEO （CCTV 素材经 YOLO runtime 实时推理）
> Risk decision        = runtime-computed   （decision_policy 真算，非预设）
> ```
>
> Real-World Telephone Validation 当前 NOT READY（H-5 / Gap-2 / Gap-3，见 REALITY-CHECK 报告），
> 不宣称 PASS。该三行声明已在 /live 页面 prov-banner 常显落地（D0 AU-08 断言守护）。

## 1. 结论

| 轮次 | 截图版本 | PASS | WARN | FAIL | 判定 |
|---|---|---|---|---|---|
| R1（修复前） | 首批六图 | 11 | 14 | **5** | 图01/图04 整体 FAIL → 触发回修 |
| **R2（修复后）** | 重生成六图 | **26** | **4** | **0** | ✅ **30 项无未解释 FAIL，达成收口条件** |

## 2. R2 六图判定矩阵（6 图 × 5 维）

| 区域 | 信息层级 | 叙事完整性 | 调试元素残留 | 视觉压迫感 | 产品感 | 整体 |
|---|---|---|---|---|---|---|
| 01 视频 | PASS | PASS | WARN | PASS | PASS | WARN |
| 02 行为时间线 | PASS | PASS | PASS | WARN | PASS | **PASS** |
| 03 风险解释卡 | PASS | PASS | WARN | PASS | PASS | WARN |
| 04 实时风险信号 | PASS | PASS | PASS | PASS | PASS | **PASS** |
| 05 行动闭环 | PASS | PASS | WARN | PASS | PASS | **PASS** |
| ⑥ Memory Context | PASS | PASS | PASS | PASS | PASS | **PASS** |

## 3. FAIL 缺陷闭环（R1 → 修复 → R2 resolved）

### D-I1 · runtime 标识裸显（R1 图04 FAIL 主因 + 图01 FAIL 组成）
- **证据**：signal_id UUID 片段（`a8afc077-00e`）、主体 UUID 片段、category `behavioral`、severity `LOW` 直接上屏；视频区出现「Visual perception」「媒体时间≠证据时间（VM-10）」内部条款号。
- **修复**：live_stream.js rt-card 人话化——UUID/数值降级为 `data-signal-id`/`data-subject-id`/`data-severity` 溯源属性；levels/category 中文映射（`_LEVEL_ZH`/`_SIGNAL_CATEGORY_ZH`），未知名类不渲染；render.py 说明文去 VM-10 改人话。
- **R2**：图04 五维全 PASS；图01 升至 WARN。

### D-I2 · behavioral(vision) 枚举裸露（图02/03/04 共性）
- **修复**：live_stream.js `_REASON_ZH` 增加确定性译文映射 `'实时风险信号: behavioral(vision)' → '实时风险信号: 行为特征（视觉）'`；BA `REASON_RUNTIME_ALLOWLIST` 同步译文（该 allowlist 即 P5b docstring 定义的「润色白名单」机制，键集冻结可枚举，非编造）。
- **R2**：三图枚举全部消除。

### G-H1 · Live 页 Evidence Graph 从未渲染（H 步骤发现）
- **证据**：console/pageerror=0 校验抓到 `ReferenceError: echarts is not defined`——图 JS 内联于文档中段解析期执行，先于页尾 echarts 定义；原「details 展开时自然解析执行」假设与浏览器事实不符。
- **修复**：render.py toggle-gated 内联（details 首次展开才 init，兼规避隐藏容器 0-size init）。

## 4. 打磨清单（WARN 级，登记不阻塞收口）

| ID | 区域 | 内容 |
|---|---|---|
| W-1 | 01 视频 | 进度条 `--` 占位符改总时长语义；「受控演示输入」措辞软化；Case Time 秒数口语化 |
| W-2 | 02 时间线 | 重复预警合并/折叠，增加行间距 |
| W-3 | 03 解释卡 | trigger chips 裸 score `0.50` 定性化或 data-* 化（涉 D0 AU-02 断言边界，需契约联动后处理） |
| W-4 | 05 闭环 | 底部「只读证据节点/证据时间线」改为用户友好表述 |

## 5. 复验记录

- 产品修复后全量回归：D0(32) + BA(37+1s) + H(3) 三套件全绿，ruff 干净；
- 六张截图以修复后代码重生成（manifest md5 更新，见步骤 H 产物目录）；
- 本报告 R2 为完整重走流水线的第二轮评审结论。