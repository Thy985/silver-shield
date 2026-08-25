"""构造 benign 正常通话音轨 ``normal_call_fixture.wav``（SSOT v3.2 收尾路径 · 步骤 A / F-1 方案）。

F-1 冻结方案（Owner 拍板）：telephone signaling + telephone-channel speech
+ normal conversational continuity → benign fixture。素材取自 Tier1 qualification
已实测的 candidates 池（TIER1-RUN1：LBJ top speech ≥ .95 且零 TEL 标签、signaling
recall 100%），使 C1 校验证据链与既有 qualification 证据直接可比。

拼接叙事（响铃 → 接听 → 正常通话 → 挂断）::

    [ring burst + 尾随静默] + [0.5s 接听静默] + [LBJ 窄带电话语音全段 + fade-out]

响铃段边界由 RMS 能量检测在 burst 后静默期切分（不在响铃中间截断产生 artifact）。
总时长 ≈ 22.5s（ring ~4s + 0.5s + LBJ 18s）：**素材真实完整性优先于与 mix.wav 的
30s 规格对称**——不循环拼接通话段引入非自然重复 artifact（F-1 教训：acoustic
semantics 对了才算对）；benign 场景 ``loop=false``，音频播完即止，时长非契约项。

用法::

    python scripts/build_benign_call_fixture.py build    # 构造 + 写 provenance + 自动校验
    python scripts/build_benign_call_fixture.py verify   # 仅校验已有资产（YAMNet C1 + 全链路）

退出码：0 = C1 校验通过；1 = 校验失败；2 = 用法错误。
"""

from __future__ import annotations

import csv
import json
import sys
import wave
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
CAND_DIR = ROOT / "dataset/_canonical/audio_semantic/qualification/tier2_real/_candidates/telephone"
OUT_DIR = ROOT / "dataset/_canonical/audio_semantic/product_story/benign_normal_call/audio"
# 选材依据：三候选中仅 Germany System 55 具备「t=0 即响铃 + 真零能量静默期」结构
# （Model_500 带混响底噪无真静默、Iskra ETA80 有 1s 前导静默且电平偏低 0.176），
# 且电平 0.689 与 LBJ 语音段匹配，无需增益。
RING_PATH = CAND_DIR / "telephone__ringtone__Ring_tone_Germany_System_55.wav"
SPEECH_PATH = CAND_DIR / "telephone__narrowband-speech__LBJ_FORD_phonecall_1963.wav"
OUT_WAV = OUT_DIR / "normal_call_fixture.wav"
PROVENANCE = OUT_DIR / "provenance.json"

MODEL_PATH = ROOT / "data/models/yamnet/onnx/yamnet_runtime.onnx"
CLASS_MAP_PATH = ROOT / "data/models/yamnet/yamnet_class_map.csv"

SR = 16000
ANSWER_SILENCE_S = 0.5  # 接听静默（响铃结束 → 通话开始）
FADE_S = 0.05  # 首尾淡入淡出，防 click artifact
AUDIT_THRESHOLD = 0.03  # 宽阈值审计扫描（低于生产 0.1，仅观察黑名单类底分，不影响判定）

# C1 硬黑名单：语义标签精确匹配（经 tagging.YAMNET_SEMANTIC_MAP 归并后）
SEMANTIC_BLACKLIST = {"crying", "distress", "scream", "shout"}
# 透传原始名关键词兜底（防语义映射未覆盖的 AudioSet 类透传漏网）
RAW_BLACKLIST_KEYWORDS = (
    "crying", "sobbing", "scream", "wail", "moan", "whimper", "shout", "yell", "anger",
)

# 正面软期望（缺失仅 WARN，不 FAIL——由 Owner 听检终裁）
RING_POSITIVE = ("telephone_ring", "ringtone", "telephone", "bell")
SPEECH_POSITIVE = ("speech", "conversation")


