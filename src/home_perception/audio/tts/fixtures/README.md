# tts/fixtures — 合成音频产物

本目录存放由 `scenario.yaml` + `generator.py` 生成的合成音频（WAV，16k mono 16-bit）。

- **来源可重现**：每条文件都对应 `scenario.yaml` 中一个 scenario（base_ref + effects 链），
  重新运行 `python -m home_perception.audio.tts.generator` 即可再生（效果确定性、固定 seed）。
- **离线生成**：默认 scenario 使用 `base_ref`（引用 `tests/fixtures/audio/*.wav`），
  无需 TTS / 网络；仅 `tts:` 形式的场景需要 `pip install -e ".[audio-dev]"` + 网络。
- **不入库说明**：这些 WAV 是派生产物，可由 scenario.yaml 再生；若需作为稳定资产提交，
  取消下方 `.gitignore` 例外或显式 `git add`。
