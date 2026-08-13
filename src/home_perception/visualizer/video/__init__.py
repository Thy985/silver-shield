"""ADR-0035 D3 · Evidence Story Compiler 子包（visualizer/video/）。

公开 API：``CaseVideoSpec``（编排配置）/ ``generate_case_video``（8 阶段驱动）/
``CaseVideoResult``（产出摘要）。具体渲染/合成细节见各子模块。
"""

from home_perception.visualizer.video.compiler import CaseVideoResult, generate_case_video
from home_perception.visualizer.video.spec import CaseVideoSpec, NarrationCue

__all__ = [
    "CaseVideoResult",
    "CaseVideoSpec",
    "NarrationCue",
    "generate_case_video",
]
