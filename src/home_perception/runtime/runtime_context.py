"""Runtime 帧上下文（ADR-0039 · Runtime Entry Contract · 单容器进给）。

``RuntimeFrameContext`` 是 ``Pipeline.process_frame`` 的**唯一入参容器**，
与输出侧 ``FrameResult`` 对称（in/out dataclass 对）。多模态扩展走「新增 ADR →
扩展本 Context 字段」，不改方法签名——Context 是扩展边界，不是无限字段垃圾桶
（ADR-0039 Owner 修订：不预留 thermal/imu/door 等占位模态字段）。

字段（四字段冻结，schema 测试钉死）：

- ``video_frame``：视频帧（np.ndarray 等）；允许 ``None``（纯音频帧场景）；
- ``frame_index``：单调帧序号（非负 int；loop 重放时不回绕，见 gateway 帧循环契约）；
- ``case_time``：显式化伪时钟 ``frame_index × frame_interval_s``（秒）——
  gateway 三处重复计算的收敛单一事实源，也是 ADR-0041 时钟统一的锚点；
- ``audio_events``：本帧进给的音频感知事件元组（ADR-0026）。**本字段仅随 ctx 携带**，
  runtime 消费接线是 ADR-0042 的职责；接线前不进入任何 risk 链（硬门控：
  policy 升级前不接通 audio→risk，见 ADR-0040 D6）。

分层说明：本模块放 ``runtime/`` 而非 ADR 迁移步骤初稿所写的 ``core/``——
因 ``AudioPerceptionEvent`` 属 audio 业务层，core 不得反向依赖业务层
（AGENTS.md §1.1）；与 FrameResult 同层对称（实施偏差已在实现 PR 记录）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from ..audio.event import AudioPerceptionEvent

# 字段闭合基准（导入期 fail-closed 断言 + schema 测试逐值校验，防漂移）
RUNTIME_FRAME_CONTEXT_FIELDS: frozenset[str] = frozenset(
    {
        "video_frame",
        "frame_index",
        "case_time",
        "audio_events",
    }
)


@dataclass(frozen=True)
class RuntimeFrameContext:
    """单帧 runtime 进给容器（frozen；与 ``FrameResult`` 输出容器对称）。"""

    video_frame: Any | None
    frame_index: int
    case_time: float
    audio_events: tuple[AudioPerceptionEvent, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.frame_index, int) or isinstance(self.frame_index, bool):
            raise TypeError(
                "frame_index 必须是非 bool int，收到 "
                f"{type(self.frame_index).__name__}"
            )
        if self.frame_index < 0:
            raise ValueError(f"frame_index 必须 >= 0，收到 {self.frame_index}")
        if isinstance(self.case_time, bool) or not isinstance(self.case_time, (int, float)):
            raise TypeError(
                f"case_time 必须是数值，收到 {type(self.case_time).__name__}"
            )
        object.__setattr__(self, "case_time", float(self.case_time))
        if self.case_time < 0:
            raise ValueError(f"case_time 必须 >= 0，收到 {self.case_time}")
        if not isinstance(self.audio_events, tuple):
            raise TypeError(
                f"audio_events 必须是 tuple（C2 不可变容器），"
                f"收到 {type(self.audio_events).__name__}"
            )
        for i, ev in enumerate(self.audio_events):
            if not isinstance(ev, AudioPerceptionEvent):
                raise TypeError(
                    f"audio_events[{i}] 必须是 AudioPerceptionEvent，"
                    f"收到 {type(ev).__name__}"
                )


def _assert_contract_shape() -> None:
    """在**导入期**钉死字段形状（模仿 decision_contract.fail-closed 模式）。

    任何字段增删都会在 import 本模块的瞬间炸出来，强制走 ADR 流程
    （AGENTS.md §6.3.1：契约改动先提 ADR）。
    """
    names = {f.name for f in fields(RuntimeFrameContext)}
    if names != RUNTIME_FRAME_CONTEXT_FIELDS:
        raise RuntimeError(
            f"RuntimeFrameContext 字段集合漂移：实际 {sorted(names)}，"
            f"契约 {sorted(RUNTIME_FRAME_CONTEXT_FIELDS)}；"
            "增删字段必须先修订 ADR-0039（Context 是扩展边界，不是字段垃圾桶）"
        )


_assert_contract_shape()


__all__ = [
    "RUNTIME_FRAME_CONTEXT_FIELDS",
    "RuntimeFrameContext",
]