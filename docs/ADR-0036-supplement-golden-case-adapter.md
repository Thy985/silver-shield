# Golden Case 接入 Live — 架构决策（ADR-0036 补遗）

> **Owner**: SilverShield 架构 / 产品
> **Status**: 决策中（v0.1，待 Owner 评审）
> **承接**: AGENTS.md §1.2（产品化总原则）/ ADR-0036 / DESIGN-golden-case-live-product.md / DIAGNOSIS-golden-case-data-validity.md
> **目标**: 把"Golden Case 集合"从"资产存在"推进到"每个 Case 都能作为 Live 产品场景被正确加载、运行和展示"

---

## 一、决策反转的回顾

之前我推荐"先做 telephone_risk → 修 audio 链"，经过严格 4 维度评估被反转：

| 维度 | telephone_risk | repeated_visit | 真实判断 |
|------|---------------|----------------|----------|
| 修复成本 | **高**（2 个外部依赖）| **中**（1 个新增功能）| repeated_visit 性价比高 |
| 修复后跑通认知 | 3/6 | 2/6 | telephone_risk 多 1 个 |
| 产品价值 | nice-to-have | **核心**（跨日记忆是护城河）| repeated_visit 更核心 |
| 与"先打通 1 个 case 的 5 认知"目标的关系 | 受限（依赖外部）| 同样受限 | 二者都受限 |

**反转后的核心建议**：

> 不是"先选 1 个 case 深耕"，而是"先让 4 个 case 都能跑通"。

---

## 二、"纯映射"严格定义

### 2.1 你提出的纯映射标准

```
Golden manifest
       ↓
ScenarioConfig adapter（只翻译字段，不写死、不补默认值）
       ↓
Live Runtime
```

这个标准非常关键。**纯映射的反面**是：

```
Golden manifest
       ↓
复制一套 demo scenario
       ↓
填一堆默认值
       ↓
"看起来跑通"（实际是 adapter 写死的特例）
```

### 2.2 必须满足的 3 个条件

1. `manifest` 字段集合 `M` 的所有"运行所需"字段都有 `ScenarioConfig` 字段集合 `S` 中的对应字段
2. `adapter` 不会"填默认值"——任何在 `M` 中**没有**的字段，在 `S` 中也**没有**或保持 None
3. 4 个 case 共用同一个 `adapter` 函数，**不**为某个 case 写硬编码特例

---

## 三、4 case × 字段映射矩阵（基于实测）

### 3.1 完整映射表

| Manifest 字段 | stranger_visit | repeated_visit | telephone_risk | evidence_insufficient | ScenarioConfig 字段 | 纯映射？ |
|---------------|:---:|:---:|:---:|:---:|---|:---:|
| `case` / `scenario_id` | ✓ | ✓ | ✓ | ✓ | `scenario_id` | ✅ |
| `case_start` | ✓ | ✓ | ✓ | ❌ | `start_time` | ✅ |
| `output/*_demo.mp4` | ✓ | ✓ | ✓ | ✓ | `media_path` | ✅ |
| `audio_mix/*_mix.wav` | ✓ | ✓ | ✓ | ✓ | `audio_path` | ✅ |
| `product_question` | ✓ | ✓ | ✓ | ✓ | `description` | ✅ |
| `episodes[].media` | — | 3 段 | — | — | (预拼接) | ✅ |
| `episodes[].memory_ref` | — | ✓ | — | — | **无** | ❌ 新功能 |
| `episodes[].decision` | — | ✓ | — | — | **无** | ❌ 静态展示 |
| `acts[].video_file` | — | — | — | 3 段 | (预拼接) | ✅ |
| `acts[].audio_mix` | — | — | — | 3 个 | (单 path) | ⚠️ 选 1 个 |
| `variants[].audio_mix` (case_a/b) | — | — | ✓ | — | **单 path** | ⚠️ 选 1 个 |
| `acoustic_progression` 4 阶段 | — | — | ✓ | — | **无** | ❌ 新功能 |
| `event_windows` | ✓ | ❌ | ✓ | ❌ | **无** | ⚠️ metadata |
| `cctv` 后处理参数 | — | — | — | ✓ | **无** | ❌ metadata |
| `expected.decision.outcome` | ✓ | ✓ | ✓ | ✓ | **无** | ❌ 非 runtime |
| `expected.workflow.family.required_state` | ✓ | ✓ | — | — | **无** | ❌ 新功能 |

