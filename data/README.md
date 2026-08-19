# data/ — 运行时数据目录

> SilverShield Home 感知模块的运行时数据存储。**不要提交本目录下的媒体文件**（见 `.gitignore`）。

## 目录结构

```
data/
├── cache/          # 运行时缓存（帧缓存、推理中间结果）
├── demo/           # 演示运行时数据
│   ├── live_audio/ # Live 音频注入数据（P0-11.x）
│   └── region_audit_cctv_live.md
├── evidence/       # 证据片段（高风险事件触发留存，自动过期）
├── memory/         # 记忆快照
├── models/         # 模型权重（YOLO 等）
└── (旧目录已迁移至 dataset/)
    ├── _analysis/   → dataset/_analysis/
    ├── _quarantine/ → dataset/_quarantine/
    └── golden/      → 已清除（资产已归档至 dataset/_canonical/）
```

## 说明

- 本目录存放**运行时生成**的数据（缓存、证据、记忆快照），与 `dataset/` 的**静态数据集**分离。
- 媒体文件（`.mp4`、`.wav`、`.png` 等）受 `.gitignore` 管理，不会进入版本控制。
- 历史数据资产整理产物见 [`dataset/_analysis/`](../dataset/_analysis/)。