def read_wav_mono16k(path: Path) -> np.ndarray:
    """读取 PCM16 mono 16kHz wav 为 float32 [-1, 1]。"""
    with wave.open(str(path), "rb") as w:
        sr, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if (sr, ch, width) != (SR, 1, 2):
            raise ValueError(f"{path.name}: 期望 16kHz/mono/PCM16，实际 {sr}Hz/{ch}ch/{width * 8}bit")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def write_wav_mono16k(path: Path, samples: np.ndarray) -> None:
    """写出 PCM16 mono 16kHz wav。"""
    pcm = (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ring_cut_point(samples: np.ndarray) -> int:
    """RMS 能量检测：第一个响铃 burst 结束后的静默期切割点（返回样本索引）。

    在 burst 结束 +0.8s 处切割——落在自然静默期深部而非贴着铃声截断，
    叙事为「铃响一声、停顿、拿起听筒」。要求素材具备真零/低能量静默期。
    """
    hop, win = int(0.010 * SR), int(0.020 * SR)
    n = max(1, (len(samples) - win) // hop + 1)
    frames = samples[: n * hop].reshape(n, hop).astype(np.float64)
    rms = np.sqrt(np.mean(frames**2, axis=1))
    thr = float(rms.max()) * 0.25
    active = rms > thr
    on = int(np.argmax(active))  # 第一个 burst 起点
    low_run = int(0.3 * SR) // hop  # 连续 0.3s 低能量视为 burst 结束
    for i in range(on, n - low_run):
        if not active[i : i + low_run].any():
            return min(i * hop + int(0.8 * SR), len(samples))
    raise RuntimeError("未检测到响铃 burst 结束后的静默期；请人工检查素材或指定切割点")


def apply_fades(seg: np.ndarray, fade_in: bool, fade_out: bool) -> np.ndarray:
    """首尾线性淡入淡出（就地安全：调用方传入 copy）。"""
    nf = int(FADE_S * SR)
    if fade_in and len(seg) > nf:
        seg[:nf] *= np.linspace(0.0, 1.0, nf, dtype=np.float32)
    if fade_out and len(seg) > nf:
        seg[-nf:] *= np.linspace(1.0, 0.0, nf, dtype=np.float32)
    return seg


def build() -> dict[str, float]:
    """执行拼接并写出资产与 provenance，返回段边界信息。"""
    ring = read_wav_mono16k(RING_PATH)
    speech = read_wav_mono16k(SPEECH_PATH)

    cut = ring_cut_point(ring)
    ring_seg = apply_fades(ring[:cut].copy(), fade_in=True, fade_out=False)
    silence = np.zeros(int(ANSWER_SILENCE_S * SR), dtype=np.float32)
    speech_seg = apply_fades(speech.copy(), fade_in=False, fade_out=True)

    mix = np.concatenate([ring_seg, silence, speech_seg]).astype(np.float32)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_wav_mono16k(OUT_WAV, mix)

    bounds = {
        "ring_end_s": round(cut / SR, 3),
        "answer_silence_end_s": round((cut + len(silence)) / SR, 3),
        "duration_s": round(len(mix) / SR, 3),
    }
    provenance = {
        "composed_at": datetime.now(UTC).date().isoformat(),
        "purpose": (
            "SSOT v3.2 收尾路径步骤 A（F-1 冻结方案）：benign 正常通话音轨，"
            "供 product_story_benign.yaml 替换 synthetic 音源（步骤 C 成对冻结）"
        ),
        "recipe": "telephone signaling + telephone-channel speech + normal conversational continuity",
        "story_timeline_label_rule": (
            "默认 telephone_conversation_start（单方窄带电话录音，无双方可证轮流说话证据，"
            "不使用 bidirectional_speech_start）"
        ),
        "format": {
            "sample_rate": SR,
            "channels": 1,
            "bit_depth": 16,
            "codec": "pcm_s16le",
            "duration_s": bounds["duration_s"],
        },
        "boundaries": bounds,
        "sources": [
            {
                "segment_id": "telephone__ringtone__Ring_tone_Germany_System_55",
                "path": "qualification/tier2_real/_candidates/telephone/"
                "telephone__ringtone__Ring_tone_Germany_System_55.wav",
                "offset_in_fixture_s": 0.0,
                "duration_in_fixture_s": bounds["ring_end_s"],
                "source_duration_s": round(len(ring) / SR, 3),
                "cut_rule": "RMS 能量检测：第一响铃 burst 结束后 +0.8s（自然静默期深部）",
                "license_id": "CC BY-SA 3.0",
                "author": "Rene Böke",
                "attribution_ref": "qualification/tier2_real/ATTRIBUTION.md",
            },
            {
                "segment_id": "telephone__narrowband-speech__LBJ_FORD_phonecall_1963",
                "path": "qualification/tier2_real/_candidates/telephone/"
                "telephone__narrowband-speech__LBJ_FORD_phonecall_1963.wav",
                "offset_in_fixture_s": bounds["answer_silence_end_s"],
                "duration_in_fixture_s": round(len(speech) / SR, 3),
                "source_duration_s": round(len(speech) / SR, 3),
                "note": "Tier1 qualification 已实测版本（TIER1-RUN1：top speech≥.95，零 TEL/distress 标签）",
                "license_id": "Public domain",
                "author": "LBJ Presidential Library",
                "source_url": "https://upload.wikimedia.org/wikipedia/commons/a/a3/"
                "Telephone_conversation_586%2C_sound_recording%2C_LBJ_and_GERALD_FORD%2C_12-20-1963.wav",
            },
        ],
        "reproducibility": {
            "script": "scripts/build_benign_call_fixture.py",
            "command": "python scripts/build_benign_call_fixture.py build",
        },
    }
    PROVENANCE.write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print("[build] 拼接完成：")
    print(f"  ring   : 0.000 ~ {bounds['ring_end_s']:.3f}s ({RING_PATH.name})")
    print(f"  answer : {bounds['ring_end_s']:.3f} ~ {bounds['answer_silence_end_s']:.3f}s (silence)")
    print(f"  speech : {bounds['answer_silence_end_s']:.3f} ~ {bounds['duration_s']:.3f}s ({SPEECH_PATH.name})")
    print(f"  total  : {bounds['duration_s']:.3f}s @ {SR}Hz/mono/PCM16")
    print(f"  output : {OUT_WAV}")
    return bounds


def load_class_names(path: Path) -> list[str]:
    """加载 YAMNet class_map CSV（index,mid,display_name），按 index 排序。"""
    with open(path, newline="", encoding="utf-8") as f:
        rows = sorted(csv.DictReader(f), key=lambda r: int(r["index"]))
    return [r["display_name"] for r in rows]


def _fmt_tags(tags: list, limit: int = 10) -> str:
    if not tags:
        return "(无标签 ≥ 阈值)"
    return ", ".join(f"{t.label}={t.score:.3f}" for t in tags[:limit])


def check_blacklist(tags: list, scope: str) -> list[str]:
    """C1 黑名单检查：语义精确匹配 + 原始名关键词兜底。返回违规描述列表。"""
    failures = []
    for t in tags:
        if t.label in SEMANTIC_BLACKLIST or any(k in t.label.lower() for k in RAW_BLACKLIST_KEYWORDS):
            failures.append(f"[C1-FAIL] {scope}: 黑名单标签 {t.label!r} score={t.score:.3f}")
    return failures


def verify() -> bool:
    """对已生成资产跑 C1 校验：YAMNet 分层推理 + AudioPipeline 全链路。"""
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    from home_perception.audio.event import AudioPerceptionEvent, AudioPerceptionKind
    from home_perception.audio.pipeline import AudioPipeline
    from home_perception.audio.tagging import YamNetTagger

    if not OUT_WAV.exists():
        print(f"[verify] 资产不存在：{OUT_WAV}；请先执行 build")
        return False

    tagger = YamNetTagger(model_path=str(MODEL_PATH), class_names=load_class_names(CLASS_MAP_PATH))
    samples = read_wav_mono16k(OUT_WAV)
    failures: list[str] = []

    # ---- 1) 全段推理（生产阈值 0.1）----
    full_tags = tagger.tag(samples, SR)
    print("\n[verify] 全段 YAMNet top-10（threshold=0.1）:")
    print(f"  {_fmt_tags(full_tags)}")
    failures += check_blacklist(full_tags, "full")

    # ---- 2) 分段推理 ----
    prov = json.loads(PROVENANCE.read_text(encoding="utf-8"))
    b = prov["boundaries"]
    ring_tags = tagger.tag(samples[: int(b["ring_end_s"] * SR)], SR)
    speech_tags = tagger.tag(samples[int(b["answer_silence_end_s"] * SR) :], SR)
    print(f"\n[verify] ring 段（0 ~ {b['ring_end_s']}s）top-5:")
    print(f"  {_fmt_tags(ring_tags, 5)}")
    print(f"\n[verify] speech 段（{b['answer_silence_end_s']}s ~ 末尾）top-5:")
    print(f"  {_fmt_tags(speech_tags, 5)}")
    failures += check_blacklist(ring_tags, "ring")
    failures += check_blacklist(speech_tags, "speech")

    # ---- 3) 正面软期望（WARN 不 FAIL）----
    if not any(t.label in RING_POSITIVE for t in ring_tags):
        print("[verify][WARN] ring 段未命中电话铃类正面期望（telephone_ring/ringtone/telephone/bell）")
    if not any(t.label in SPEECH_POSITIVE for t in speech_tags):
        print("[verify][WARN] speech 段未命中语音类正面期望（speech/conversation）")

    # ---- 4) 宽阈值审计扫描（观察黑名单类真实底分，不参与判定）----
    audit = YamNetTagger(
        model_path=str(MODEL_PATH),
        class_names=load_class_names(CLASS_MAP_PATH),
        threshold=AUDIT_THRESHOLD,
        top_k=30,
    )
    audit_hits = [t for t in audit.tag(samples, SR) if check_blacklist([t], "audit")]
    if audit_hits:
        print(f"\n[audit] 宽阈值({AUDIT_THRESHOLD})下黑名单类出现（仅观察，不判 FAIL）:")
        for line in audit_hits:
            print(f"  {line.split('] ', 1)[1]}")
    else:
        print(f"\n[audit] 宽阈值({AUDIT_THRESHOLD})审计：黑名单类零出现")

    # ---- 5) 全链路管道（Tier0 规则 + 触发式 Tier1）----
    pipe = AudioPipeline.from_defaults(OUT_WAV, tagger=tagger)
    events: list[AudioPerceptionEvent] = pipe.run_path(OUT_WAV)
    kinds = Counter(ev.kind.value for ev in events)
    print(f"\n[verify] AudioPipeline 全链路事件（{len(events)} 条）:")
    for kind, cnt in sorted(kinds.items()):
        print(f"  {kind}: {cnt}")
    for ev in events:
        scored = ", ".join(f"{t.label}={t.score:.3f}" for t in ev.scored_labels) or "-"
        print(f"  - t={ev.timestamp:.2f}s kind={ev.kind.value} rule_labels={ev.labels} tier1=[{scored}]")

    # distress_cry 处置：C1 关注的是**素材**语义合格性。
    # - Tier1 佐证必须取 ``scored_labels``（契约上仅承载 Tier1 声学标签，pipeline.py
    #   「seg.labels 与 ev.labels 语义层不同」注记）；``labels`` 是 Tier0 规则标签 ∪
    #   Tier1 标签的并集，不能作为 Tier1 佐证。
    # - Tier0 Energy backend 已知 tremor 重定义塌缩缺陷（P2 挂账）会把正常窄带电话
    #   语音误判 distress_cry——那是 Runtime 缺陷而非素材缺陷：如实登记，不阻塞素材
    #   合格判定，留给六元组三方对照（步骤 B/C）与 P2 修复暴露。
    for ev in events:
        if ev.kind == AudioPerceptionKind.AUDIO_DISTRESS_CRY:
            tier1_evidence = {t.label for t in ev.scored_labels} & SEMANTIC_BLACKLIST
            if tier1_evidence:
                failures.append(
                    f"[C1-FAIL] pipeline: distress_cry 事件 t={ev.timestamp:.2f}s "
                    f"带 Tier1 佐证 {sorted(tier1_evidence)}（素材不合格）"
                )
            else:
                print(
                    f"[verify][WARN-P2] distress_cry 事件 t={ev.timestamp:.2f}s "
                    f"scored_labels={{{', '.join(sorted({t.label for t in ev.scored_labels})) or '空'}}} "
                    f"无 Tier1 黑名单佐证 → Tier0 Energy backend 已知塌缩缺陷（P2 挂账），"
                    f"登记不阻塞素材合格判定"
                )

    print("\n[verify] ===== C1 判定 =====")
    if failures:
        for line in failures:
            print(line)
        print("[verify] 结果: FAIL")
        return False
    print("[verify] 结果: PASS（全段/分段/pipeline 三层黑名单零命中）")
    return True


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "build"
    if cmd == "build":
        build()
        return 0 if verify() else 1
    if cmd == "verify":
        return 0 if verify() else 1
    print(f"用法: python {Path(__file__).name} [build|verify]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))