# Reality Check · telephone_risk_demo.mp4（REALITY-CHECK-2026-08-24）

> SSOT：`DOM-E2E-UPGRADE-ACCEPTANCE-CHECKLIST-2026-08-24.md` v3.6 §4 / §5 步骤 G。
> 定位（Owner 原话锚定）：回答「真实世界电话视频，在已修复 class_map 的当前 Runtime 上，
> 到底能产生什么」——**不是**「它能不能撑起确定性 Risk Story」。独立验证，不阻塞主线。
> 硬门禁自查：本报告仅实测取证与结论，未修改任何 Runtime 代码 / 断言 / 黑名单。

---

## 1. 结论（三选一）

> ## ✅ **选项 3 · 暴露 Runtime × 真实数据缺口 → 形成明确 gap 清单回填 roadmap**
> （有价值结果，非失败）

| 选项 | 判定 | 一句话理由 |
|---|---|---|
| 1 · 作 fixture 基底 | ❌ | 音频侧 Tier0 塌缩未修（H-5）+ 视觉无 PERSON_ENTERED 跃迁，链路两处断裂 |
| 2 · D2 第二场景样本 | ⚠️ 部分 | 素材真实，但 Runtime 当前对其输出 18×distress_cry 误报，直接入演示会污染产品故事 |
| **3 · gap 清单回填 roadmap** | ✅ | 首次在**纯真实世界素材**上量化复现 H-5，并暴露两条新 gap（见 §5） |

## 2. 素材与实验设置

| 项 | 值 |
|---|---|
| 素材 | `dataset/telephone_risk/media/telephone_risk_demo.mp4`（76,684,479 bytes） |
| 视频 | H.264 1920×1080 @30fps，时长 **30.97s** |
| 音轨 | AAC 48kHz mono，时长 **31.02s**（提取为 PCM16/16k mono 后送验） |
| 内容（三帧目视佐证 t=0/14/28s） | 室内客厅监控视角（CAM-02 时间戳 19:45）；老年男性坐沙发持手机贴耳通话，~26s 后起身放下手机 |
| 音频工具 | `scripts/verify_audio_fixture.py`（YAMNet ONNX 显式加载修复版 class_map + AudioPipeline 全链路） |
| 视觉工具 | ultralytics YOLO `data/models/yolo11n.pt`，conf≥0.25，每 2s 抽帧 ×16 |

## 3. 音频事件时间线（实测）

### 3.1 YAMNet 语义层（threshold=0.1 生产口径）
- 全段 top 标签：**`speech=0.781`**（唯一 ≥阈值标签）
- 宽阈值审计（0.03）：**黑名单类零出现**

### 3.2 AudioPipeline 全链路（Tier0 Energy backend）
- 事件总数 **19**：`audio_distress_cry ×18` + `audio_speech_rapid ×1`
- 时间分布：t=1.28s ~ 28.56s 近乎全程连续（与画面通话起止吻合）
- **关键证据**：每条 distress_cry 事件的 Tier1 scored_labels 均为
  `speech=0.866 ~ 0.997`（无一例外），即 Tier1 单元完全干净，
  distress_cry 纯粹由 Tier0 Energy 规则塌缩产生 —— 与 mix.wav/case_a/b_mix
  三组合成素材上的现象完全一致，且本次为**无合成混音的纯真实音轨**。
- t=25.56s 出现唯一 `audio_speech_rapid`（conf=0.947，tier1 speech=0.994）。
- C1 判定 PASS（scored_labels 契约口径：黑名单单元 = Tier1 labels，全程干净）；
  kind 层的 18×distress_cry 按 H-5 处置登记，不作素材否决。

### 3.3 Evidence Strength 分档说明
ADR-0042 五档强度字段尚未接入运行时事件输出（实现队列中）；本报告以
kind/score/conf/Tier1 labels 原始值如实呈现，不做分档推断。

## 4. 视觉事件时间线（实测）

| 指标 | 值 |
|---|---|
| person 检出 | **16/16 帧**（conf 0.859 ~ 0.911，全程持续在场） |
| cell phone 检出 | **0/16 帧**（即便画面中手机清晰可见） |
| 其他检出 | 每帧 3~5 个物体（沙发/茶几等家具类） |

### 4.1 与 §4 冻结事实的精确化
§4 记载「已确认无门前人物」。实测精确化：画面**有人**（接电话的老人本人，
YOLO 检出正确），但**无门前到访者**——无进入跃迁、无门外等候语义。
「持续在场 ≠ PERSON_ENTERED」正是链路断裂点之一。

## 5. Product Story 链路对照与 Gap 清单

```
telephone ──✓──▶ PERSON_ENTERED ──✗──▶ temporal overlap ──✗──▶ combined risk
(真实存在,      (人持续在场但无        (依赖前环)              (依赖前环)
 Tier0塌缩✗)    进入跃迁)
```

| Gap # | 描述 | 建议归属 |
|---|---|---|
| **Gap-1** | Tier0 Energy backend semantic collapse 在纯真实素材复现：31s 电话通话 → 18×distress_cry（H-5 第四组独立素材；回归判据素材池由四组扩为五组，+demo.mp4 ×18） | H-5（Policy 升级窗口） |
| **Gap-2** | 「老人在家接电话」叙事需要**室内在场类视觉事件**（presence/activity），而当前事件契约仅有「门前到访」语义的 PERSON_ENTERED/visit 系列——持续在场者无法触发任何事件 | 事件契约层（v2 范围，需 Owner 立项决策） |
| **Gap-3** | yolo11n 对手持手机 0/16 检出——多模态 phone 证据在视觉通道不可依赖，phone_interaction 只能靠音频通道（与 ADR-0038 phone_interaction=optional_supporting 结论一致） | 模型选型 backlog（非阻塞） |
| Gap-4 | 部署边界：素材为室内客厅视角，超出本模块「Home 门前时空异常」部署语义（AGENTS.md §0 模块边界）——即使 Gap-1/2 修复，该素材也不适用门前产品场景 | 边界声明（本文档即记录） |

## 6. 对既有裁决的影响

- **H-5 强化**：回归判据素材池扩为五组（benign×7 / mix×11 / case_a_mix×9 /
  case_b_mix×9 / **demo.mp4×18**），全部清零方可解除；
- **ADR-0038 无影响**：phone_interaction=optional_supporting 的降级结论被 Gap-3 反向佐证；
- **Product Story 主线无影响**：synthetic_replay 架构裁决（Owner 2026-08-24）
  再次被证实为正确分层——真实素材的 Runtime 缺陷不阻塞产品表面收口。

## 7. 复现命令

```bash
ffmpeg -y -i dataset/telephone_risk/media/telephone_risk_demo.mp4 \
  -vn -acodec pcm_s16le -ac 1 -ar 16000 rc_audio.wav
python scripts/verify_audio_fixture.py rc_audio.wav          # 音频三层
ffmpeg -i dataset/telephone_risk/media/telephone_risk_demo.mp4 -vf fps=0.5 f_%03d.jpg
# YOLO: data/models/yolo11n.pt, conf>=0.25, 逐帧 person/cell phone 统计
```