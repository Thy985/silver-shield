"""TTS Provider 抽象（Audio Synthetic Infrastructure / ``tts`` 包子模块）。

> 本模块属 Testing / Evaluation Infrastructure，不是 Audio Perception Chain 的一环。
> 用于生成音频测试 fixture（TTS → WAV），与 Memory replay dataset / E-1 synthetic generator 同构。
> 默认 ``EdgeTTSProvider``（免费、无密钥、CI 友好）；可替换为 Azure / 本地模型而不改 fixture 定义。
>
> 本模块**仅被 ``tts`` 包与 ``scripts/gen_audio_fixtures.py`` 使用**，不被 ``audio`` 包运行时 import
> （保持运行时零 TTS 依赖）。``edge_tts`` 为可选 dev 依赖，惰性 import。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    """音频测试素材的 TTS 抽象。实现可替换，fixture 定义与具体 TTS 解耦。"""

    @abstractmethod
    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float,  # 语速倍率，1.0=正常，>1 更快
        pitch: float,  # 音高偏移，0.0=正常，>0 更高
        out_path: Path,
    ) -> Path:
        """将文本合成为音频文件，返回文件路径。子类负责具体后端。"""
        ...

    def synthesize_bytes(
        self, text: str, voice: str, rate: float, pitch: float
    ) -> bytes:
        """将文本合成为音频字节（内存，不落盘）。默认抛 NotImplemented；子类可重写。"""
        raise NotImplementedError


class EdgeTTSProvider(TTSProvider):
    """CI 默认实现：Microsoft Edge TTS，免费、无需密钥、可脚本化。

    产出 MP3；由调用方（生成器）统一转 WAV 提交为 fixture。
    """

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float,
        pitch: float,
        out_path: Path,
    ) -> Path:
        # 惰性 import：仅在使用时要求 edge-tts 已安装（dev 依赖）
        try:
            import edge_tts  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "EdgeTTSProvider 需要 edge-tts（dev 依赖）。请 `pip install edge-tts`。"
            ) from exc

        # edge-tts 的 rate/pitch 格式：相对百分比，如 "+40%" / "+10Hz"
        rate_str = f"{'+' if rate >= 1 else ''}{round((rate - 1.0) * 100)}%"
        # pitch 按半音近似：edge-tts 用 Hz，1 半音 ≈ 100 cents；这里简单映射 pitch 偏移到 Hz
        pitch_hz = round(pitch * 100)  # pitch=1.0 → 0Hz；pitch=1.2 → +20Hz
        pitch_str = f"{'+' if pitch_hz >= 0 else ''}{pitch_hz}Hz"

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            communicate = edge_tts.Communicate(
                text=text, voice=voice, rate=rate_str, pitch=pitch_str
            )
            await communicate.save(str(out_path))

        asyncio.run(_run())
        return out_path

    def synthesize_bytes(
        self, text: str, voice: str, rate: float, pitch: float
    ) -> bytes:
        try:
            import edge_tts  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "EdgeTTSProvider 需要 edge-tts（dev 依赖）。请 `pip install edge-tts`。"
            ) from exc

        rate_str = f"{'+' if rate >= 1 else ''}{round((rate - 1.0) * 100)}%"
        pitch_hz = round(pitch * 100)
        pitch_str = f"{'+' if pitch_hz >= 0 else ''}{pitch_hz}Hz"

        async def _run() -> bytes:
            communicate = edge_tts.Communicate(
                text=text, voice=voice, rate=rate_str, pitch=pitch_str
            )
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    chunks.append(chunk["data"])
            return b"".join(chunks)

        return asyncio.run(_run())


class AzureTTSProvider(TTSProvider):
    """生产级实现：Azure Speech（需 AZURE_SPEECH_KEY/REGION，密钥走 ENV，不硬编码）。

    留作可选后端；本期 fixture 生成默认用 EdgeTTSProvider。
    """

    def synthesize(
        self,
        text: str,
        voice: str,
        rate: float,
        pitch: float,
        out_path: Path,
    ) -> Path:  # pragma: no cover - 需 Azure 凭证，CI 不走此路径
        raise NotImplementedError(
            "AzureTTSProvider 需接入 Azure Speech SDK（AZURE_SPEECH_KEY/REGION）。"
            "本期 fixture 生成请用 EdgeTTSProvider。"
        )