### 3.2 关键发现

> `data/golden/{case}/output/{case}_demo.mp4` **已经预拼接好 3 幕**。
> 这意味着多幕 case 的"资产层合并"已经完成，adapter **不需要解析 episodes/acts/variants 数组**。
>
> 所以**纯映射在"运行启动"层面是可行的**——4 个 case 都能跑通。

**纯映射能搞定的（70%）**：
- 基础元数据（scenario_id, start_time, description）
- 视频路径（`output/{case}_demo.mp4`，已预拼接）
- 音频路径（`audio_mix/{case}_mix.wav`，已合并）
- 简单 rule_overrides
- description 包含 product_question

**纯映射搞不定的（30%）**：
- `memory_ref` / `prior_episodes` → ⑥ 跨日叙事
- `episodes` / `acts` / `variants` 的多幕切换
- `acoustic_progression` 4 阶段
- `event_windows` 时间窗
- `cctv` 后处理参数

**这 30% 字段是"先验叙事信息"**——描述"应该发生什么"，但 Live runtime 不知道。

---

## 四、M1 严格分两阶段

### 4.1 阶段 1：M1-Adapter（纯映射）

**目标**：4 个 case 都能进入 Live Runtime，跑出基础视频流。

**实现**：
```python
# silver_demo/golden_adapter.py（新文件）

def load_golden_scenario(case: str) -> ScenarioConfig:
    """从 data/golden/{case}/manifest.yaml + output/{case}_demo.mp4 加载。
    
    纯映射：只翻译 manifest 已声明的字段到 ScenarioConfig。
    """
    manifest_path = REPO_ROOT / f"data/golden/{case}/manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    
    # 1. case → scenario_id
    scenario_id = case
    
    # 2. case_start → start_time
    start_time = _parse_iso(manifest["case_start"])
    
    # 3. output/{case}_demo.mp4 → media_path（已预拼接，不解析多幕）
    media_path = str(REPO_ROOT / f"data/golden/{case}/output/{case}_demo.mp4")
    
    # 4. audio_mix/*_mix.wav → audio_path
    audio_mix = REPO_ROOT / f"data/golden/{case}/audio_mix/{case}_mix.wav"
    audio_path = str(audio_mix) if audio_mix.exists() else None
    
    # 5. product_question → description
    description = manifest.get("product_question", "")
    
    # 6. source = case 名（与现有 delivery_courier_normal 一致）
    source = case
    
    return ScenarioConfig(
        scenario_id=scenario_id,
        source=source,
        source_type="video_file",
        media_path=media_path,
        audio_path=audio_path,
        start_time=start_time,
        frame_interval_s=0.5,
        fps_target=8,
        loop=True,
        description=description,
    )
```

**验收**：
- [ ] 4 个 case 都能 `python scripts/run_demo.py --scenario golden_stranger_visit` 启动
- [ ] Gateway `/health` 返回 OK
- [ ] 视频帧流 + 基础感知事件从 WS 推送

**4 个 case 跑通后**：
- ① 实时画面：✅ 视频流
- ② AI 看到：⚠️ 视觉有，audio 空（YAMNet 修好前）
- ③ 为什么：⚠️ runtime 检测出的 reason
- ④ 当前判断：runtime 实际判定的 level
- ⑤ AI 做什么：runtime 实际发的 action
- ⑥ 历史：空（Live 不产 memory）

### 4.2 阶段 2：M1-Evidence（manifest → EvidenceProjection 增强）

**目标**：把 manifest 中的"先验叙事信息"作为 EvidenceProjection 的 pre-event 投影。

