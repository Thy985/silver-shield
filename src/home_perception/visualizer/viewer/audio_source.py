"""ADR-0036 音频 E2E · Audio Source Adapter（只读解析层，非生成层）。

铁律（用户决策 —— 音频证据与可播放音频**严格分离**）：
- **只读解析**：本模块只"读" ``{base_dir}/{sid}/audio/manifest.json``，绝不"生成"音频文件。
- **证据 ≠ 媒体**（VM-9 / VM-10）：``EvidenceProjection.audio_evidence``（loader 投影产出）
  **绝不**持有任何 url / 媒体字节 / transcript——它只描述"系统听到了什么"（kind/score/
  confidence/labels/segments）。可播放的音频**样本**是独立的绑定层，经本 Adapter 经
  ``ref`` 解析，绝不进 View Model / EvidenceProjection（VM-10 / AC-11 同理）。
- VM-3：只 import stdlib + ``case_presentation.AudioSourceKind``，**绝不** import
  ``silver_demo`` / 生产 runtime（viewer/ 仍是 import 图死胡同叶子）。
- 媒体字节绝不进 View Model：``AudioManifest`` 只持 ``files``（kind → 相对 base 目录的
  url 模板），不持任何媒体字节；渲染层再叠加 ``audio_base_url`` 形成最终可解析 URL。

契约：
- ``resolve_audio_source(base_dir, scenario_id)``：
  - 读 ``{base_dir}/{sid}/audio/manifest.json``（kind → 相对 url 映射）；
  - 目录/文件缺失 → 返回 ``None``（无绑定音频样本，Case Viewer 不渲染播放控件，不崩）；
  - 结构非法（字段类型/缺关键字段/未知 source_kind）→ 抛 ``AudioSourceError``（fail-closed）。

版权/合规：样本 WAV 来自 ``src/home_perception/audio/tts/fixtures/``（确定性合成素材，
非真实设备录音）；manifest 不含任何"真实传感器"暗示。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

from home_perception.visualizer.viewer.case_presentation import AudioSourceKind

# 音频资产目录结构（对齐用户决策，与 media 对称）：
#   {base_dir}/{scenario_id}/audio/manifest.json
#   {base_dir}/{scenario_id}/audio/{kind}.wav   （可播放样本，由 prepare_case_audio 复制）
_AUDIO_SUBDIR = "audio"
_MANIFEST_FILENAME = "manifest.json"


class AudioManifest(TypedDict):
    """音频源解析结果（只读；不持任何媒体字节，VM-10/AC-11）。

    - ``source_kind``：固定 ``"AudioFileSource"``（当前唯一音频样本源）；
    - ``files``：音频感知 kind（``AudioPerceptionKind.value``）→ 相对 **audio base 目录** 的
      样本 URL。渲染层会再叠加 ``audio_base_url``（HTML→artifact 的相对路径）形成最终地址。
      只有确实存在的样本才入表；未命中的 kind 不编造（诚实降级）。
    """

    source_kind: AudioSourceKind
    files: dict[str, str]


class AudioSourceError(ValueError):
    """音频源 manifest 结构非法（fail-closed：拒绝产残缺 manifest）。"""


def _read_manifest_json(audio_dir: Path, owner: str) -> dict:
    p = audio_dir / _MANIFEST_FILENAME
    if not p.exists():
        raise FileNotFoundError(f"{owner} 无音频 manifest：{p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AudioSourceError(f"{owner} 音频 manifest 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise AudioSourceError(f"{owner} 音频 manifest 顶层非对象（fail-closed）")
    return data


def resolve_audio_source(
    base_dir: str | Path,
    scenario_id: str,
) -> AudioManifest | None:
    """只读解析音频样本 manifest（绝不生成音频）。

    Args:
        base_dir: artifact 根目录（内含 ``{scenario_id}/audio/``）。
        scenario_id: 场景标识（用于定位音频子目录）。

    Returns:
        ``AudioManifest``：解析成功；``None``：无绑定音频样本（降级，不崩）。

    Raises:
        AudioSourceError: manifest 结构非法（字段类型/缺关键字段/未知 source_kind，fail-closed）。
    """
    audio_dir = Path(base_dir) / scenario_id / _AUDIO_SUBDIR
    # 音频样本是 artifact 的可选组成部分：缺失即降级（不渲染播放控件），fail-open 不崩。
    if not audio_dir.exists():
        return None
    try:
        data = _read_manifest_json(audio_dir, f"audio[{scenario_id}]")
    except FileNotFoundError:
        return None

    kind = data.get("source_kind")
    if kind != "AudioFileSource":
        raise AudioSourceError(
            f"audio[{scenario_id}].source_kind 非法：{kind!r}（须为 AudioFileSource，fail-closed）"
        )

    files_raw = data.get("files", {})
    if not isinstance(files_raw, dict):
        raise AudioSourceError(f"audio[{scenario_id}].files 非对象（fail-closed）")

    files: dict[str, str] = {}
    for audio_kind, url in files_raw.items():
        if not isinstance(audio_kind, str) or not audio_kind:
            raise AudioSourceError(f"audio[{scenario_id}].files 键非法（fail-closed）")
        if not isinstance(url, str) or not url:
            raise AudioSourceError(
                f"audio[{scenario_id}].files[{audio_kind}] url 非法（fail-closed）"
            )
        # 路径穿越防护：拒绝 ".."（fail-closed，防越界读取 artifact 外文件）。
        if ".." in url:
            raise AudioSourceError(
                f"audio[{scenario_id}].files[{audio_kind}] 含非法路径片段 '..'（fail-closed）"
            )
        files[audio_kind] = url

    return AudioManifest(source_kind="AudioFileSource", files=files)


__all__ = [
    "AudioManifest",
    "AudioSourceError",
    "resolve_audio_source",
]
