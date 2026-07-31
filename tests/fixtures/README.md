# Test Fixtures（场景验证数据，非训练数据）

> **目标**：验证「摄像头视频流 → YOLO 检测 → Tracker → VisitorTrack → 门前事件」
> 这一条感知链路（见 `docs/08_roadmap.md` P0-5/P0-7）。
> **不**为训练诈骗识别模型服务；本仓库的训练数据是另一回事（v2 范畴）。

## 目录结构

```
tests/fixtures/
├── person.jpg              # 单张 ultralytics 官方示例（多人室内）
├── doorway/                # 真实监控场景（CAVIAR）
│   ├── one_stop_enter/     # 单人 stop + enter
│   ├── one_leave_reenter/  # 单人 leave + reenter（revisit）
│   └── meet_walk_together/ # 多人 meet + walk
├── caviar_raw/             # CAVIAR 原始 MPG（gitignore）
├── download_fixtures.py    # 下载 + 抽帧脚本
└── README.md
```

`*.jpg` 和 `caviar_raw/`、`doorway/` 全部被 `.gitignore` 忽略。
**仓库里只存 `download_fixtures.py` + `README.md`，首次运行脚本即可重现。**

## 数据源：CAVIAR

- **项目**：CAVIAR（Context Aware Vision using Image-based Active Recognition）
- **资助**：EC Funded CAVIAR project / IST 2001 37540
- **授权**：免费，注明来源即可（学术研究公开数据集）
- **官方**：http://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1
- **场景**：INRIA 实验室门口/走廊 + Lisbon 商城走廊（双机位：cor 走廊 + front 正面）
- **规格**：MPEG2 / 384x288 / 25 fps

## 选定场景

| 输出目录 | 源 MPG | 验证目标 | 帧数 |
| --- | --- | --- | --- |
| `one_stop_enter/` | `CAVIARDATA2/OneStopEnter1cor/OneStopEnter1cor.mpg` | 单人 enter + 短暂 dwell（为 P0-7 停留规则做前置） | 50 |
| `one_leave_reenter/` | `CAVIARDATA2/OneLeaveShopReenter1cor/OneLeaveShopReenter1cor.mpg` | 单人 leave → reenter（验证 P0-5 revisit + P0-7 重复来访） | 30 |
| `meet_walk_together/` | `CAVIARDATA1/Meet_WalkTogether1/Meet_WalkTogether1.mpg` | 多人 meet + walk（验证 track_id 独立不串） | 50 |

**数据规格**：2 fps 抽帧 + 均匀下采样，每场景 30-50 帧 JPG（q=90），总约 4MB。
下载 + 抽帧总时间 < 1 分钟（ffmpeg MPEG2 解码占主要）。

## 使用方法

### 首次下载
```bash
python tests/fixtures/download_fixtures.py
```

### 验证下载结果
```bash
ls tests/fixtures/doorway/one_stop_enter/    # 应该有 frame_00001.jpg ... frame_00050.jpg
```

### 跑测试
```bash
python -m pytest tests/test_tracker.py -v
```

测试在 fixture 缺失时会 `pytest.skip("CAVIAR fixture 缺失")`，不会失败。

### 重新生成
```bash
rm -rf tests/fixtures/doorway/ tests/fixtures/caviar_raw/
python tests/fixtures/download_fixtures.py
```

## 不在 MVP 范围

以下**当前不收集**（明确边界，避免过度采集）：

- ❌ **真实老人门口录像**——隐私敏感、需脱敏、样本稀缺。
  MVP 用 CAVIAR 公开数据验证链路；真实部署时再采集。
- ❌ **诈骗行为样本**——隐私敏感、极少、标注难；
  v2 再考虑，**绝不**用真实案例训练。
- ❌ **MOT/MOT20 大型数据集**——本场景不需要；
  CAVIAR 30-50 帧已足够验证 VisitorTrack 生命周期。
- ❌ **PETS2009 / BEHAVE 等其他监控数据集**——本项目无新增需求；
  需要时再扩。

## 引用方式

CAVIAR 数据集引用：

> CAVIAR: Context Aware Vision using Image-based Active Recognition.
> EC Funded CAVIAR project / IST 2001 37540. http://homepages.inf.ed.ac.uk/rbf/CAVIARDATA1