**实现**：
```python
# silver_demo/golden_adapter.py 扩展

def golden_prior_evidence(case: str) -> list[dict]:
    """从 manifest 派生 pre-event 证据节点（不编造事实，仅翻译声明）。
    
    严格 VM-1：只读 manifest 已有字段，不创造新事实。
    严格 VM-9：浏览器只渲染，不推理。
    """
    manifest = _load_manifest(case)
    nodes = []
    
    # acoustic_progression 4 阶段 → 预期 audio nodes（pre-event 标签）
    audio_cfg = manifest.get("audio", {})
    for layer_name, layer in audio_cfg.items():
        if isinstance(layer, dict) and "acoustic_progression" in layer:
            for phase in layer["acoustic_progression"]:
                nodes.append({
                    "type": "expected_audio_state",
                    "phase": phase["phase"],
                    "time": phase["time"],
                    "f0_mean": phase["f0_mean"],
                    "ref": f"golden://{case}/audio/{phase['phase']}",
                })
    
    # episodes[].memory_ref → 预期 memory nodes（pre-event）
    for ep in manifest.get("episodes", []):
        for mem_ref in ep.get("memory_ref", []):
            nodes.append({
                "type": "expected_memory_ref",
                "source_episode": mem_ref,
                "current_episode": ep["id"],
                "ref": f"golden://{case}/episodes/{mem_ref}",
            })
    
    return nodes
```

**验收**：
- [ ] `telephone_risk` 跑起来时，声学状态区域显示 4 阶段（NORMAL/ATTENTION/AROUSAL/STRESS）
- [ ] `repeated_visit` 跑起来时，⑥ 区域显示跨日引用（"3 天前 ep_001 → MONITOR"）
- [ ] 这些都是**pre-event 标签**，不是 runtime 真正检测出来的事实
- [ ] UI 上有清楚标识（"预期 / 设计意图"vs"实测"）

### 4.3 阶段 3：M1-Validation（4 case 真实跑通）

**目标**：用实测数据填"六认知 × Case 矩阵"，**真实运行结果**（不是设计意图）。

**实现**：
- 启动 4 个 case 的 Live Runtime
- 用 Playwright + WS 客户端收集真实事件
- 填一张表：
  - 设计意图 | 实际运行 | 偏差原因

**验收**：
- [ ] 4 个 case 都实测
- [ ] 矩阵区分"设计意图"vs"实际运行"
- [ ] 偏差原因明确（如"audio 全空 → YAMNet class_names 缺失"）

---

## 五、VM-1 / VM-9 不变量守护

M1-Evidence 阶段 2 涉及"先验信息投影"——这可能与 VM-1（唯一事实源）冲突。

### 5.1 不变量守则

1. **VM-1 单源真相**：所有 EvidenceProjection 节点**必须**有明确标注：
   - `provenance_kind: REAL_SENSOR` → runtime 实际检测出
   - `provenance_kind: GOLDEN_EXPECTED` → manifest 声明的预期
   - **绝不**把这两类混在一起

2. **VM-9 浏览器零推理**：UI 渲染 pre-event 时**必须**显示"预期 / 设计意图"标签，绝不让用户误以为是 runtime 实际检测

3. **AC-7 provenance 一等视觉**：复用现有 `_PROVENANCE_BADGE` 机制，新增 `GOLDEN_EXPECTED` 标签

### 5.2 验证方案

- [ ] `live_adapter.py` 新增 `GOLDEN_EXPECTED` provenance kind
- [ ] `_render_provenance_banner` 显示"● LIVE · 预期事件已加载"（与 REAL_SENSOR 区分）
- [ ] Pre-event 节点在 timeline 中**视觉差异化**（如灰底 + ⓘ 图标）

---

## 六、任务清单（修正后）

### Phase 1：M1-Adapter（纯映射，纯代码）

