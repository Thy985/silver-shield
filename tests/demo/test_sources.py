"""P0-11.3 帧源适配测试：VideoFileFrameSource / CaviarJpgFrameSource / 工厂分发。

验证："把 CAVIAR 帧源换成 VideoFileFrameSource（真实 MP4），Dashboard/Pipeline/WarningEvent 零改动"
—— 网关只依赖 DemoFrameSource 抽象，切换输入源仅需改场景配置。

冻结合规：本测试与 silver_demo 同源，仅消费白名单（runtime.config 经 silver_demo.sources 间接）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from silver_demo.scenarios import ScenarioConfig, load_scenario
from silver_demo.sources import (
    CaviarJpgFrameSource,
    DemoFrameSource,
    VideoFileFrameSource,
    build_frame_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CAVIAR_BASE = REPO_ROOT / "tests" / "fixtures" / "doorway"


def _caviar_frames_present(scenario: str = "one_leave_reenter") -> bool:
    """CAVIAR jpg 帧 fixture 是否就位（*.jpg 被 gitignore，需先跑 download_fixtures.py）。"""
    d = CAVIAR_BASE / scenario
    return d.is_dir() and bool(list(d.glob("frame_*.jpg")))


def _make_synthetic_mp4(path: Path, n: int = 10, w: int = 320, h: int = 240, fps: int = 8) -> None:
    """生成合成 MP4（彩色帧），用于验证 VideoFileFrameSource 不依赖真实素材。"""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("无法创建测试 MP4（cv2.VideoWriter 打开失败）")
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:, :] = ((i * 25) % 255, 80, 80)
        writer.write(frame)
    writer.release()


# ============================================================================
# VideoFileFrameSource（真实 MP4 适配器）
# ============================================================================


def test_video_file_frame_source_yields_frames(tmp_path) -> None:
    mp4 = tmp_path / "syn.mp4"
    _make_synthetic_mp4(mp4, n=10)

    src = VideoFileFrameSource(str(mp4), fps_target=8)
    # 帧数探测（mp4v 容器一般可取得；个别构建可能返回 -1，均合法）
    assert src.frame_count in (10, -1)

    frames = list(src)
    assert len(frames) == 10
    ts, frame = frames[0]
    assert isinstance(ts, float)
    assert frame.shape == (240, 320, 3)
    assert frame.dtype == np.uint8


def test_video_file_frame_source_missing_file_raises() -> None:
    src = VideoFileFrameSource("data/demo/does_not_exist.mp4")
    with pytest.raises(RuntimeError):
        next(iter(src))


def test_video_file_frame_source_is_demo_frame_source() -> None:
    assert issubclass(VideoFileFrameSource, DemoFrameSource)


def test_video_file_frame_source_skips_to_target_fps(tmp_path) -> None:
    """高帧率源应按 fps_target 跳帧：产出帧数远少于总帧数（修复逐帧全读 3x 慢放）。"""
    mp4 = tmp_path / "hi.mp4"
    _make_synthetic_mp4(mp4, n=24, fps=24, w=320, h=240)
    src = VideoFileFrameSource(str(mp4), fps_target=8)
    # 24fps / 8fps → skip=3（src_fps 偏差不影响取整）
    assert src._skip >= 2
    frames = list(src)
    # 跳帧生效：产出远少于 24，且 frame_count 与产出一致
    assert 0 < len(frames) < 24
    assert src.frame_count == len(frames)
    # 限速下产出帧率 ≈ fps_target（8fps 目标，24 总帧 → 约 8 产出帧）
    assert len(frames) <= 12


# ============================================================================
# CaviarJpgFrameSource（CAVIAR 工程验证帧源）
# ============================================================================


def test_caviar_jpg_frame_source_yields_frames() -> None:
    if not _caviar_frames_present("one_leave_reenter"):
        pytest.skip("CAVIAR fixture 缺失：跑 tests/fixtures/download_fixtures.py 下载")
    src = CaviarJpgFrameSource(str(CAVIAR_BASE), scenario_source="one_leave_reenter", fps_target=2)
    # one_leave_reenter fixture 含 30 张 jpg
    assert src.frame_count == 30

    frames = list(src)
    assert len(frames) == 30
    _, frame = frames[0]
    assert frame.shape[2] == 3
    assert frame.dtype == np.uint8


def test_caviar_jpg_frame_source_is_demo_frame_source() -> None:
    assert issubclass(CaviarJpgFrameSource, DemoFrameSource)


def test_caviar_jpg_frame_source_loop_replay() -> None:
    """loop 重放：第二次迭代应与第一次产出相同帧（时间戳允许不同）。"""
    if not _caviar_frames_present("one_leave_reenter"):
        pytest.skip("CAVIAR fixture 缺失：跑 tests/fixtures/download_fixtures.py 下载")
    src = CaviarJpgFrameSource(str(CAVIAR_BASE), scenario_source="one_leave_reenter", fps_target=2)
    first = list(src)
    second = list(src)
    assert len(first) == len(second) == 30
    for i, ((_, f1), (_, f2)) in enumerate(zip(first, second)):
        assert np.array_equal(f1, f2), f"帧 {i} 内容不一致（重放应一致）"


def test_video_file_frame_source_loop_replay(tmp_path) -> None:
    """loop 重放：第二次迭代应与第一次产出相同帧（时间戳允许不同）。"""
    mp4 = tmp_path / "syn.mp4"
    _make_synthetic_mp4(mp4, n=10)
    src = VideoFileFrameSource(str(mp4), fps_target=8)
    first = list(src)
    second = list(src)
    assert len(first) == len(second) == 10
    for i, ((_, f1), (_, f2)) in enumerate(zip(first, second)):
        assert np.array_equal(f1, f2), f"帧 {i} 内容不一致（重放应一致）"


# ============================================================================
# build_frame_source 工厂分发（P0-11.3 核心替换点）
# ============================================================================


def test_build_frame_source_video_file(tmp_path) -> None:
    mp4 = tmp_path / "syn.mp4"
    _make_synthetic_mp4(mp4, n=5)

    scenario = ScenarioConfig(
        scenario_id="x",
        source="x",
        source_type="video_file",
        media_path=str(mp4),
        start_time=datetime(2026, 7, 19, 23, 30, tzinfo=UTC),
    )
    src = build_frame_source(scenario, hp_settings=None)
    assert isinstance(src, VideoFileFrameSource)
    assert src.path == str(mp4)


def test_build_frame_source_caviar() -> None:
    if not _caviar_frames_present("one_leave_reenter"):
        pytest.skip("CAVIAR fixture 缺失：跑 tests/fixtures/download_fixtures.py 下载")
    scenario = ScenarioConfig(
        scenario_id="night_visit",
        source="one_leave_reenter",
        source_type="caviar_jpg",
        start_time=datetime(2026, 7, 19, 23, 30, tzinfo=UTC),
    )

    class _Runtime:
        caviar_base_dir = str(CAVIAR_BASE)
        frame_glob = "frame_*.jpg"

    class _StubHP:
        runtime = _Runtime

    src = build_frame_source(scenario, _StubHP())
    assert isinstance(src, CaviarJpgFrameSource)
    assert src.frame_count == 30


def test_build_frame_source_video_file_missing_media_path_raises() -> None:
    scenario = ScenarioConfig(
        scenario_id="x",
        source="x",
        source_type="video_file",
        media_path=None,
        start_time=datetime(2026, 7, 19, 23, 30, tzinfo=UTC),
    )
    with pytest.raises(ValueError):
        build_frame_source(scenario, hp_settings=None)


# ============================================================================
# 场景配置加载
# ============================================================================


def test_load_real_doorway_scenario() -> None:
    """断言 real_doorway.yaml 正确加载为 video_file 源场景。"""
    scenario = load_scenario("config/demo/scenarios/real_doorway.yaml")
    assert scenario.scenario_id == "real_doorway"
    assert scenario.source_type == "video_file"
    assert scenario.media_path == "dataset/benign/media/real_doorway.mp4"
    assert scenario.source == "real_doorway"
    assert scenario.loop is True


def test_load_night_visit_default_source_type() -> None:
    """断言 night_visit.yaml 默认 source_type=caviar_jpg（向后兼容）。"""
    from silver_demo.scenarios import load_scenario as _load

    scenario = _load("config/demo/scenarios/night_visit.yaml")
    assert scenario.source == "one_leave_reenter"
    assert scenario.source_type == "caviar_jpg"
