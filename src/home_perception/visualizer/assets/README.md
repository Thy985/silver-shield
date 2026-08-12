# Vendored Assets

## echarts.min.js (v5.5.1)

- **来源**: https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js
- **许可证**: Apache License 2.0（Apache Software Foundation）
- **用途**: ADR-0035 D4 自包含 HTML 的图视图（Cross Modal Graph）渲染；内联进
  `run_evidence_explorer.py` 输出，零外部网络依赖（浏览器直开）。
- **升级**: 替换文件 + 同步更新本说明与 ADR-0035 验收（确定性/脱敏不受影响——
  第三方资产不参与渲染正文判定，见 `tests/visualizer/test_renderer.py`）。

完整许可文本见 Apache 2.0 官方条款（与项目 LICENSE 兼容）。