| ID | 任务 | 文件 | 验收 |
|----|------|------|------|
| M1.1 | 新建 `golden_adapter.py` | `src/silver_demo/golden_adapter.py` | 函数 `load_golden_scenario(case)` |
| M1.2 | 4 个 case 都能 `load_golden_scenario` 成功 | 测试 | 不解析 `episodes/acts/variants` |
| M1.3 | 与 `run_demo.py` 集成 | `scripts/run_demo.py` | `--scenario golden_*` 入口 |
| M1.4 | Playwright 验证 4 case 都能跑 | `tests/visualizer/test_golden_live_acceptance.py` | 视频流 + 基础感知 |

**周期**：3 天（1 周内可完成）

### Phase 2：M1-Evidence（manifest → 投影）

| ID | 任务 | 文件 | 验收 |
|----|------|------|------|
| M1.5 | 新增 `golden_prior_evidence()` | `src/silver_demo/golden_adapter.py` | 从 manifest 派生 pre-event |
| M1.6 | LiveAdapter 接入 pre-event | `live_adapter.py` | `GOLDEN_EXPECTED` provenance |
| M1.7 | UI 差异化显示 | `render.py` | 灰底 + ⓘ 标识 |

**周期**：1 周

### Phase 3：M1-Validation（实测矩阵）

| ID | 任务 | 文件 | 验收 |
|----|------|------|------|
| M1.8 | 4 case 真实跑 Live Runtime | 测试 | 收集真实事件 |
| M1.9 | 填"六认知 × Case × 设计意图 / 实际运行"矩阵 | `docs/DIAGNOSIS-golden-case-data-validity.md` 附录 |
| M1.10 | 偏差分析 | 文档 | 每个偏差的根因 |

**周期**：3 天

### Phase 4：M1 完成后的产品决策

> 完成 M1 后，4 个 case 都能跑通，**真实数据矩阵**完成。
> 这时再做"哪个 case 做主 Demo"的判断。
>
> **预期优先级**（基于你的产品判断）：
> 1. `repeated_visit`（护城河）
> 2. `telephone_risk`（声学 + 跨模态）
> 3. `evidence_insufficient`（克制）
> 4. `stranger_visit`（基础）
>
> 但**优先级**的判断需要等真实数据填完。

---

## 七、不做清单

> 严格遵守你的"先建通用接入层，不写特例"原则。

- ❌ 不写 4 个 hard-coded scenario YAML
- ❌ 不为某个 case 在 adapter 中写特例逻辑
- ❌ 不复制 manifest 内容到 scenario YAML
- ❌ 不填"看起来合理"的默认值
- ❌ 不在 adapter 中硬编码 `episodes` / `acts` / `variants` 解释逻辑

**如果发现必须做上述任何一项，就停下来**——说明抽象层不完整，需要先修抽象层。

---

## 八、Owner 决策点

1. **M1-Adapter 范围**：是否接受"用 `output/{case}_demo.mp4` 预拼接"作为多幕合并的合法方案？
2. **M1-Evidence 范围**：是否接受 manifest 派生 pre-event 节点（GOLDEN_EXPECTED provenance）？
3. **VM-1 守护**：是否接受新的 provenance kind `GOLDEN_EXPECTED`？
4. **Phase 1 时序**：是否在 audio 链修复（Path A）**之前**做 M1？还是并行？

---

## 十一、Phase 2 完成报告（M1-Evidence · 2026-08-17）

### 实施内容

