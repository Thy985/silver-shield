"""Tier1 声学标签器（ADR-0026 §3 Tier 1 · YAMNet 可选增强）。

> Tier1 是 **config 可选增强，默认关闭**；开启后由 Tier0 **触发式拉起**——仅对 VAD 检出的
> 语音段跑一次 YAMNet，把 AudioSet 521 类映射为有用的声学标签，并入
> ``AudioSegmentEvent.labels`` / ``AudioPerceptionEvent.labels``。
>
> 设计同 ``VadBackend``：``AcousticTagger`` ABC + ``YamNetTagger``（真实加载器，惰性 import
> ``onnxruntime``，仅 enabled 且权重存在时跑真实推理）+ ``StubAcousticTagger``（确定性假标签，
> 供 CI / 测试 / 缺权重回退）。**零运行时硬依赖**：``onnxruntime`` / ``scipy`` 仅在真实路径
> 按需 lazy import，核心管道仍是纯 numpy。

边界铁律（ADR-0001 / ADR-0026）：标签器只产出**声学感知标签**（speech / telephone / crying /
alarm ...），**绝不产出** fraud / suspect / verdict 等犯罪认定——那是中心风控与 ``DecisionPolicy`` 的事。
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..common.logging import get_logger

log = get_logger(__name__)


@dataclass
class AudioTag:
    """单个声学标签（Tier1 产出）。"""

    label: str  # 语义标签，如 "telephone" / "crying" / "alarm"
    score: float = 0.0  # 该标签置信分 0~1（来自 YAMNet 帧平均 score）


class AcousticTagger(ABC):
    """Tier1 声学标签器接口。"""

    name: str = "base"

    @abstractmethod
    def tag(self, samples: np.ndarray, sample_rate: int) -> list[AudioTag]:
        """对一段单声道音频产出声学标签。

        ``samples`` 为 float 波形（已去均值与否皆可），``sample_rate`` 为采样率 Hz。
        实现须对空音频返回 ``[]`` 而非抛异常（便于管道失败隔离）。
        """
        raise NotImplementedError


# AudioSet / YAMNet 中与居家安全场景最相关的类 → 语义标签的精选映射。
# 真实 ``YamNetTagger`` 用模型 521 类 score 输出，取高于阈值的类，再经此表归并语义标签；
# 不在表中的高频类会以其 AudioSet 原始名（小写、空格转下划线）透传，保证信息不丢。
YAMNET_SEMANTIC_MAP: dict[str, str] = {
    # 语音 / 人声
    "Speech": "speech",
    "Male speech, man speaking": "speech",
    "Female speech, woman speaking": "speech",
    "Child speech, kid speaking": "speech",
    "Whispering": "whisper",
    "Shouting": "shout",
    # 电话 / 通信
    "Telephone": "telephone",
    "Telephone bell ringing": "telephone_ring",
    "Ringtone": "ringtone",
    # 哭诉 / 求助 / 痛苦
    "Crying, sobbing": "crying",
    "Wail, moan": "distress",
    "Screaming": "scream",
    "Whimper": "distress",
    # 警报 / 紧急
    "Smoke alarm": "alarm",
    "Fire alarm": "alarm",
    "Siren": "siren",
    "Alarm": "alarm",
    "Civil defense siren": "siren",
    # 背景 / 环境
    "Silence": "silence",
    "Noise": "noise",
    "Babbling": "babble",
    "Traffic noise, roadway noise": "traffic",
    "Music": "music",
    "Laughter": "laughter",
    "Applause": "applause",
    "Cheering": "cheering",
    "Cough": "cough",
    "Sneeze": "sneeze",
    "Snoring": "snore",
    # 玻璃 / 冲击（入侵 / 意外）
    "Glass": "glass",
    "Breaking": "breaking",
    "Crash": "crash",
}


class StubAcousticTagger(AcousticTagger):
    """确定性假标签器（CI / 测试 / 缺权重回退）。

    区分两种角色，请用对应子类以避免混淆（评审 1.4）：
    - :class:`EnergyStubAcousticTagger`：无参，依能量判定（有能量→``speech`` / 静音→``silence``），
      **仅作缺权重回退**，绝不用于生产推理。
    - :class:`FixedStubAcousticTagger`：固定返回给定标签，仅用于**测试断言精确标签**。

    基类构造函数保留 ``labels`` 参数以维持向后兼容（测试可直接用基类）；新代码请优先用子类。
    """

    name = "stub"

    def __init__(self, labels: list[str] | None = None) -> None:
        # labels 非空时固定返回（测试可断言精确标签）；为 None 时走确定性回退。
        self._fixed = list(labels) if labels is not None else None

    def tag(self, samples: np.ndarray, sample_rate: int) -> list[AudioTag]:
        if self._fixed is not None:
            return [AudioTag(label=l, score=1.0) for l in self._fixed]
        # 确定性回退：有能量即标 "speech"（VAD 段本质已是语音），否则 "silence"
        rms = float(np.sqrt(np.mean(samples**2) + 1e-12)) if len(samples) else 0.0
        if rms <= 1e-4:
            return [AudioTag("silence", 1.0)]
        return [AudioTag("speech", 1.0)]


class EnergyStubAcousticTagger(StubAcousticTagger):
    """缺权重回退 stub：依能量判定（``speech`` / ``silence``）。**仅回退用，非生产推理。**"""

    name = "stub-energy"

    def __init__(self) -> None:
        super().__init__(labels=None)


class FixedStubAcousticTagger(StubAcousticTagger):
    """测试用 stub：固定返回给定标签，供精确断言。"""

    name = "stub-fixed"

    def __init__(self, labels: list[str]) -> None:
        super().__init__(labels=list(labels))


class YamNetTagger(AcousticTagger):
    """YAMNet 真实标签器（惰性加载 ``onnxruntime``，config-gated）。

    设计取舍（ADR-0026 §3）：Tier1 是可选增强，默认关闭；仅当 config 开启且权重文件存在时
    才实例化并加载模型。``onnxruntime`` 为 lazy import，不进入核心依赖。

    规范：输入 16kHz mono；模型输出帧级 521 类 score → 段级取帧平均 → 取 top-k 高于阈值的类
    → 经 ``YAMNET_SEMANTIC_MAP`` 归并语义标签；未命中映射的高频类以其 AudioSet 原始名
    （小写、空格转下划线）透传。

    权重与类映射：``model_path`` 指向 .onnx；``class_names`` 为 521 条 AudioSet 类名列表
    （顺序与模型输出对齐），可由用户随权重提供的类映射给出。本项目内嵌一份精选子集映射
    （``YAMNET_SEMANTIC_MAP``）用于语义归并，未提供的类沿原始名透传。
    """

    name = "yamnet"

    def __init__(
        self,
        model_path: str,
        class_names: list[str] | None = None,
        threshold: float = 0.1,
        top_k: int = 10,
        target_sr: int = 16000,
        frame_s: float = 0.96,
        hop_s: float = 0.48,
    ) -> None:
        if not model_path or not str(model_path).strip():
            raise ValueError("YamNetTagger 需要 model_path（.onnx 权重路径）")
        self.model_path = self._validate_model_path(model_path)
        if class_names is None:
            # 未随权重提供 521 类名 → 语义归并退化为 class_N，下游不可解释；
            # 显式告警让 ops 补全，而非静默吞掉（评审 1.2）。
            log.warning(
                "audio.tier1.class_names_missing",
                model_path=self.model_path,
                note="提供 class_names 以恢复 521 类语义标签",
            )
        self.class_names = class_names
        self.threshold = threshold
        self.top_k = top_k
        self.target_sr = target_sr
        self.frame_s = frame_s
        self.hop_s = hop_s
        self._session = None  # 惰性加载

    @staticmethod
    def _validate_model_path(path: str) -> Path:
        """路径安全校验（评审 3.1）：必须是 ``.onnx`` 且拒绝路径遍历（``..`` / 越权）。

        ``model_path`` 应仅由运维受控配置提供，切勿接受用户输入。
        """
        p = Path(path)
        if p.suffix.lower() != ".onnx":
            raise ValueError(f"YamNet model_path 必须是 .onnx 文件，收到 {path!r}")
        if ".." in p.parts:
            raise ValueError(f"YamNet model_path 拒绝路径遍历，收到 {path!r}")
        return path  # 校验通过；保留配置原始字符串（避免 Path 平台相关归一化改写入库路径）

    # ---- 惰性加载 ----

    def _ensure_session(self):
        if self._session is not None:
            return self._session
        try:
            import onnxruntime as ort  # lazy：仅真实路径 import
        except ImportError as exc:
            raise RuntimeError(
                "YamNetTagger 需要 onnxruntime（pip install -e '.[audio]'）；"
                "或关闭 audio.tier1.enabled 回退 Stub"
            ) from exc
        path = Path(self.model_path)
        if not path.exists():
            raise FileNotFoundError(
                f"YAMNet 权重不存在：{self.model_path}；请放置 .onnx 或关闭 audio.tier1.enabled"
            )
        self._session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
        return self._session

    # ---- 输入形状校验（Gate 4 真实缺陷 4B 根因）----

    @staticmethod
    def _validate_input_shape(sess) -> None:
        """加载后校验 YAMNet ONNX 输入形状。

        不同导出对输入 rank/dim 约定不一，喂错会在推理期抛 INVALID_ARGUMENT
        （``Got invalid dimensions ... Expected: 1``），且被管道的失败隔离静默吞掉
        （表现为 tier1_failed、无有效标签）。此处显式拒绝退化导出并给出可执行的修正指向，
        把"静默失败"变成"加载/首推阶段的可读错误"。

        - 合法：rank-1 动态（``[samples]`` / ``[-1]`` / 变量名维度）或 rank-2（``[1, samples]`` /
          ``[None, N]``）。``_run_frames`` 已按声明 rank 自适应喂入。
        - 退化：rank-1 但为固定单元素 ``[1]``（如 ``yamnet.onnx`` 的错误导出）——无法接受变长音频，
          任何喂法都会 INVALID_ARGUMENT，必须拒绝。
        - 无 shape 信息（测试替身 / 运行期未暴露）→ 跳过，交由运行时 rank-2 兜底行为。
        """
        inp = sess.get_inputs()[0]
        shape = getattr(inp, "shape", None)
        if shape is None:
            return
        if len(shape) == 1:
            dim = shape[0]
            if isinstance(dim, int) and dim == 1:
                raise ValueError(
                    f"YAMNet ONNX 输入形状退化（固定 [1]，无法接受变长音频）："
                    f"{inp.name}{list(shape)}。请改用正确导出"
                    f"（本项目为 data/models/yamnet/onnx/yamnet_runtime.onnx，输入为动态 [samples]）。"
                )
            return  # 动态 rank-1（[samples] / [-1] / 变量名维度）→ OK
        if len(shape) == 2:
            return  # [1, samples] / [None, N] → OK
        raise ValueError(
            f"YAMNet ONNX 输入形状不支持（期望 rank 1 或 2，收到 rank {len(shape)}）："
            f"{inp.name}{list(shape)}"
        )

    # ---- 推理 ----

    def tag(self, samples: np.ndarray, sample_rate: int) -> list[AudioTag]:
        # 防御 NaN/Inf（评审 2.3）：上游异常可能传入含 NaN 的波形，直接置 0 避免
        # mean_scores 出现 NaN 导致 argsort 平台相关的行为与不可解释 score。
        samples = np.nan_to_num(np.asarray(samples, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
        if len(samples) == 0:
            return []
        sess = self._ensure_session()
        self._validate_input_shape(sess)
        wav = self._resample_to(samples, sample_rate, self.target_sr)
        scores = self._run_frames(sess, wav, self.target_sr)
        if len(scores) == 0:
            return []
        mean_scores = np.mean(scores, axis=0)  # 段级：[521]
        idx = np.argsort(mean_scores)[::-1][: self.top_k]
        tags: list[AudioTag] = []
        for i in idx:
            s = float(mean_scores[i])
            if s < self.threshold:
                break
            raw = self._class_name(i)
            semantic = YAMNET_SEMANTIC_MAP.get(raw, self._slug(raw))
            tags.append(AudioTag(semantic, s))
        return tags

    @staticmethod
    def _slug(raw: str) -> str:
        return raw.lower().replace(" ", "_")

    def _class_name(self, idx: int) -> str:
        if self.class_names and idx < len(self.class_names):
            return self.class_names[idx]
        return f"class_{idx}"

    @staticmethod
    def _resample_to(samples: np.ndarray, sr: int, target: int) -> np.ndarray:
        """重采样到目标采样率（YAMNet 规范 16kHz）。

        真实推理路径（已安装 ``[audio]`` extra 的 scipy）使用 ``scipy.signal.resample_poly``
        带抗混叠 FIR 滤波，是该领域标准做法；scipy 缺失时退化为 ``np.interp`` 线性插值近似
        （仅开发/CI 异常情形，生产应保证安装 scipy）。二者均对外保证 float32 输出。
        """
        samples = np.asarray(samples, dtype=np.float32)
        if sr == target:
            return samples
        try:
            from scipy.signal import resample_poly

            g = math.gcd(int(target), int(sr))
            return resample_poly(samples, int(target) // g, int(sr) // g).astype(np.float32)
        except ImportError:
            # 退化路径：非 anti-alias 的线性插值（无 scipy 时）；精度低于 resample_poly。
            n = max(1, round(len(samples) * target / sr))
            x = np.linspace(0, len(samples) - 1, n)
            return np.interp(x, np.arange(len(samples)), samples).astype(np.float32)

    def _run_frames(self, sess, wav: np.ndarray, sr: int) -> np.ndarray:
        """切 0.96s 帧（hop 0.48s）逐帧推理，返回 [frames, 521] 段级 score 矩阵。"""
        frame = int(self.frame_s * sr)
        hop = int(self.hop_s * sr)
        # 钳位到 [-1, 1] 防溢出（评审 3.3：int16→float32 未钳位可能触发 ONNX 异常路径）
        wav = np.clip(np.asarray(wav, dtype=np.float32), -1.0, 1.0)
        if len(wav) < frame:
            # 不足一帧则补零到整帧；补零后长度必 ≥ frame，无需二次判空（评审 2.1）。
            wav = np.pad(wav, (0, frame - len(wav)))
        inp = sess.get_inputs()[0]
        # 输入 rank 自适应（真实权重验证发现）：不同 ONNX 导出对 waveform 的 rank 不一致——
        # TF/官方导出常为 [batch, samples]（rank2），PINTO 等导出为 [samples]（rank1，声明 shape ["samples"]）。
        # 喂错 rank 会触发 INVALID_ARGUMENT（Got: 2 Expected: 1），故按模型声明自适应；
        # 无 shape 信息的替身 session（测试）回退到原 rank-2 行为。
        _shape = getattr(inp, "shape", None)
        expects_rank2 = _shape is None or len(_shape) == 2
        # 帧起点：标准 hop 步进 + 末尾补一帧覆盖尾部（评审 2.2：非 2×hop 整数倍时尾段会漏）
        starts = list(range(0, len(wav) - frame + 1, hop))
        last = len(wav) - frame
        if last >= 0 and last not in starts:
            starts.append(last)
        out: list[np.ndarray] = []
        for start in starts:
            chunk = wav[start : start + frame].astype(np.float32)
            feed = chunk[None, :] if expects_rank2 else chunk
            res = sess.run(None, {inp.name: feed})
            # YAMNet 输出 scores 为 [frames, 521]；取该帧
            out.append(np.asarray(res[0]).reshape(-1, 521)[0])
        return np.stack(out) if out else np.empty((0, 521))


def load_class_names(class_map_path: str) -> list[str]:
    """加载 YAMNet 521 类 AudioSet 类名映射文件（csv/yaml/json）。

    支持格式：
    - **.csv**（AudioSet 官方分发格式，如 ``yamnet_class_map.csv``）：须含
      ``display_name`` 列（可选 ``index`` 列，存在时校验 0..520 连续——保证
      行序与模型输出索引严格对齐）；
    - **.yaml / .json**：521 条字符串列表。

    **fail-fast 契约（ADR-0042 步骤 6 · class_N 透传缺陷修复入口）**：空路径 /
    非法后缀 / 路径遍历 / 文件缺失 / 内容格式错 / 长度 ≠ 521 一律显式 raise。
    静默退化（class_names=None → ``class_N`` 透传）正是原缺陷教训：下游无法把
    ``class_N`` 归入语义映射，kind 分类全落 fallback，D4 MONITOR ceiling 因此
    永不可解除。521 与 ``_run_frames`` 的输出维度硬编码保持一致。

    Returns:
        521 条 AudioSet 类名（顺序对齐模型输出索引）。
    """
    if not class_map_path or not str(class_map_path).strip():
        raise ValueError("class_map_path 不能为空（留空 = 用内嵌精选子集，请显式二选一）")
    p = Path(class_map_path)
    if p.suffix.lower() not in (".csv", ".yaml", ".yml", ".json"):
        raise ValueError(
            f"class_map_path 必须是 .csv/.yaml/.yml/.json，收到 {class_map_path!r}"
        )
    if ".." in p.parts:
        raise ValueError(f"class_map_path 拒绝路径遍历，收到 {class_map_path!r}")
    if not p.exists():
        raise FileNotFoundError(
            f"class_map 文件不存在：{class_map_path}；"
            "请提供 AudioSet 521 类名列表（顺序对齐模型输出）或留空用内嵌精选子集"
        )
    if p.suffix.lower() == ".csv":
        return _load_class_names_csv(p)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        import json

        data = json.loads(raw)
    else:
        import yaml  # lazy：仅 class_map 加载路径需要

        data = yaml.safe_load(raw)
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(
            "class_map 文件内容必须是字符串列表（AudioSet 类名，顺序对齐模型输出索引），"
            f"收到 {type(data).__name__}"
        )
    if len(data) != 521:
        raise ValueError(
            f"class_map 必须包含 521 条类名（YAMNet 输出维度），收到 {len(data)}；"
            "请核对权重导出配套的 class_map（如 yamnet_class_map.csv）转换产物"
        )
    return data


def _load_class_names_csv(p: Path) -> list[str]:
    """解析 AudioSet 官方 CSV（``index,mid,display_name``）→ display_name 列表。"""
    import csv

    with p.open(encoding="utf-8-sig", newline="") as f:  # utf-8-sig：容忍导出工具加 BOM
        reader = csv.DictReader(f)
        if not reader.fieldnames or "display_name" not in reader.fieldnames:
            raise ValueError(
                "class_map CSV 缺少 display_name 列（期望表头 index,mid,display_name），"
                f"收到 {reader.fieldnames}"
            )
        rows = list(reader)
    names = [(r.get("display_name") or "").strip() for r in rows]
    if any(not n for n in names):
        raise ValueError("class_map CSV 存在空 display_name（行序即模型输出索引，不得留空）")
    idx_header = next(
        (h for h in (reader.fieldnames or []) if h.strip().lower() == "index"), None
    )
    if idx_header is not None:
        try:
            indices = [int(r[idx_header]) for r in rows]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"class_map CSV index 列必须为整数序列：{exc}") from exc
        if indices != list(range(len(indices))):
            raise ValueError(
                f"class_map CSV index 必须从 0 连续递增（保证与模型输出对齐），"
                f"收到首尾 {indices[:2]}... 共 {len(indices)} 条"
            )
    if len(names) != 521:
        raise ValueError(
            f"class_map 必须包含 521 条类名（YAMNet 输出维度），收到 {len(names)}；"
            "请核对随权重分发的 yamnet_class_map.csv 是否完整"
        )
    return names


def build_tagger(
    cfg: object,
) -> AcousticTagger | None:
    """从 Tier1 配置构建标签器（工厂）。

    分支：
    - ``enabled`` 为 False → 返回 ``None``（管道不跑 Tier1，labels 仅 Tier0）。
    - ``enabled`` 且 ``model_path`` 非空 → ``YamNetTagger``（真实推理）；
      ``class_map_path`` 非空时经 :func:`load_class_names` 加载 521 类名传入
      （恢复语义归并——否则 521 类退化为 ``class_N``，下游不可解释）。
    - ``enabled`` 但 ``model_path`` 为空（缺权重）→ ``EnergyStubAcousticTagger`` 回退
      （保证 config 开启即能用，不因缺权重崩；生产应配真实权重）。

    ``cfg`` 为 duck-typed（接受 ``Tier1AudioConfig``，避免 audio 包耦合 core.config）。
    """
    enabled = bool(getattr(cfg, "enabled", False))
    if not enabled:
        return None
    model_path = str(getattr(cfg, "model_path", "") or "")
    if model_path.strip():
        # ADR-0042 步骤 6 修复：class_map_path 此前从未被消费（恒 class_N 透传）。
        class_map_path = str(getattr(cfg, "class_map_path", "") or "")
        class_names = (
            load_class_names(class_map_path) if class_map_path.strip() else None
        )
        return YamNetTagger(
            model_path=model_path,
            class_names=class_names,
            threshold=float(getattr(cfg, "threshold", 0.1)),
            top_k=int(getattr(cfg, "top_k", 10)),
            target_sr=int(getattr(cfg, "target_sr", 16000)),
        )
    # 开启但缺权重：确定性 Stub 回退（不崩、可测试）
    return EnergyStubAcousticTagger()


def tier1_trigger_of(cfg: object) -> str:
    """从 Tier1 配置提取触发策略（duck-typed，集中对 cfg 形状的依赖，评审 1.3）。

    返回 ``segment`` / ``perception``；缺省为 ``segment``。白名单校验由
    ``AudioPipeline.__init__`` 与 ``Tier1AudioConfig`` 统一引用 ``core.config.TIER1_TRIGGERS`` 完成。
    """
    tier1 = getattr(cfg, "tier1", None) or cfg  # 若无 .tier1 包装，则 cfg 本身即 Tier1 配置
    if tier1 is None:
        return "segment"
    return str(getattr(tier1, "trigger", "segment"))