| 任务 | 文件 | 状态 |
|------|------|------|
| Golden pre-event 派生 | `src/silver_demo/golden_evidence.py`（新） | ✅ 4 类节点（acoustic_state / memory_ref / variant / cross_modal）|
| Post-process 注入 | `src/silver_demo/golden_evidence_injector.py`（新） | ✅ timeline / graph.nodes / memory_episodes / refs 同步 |
| 冻结边界守卫 | `golden_evidence.py` 零 `home_perception.*` import | ✅ freeze_boundary 测试通过 |
| 路由检测 | `gateway.py:live_preview` 按 `scenario.scenario_id in GOLDEN_CASES` 注入 | ✅ |
| 视觉差异化 | `renderer.py:_render_timeline` 加 `.tl-item-golden` class + ⓘ icon | ✅ |
| CSS 装饰 | `render.py` 加 `.tl-item-golden` style（半透明 + 灰底 + 虚线左边） | ✅ |
| Live 模式预渲染 | `render.py:_render_live_skeleton` 条件修改（golden pre-event 时调用 `_R._render_timeline`） | ✅ |
| 单元测试 | `tests/demo/test_golden_evidence.py`（12 个）| ✅ |
| 注入器测试 | `tests/demo/test_golden_evidence_injector.py`（10 个）| ✅ |
| E2E 端到端 | 4 case 启动后 DOM 含 `.tl-item-golden` 节点 | ✅ |

### E2E 验证（实测）

| Case | Pre-event 节点 | DOM 验证 |
|------|---------------|---------|
| stranger_visit | 0 | ✅（无 manifest pre-event 字段）|
| repeated_visit | 3 memory_ref | ✅ 3 `<li class="tl-item tl-item-golden">` 节点 |
| telephone_risk | 6 (4 audio_state + 2 variant) | ✅ 6 `<li class="tl-item tl-item-golden">` 节点 |
| evidence_insufficient | 0 | ✅（无 manifest pre-event 字段）|

### 关键设计决策（实际落地）

1. **不**改 schema Literal（`ProvenanceKind` 闭集保持）—— pre-event 节点用 `SIMULATED` 标注
2. **不**改 LiveAdapter 内部 —— `inject_golden_evidence` 在 `build_live_presentation` 返回后 post-process
3. **不**import `home_perception.*` 在 silver_demo Runtime Core —— pre-event 节点用 dict 结构（duck-type）
4. **不**在 `ScenarioConfig` 加 `is_golden` 字段（避免 round-trip 字段丢失）—— 用 `scenario_id in GOLDEN_CASES` 集合检测
5. **不**解析 manifest `episodes/acts/variants` 作为启动参数 —— 预拼接 `*_demo.mp4` 已在 Phase 1 解决

### 关键发现：manifest schema bug

`data/golden/telephone_risk/manifest.yaml` 的 `cross_modal:` 字段在 YAML 缩进上与 `audio: [...]` 列表的子项同级，PyYAML 解析为 audio list 的延续项。
- 影响：cross_modal 实际派生 0 节点（实测验证）
- adapter 行为：**fail-soft**（字段不存在 → 跳过），不编造
- 修复责任：manifest yaml（不在 Phase 2 范围）

### 测试结果

```
156 passed, 3 skipped, 1 warning in 13.42s
```

### VM-1 / VM-9 守护

- pre-event 节点 `provenance_kind="SIMULATED"`（schema 闭集内合法值）
- 渲染层靠 `.tl-item-golden` class 视觉区分（不动语义）
- 不污染 `audio_evidence`（REAL_SENSOR 专属）
- 不污染 `memory_episodes` 已有逻辑（仅填充，**不**触发 runtime 路径）

### 启动命令

```bash
# 4 case 都支持
python scripts/run_demo.py --scenario golden_stranger_visit --live --port 8765
python scripts/run_demo.py --scenario golden_repeated_visit --live --port 8765
python scripts/run_demo.py --scenario golden_telephone_risk --live --port 8765
python scripts/run_demo.py --scenario golden_evidence_insufficient --live --port 8765
```

访问 `http://127.0.0.1:8765/live` 即可看到 pre-event 节点（带 ⓘ 图标 + 灰底虚线左边）。

### Phase 3 / 后续

- **Phase 3**：M1-Validation（4 case 实测填"六认知 × Case × 设计意图/实际"矩阵）
- **Phase 4**：Showcase 决策（基于真实数据决定主 Demo）
- **Path A**（并行）：audio 链修复 → telephone_risk ② 声学真的跑通
- **Path C**（并行）：Live Adapter memory 通道 → ⑥ 跨日叙事的 runtime 数据

### Phase 2 完成 Owner 决策

是否进入 **Phase 3**（M1-Validation 矩阵）？

或者**并行做 Path A**（audio 链修复），让 telephone_risk 的 acoustic_state 节点不再是"预期"而是"实测"？

---

*ADR 版本：v0.3 | Phase 1 + Phase 2 完成 | 待 Owner 决策 Phase 3 / Path A / Path C 优先级*

---

## 十、Phase 1 完成报告（2026-08-17）

### 实施内容

| 任务 | 文件 | 状态 |
|------|------|------|
| Golden adapter 实现 | `src/silver_demo/golden_adapter.py`（新） | ✅ 4 case 全部 `load_golden_scenario()` 成功 |
| 单元测试 | `tests/demo/test_golden_adapter.py`（新） | ✅ **15 passed** |
| 入口集成 | `scripts/run_demo.py::resolve_scenario` | ✅ `--scenario golden_<case>` 入口工作 |
| E2E 子进程测试 | 4 case 启停循环 | ✅ 4/4 端到端 health OK |
| Playwright E2E | 浏览器实跑 | ✅ 4/4 视频流/timeline/behavior-timeline 都通 |

### 端到端验证数据（实测）

| Case | 视频元素 | ov-frame | timeline 节点 | 视频帧大小 |
|------|---------|----------|------------|-----------|
| stranger_visit | ✓ | 44 | 9 | 38 KB |
| repeated_visit | ✓ | 13 | 9 | 32 KB |
| telephone_risk | ✓ | 14 | **12** | 43 KB |
| evidence_insufficient | ✓ | 13 | 8 | 47 KB |

telephone_risk 节点最多（12），因为它跑得最久积累更多。

### 关键修复

1. **PyYAML 隐式 datetime 解析**：`2026-08-13T18:20:00` 被 PyYAML 转成 `datetime`，而非 string。`_parse_iso` 接受两种输入。
2. **timezone 兜底**：golden manifest `case_start` 无 tz 后缀，与现有 demo yaml（`+00:00`）不一致。`_parse_iso` 强制 UTC，否则 `cold_start.recover` 抛 `TypeError`（aware - naive）。

### 纯映射原则验证

| 项 | 状态 |
|----|------|
| 4 case 共用同一个 `load_golden_scenario` 函数 | ✅ |
| 没有为某个 case 写特例分支 | ✅ |
| 没有复制 manifest 内容到新 yaml | ✅（运行时生成临时 yaml 供 `load_scenario` 读） |
| 没有填"看起来合理"的默认值 | ✅（除 `frame_interval=0.5/fps=8/loop=True` 固定运行参数） |
| 不解析 `episodes/acts/variants` 数组 | ✅（用预拼接 `*_demo.mp4`） |
| 不解析 `expected.*` 字段 | ✅ |
| 不解析 `acoustic_progression` / `memory_ref` | ✅（留给 Phase 2） |

### 启动命令

```bash
# 4 case 都支持
python scripts/run_demo.py --scenario golden_stranger_visit --live --port 8765
python scripts/run_demo.py --scenario golden_repeated_visit --live --port 8765
python scripts/run_demo.py --scenario golden_telephone_risk --live --port 8765
python scripts/run_demo.py --scenario golden_evidence_insufficient --live --port 8765

# 访问
http://127.0.0.1:8765/live
```

### 已知未做（按 Phase 顺序）

- ❌ Phase 2：M1-Evidence（manifest → EvidenceProjection 增强，provenance=GOLDEN_EXPECTED）
- ❌ Phase 3：M1-Validation（4 case 实测填矩阵）
- ❌ Phase 4：Showcase 决策

### Phase 1 完成 Owner 决策

是否进入 **Phase 2**（M1-Evidence 增强投影）？

如果 Owner 决定先修 audio 链（Path A），可以**并行**：
- Phase 2 是 UI/projection 层增强
- Path A 是 audio runtime 修复
- 两者不冲突

---

*ADR 版本：v0.2 | Phase 1 完成 | 待 Owner 决策 Phase 2*