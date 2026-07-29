"""运行期装配辅助（P0-10 · 装配联调）。

把 `core.config.Settings` 翻译成各组件的构造参数：
- `RuleConfig` → `ThresholdConfig`（规则阈值）
- `ActionConfig` → `DispatcherConfig` + `FamilyContact`（行动层路由）
- CAVIAR demo 帧读取（`read_caviar_frames`）

边界：本文件**只做构造参数转换 + 帧读取**，不实例化具体组件
（组件装配在 `pipeline.PerceptionPipeline.from_settings`）。
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from ..action.dispatcher import DispatcherConfig
from ..action.notifier import FamilyContact
from ..core.config import ActionConfig, RuleConfig
from ..analysis.rule_engine import ThresholdConfig


def build_threshold_config(rule_cfg: RuleConfig) -> ThresholdConfig:
    """RuleConfig → RuleEngine 内部 ThresholdConfig。"""
    return rule_cfg.to_threshold_config()


def _build_family_contact(action_cfg: ActionConfig) -> Optional[FamilyContact]:
    """ActionConfig.family_contact → FamilyContact（未配置返回 None）。

    模块私有：仅 build_dispatcher_config 内部使用，不进 runtime 公共 API 表面积。
    """
    fc = action_cfg.family_contact
    if fc is None:
        return None
    return FamilyContact(
        elder_id=fc.elder_id,
        name=fc.name,
        phone=fc.phone,
        relation=fc.relation,
    )


def build_dispatcher_config(action_cfg: ActionConfig) -> DispatcherConfig:
    """ActionConfig → ActionDispatcher 的 DispatcherConfig。"""
    return DispatcherConfig(
        family_contact=_build_family_contact(action_cfg),
        community_endpoint=action_cfg.community_endpoint,
        mqtt_topic_prefix=action_cfg.mqtt_topic_prefix,
    )


def read_caviar_frames(
    base_dir: str,
    scenario: str,
    frame_glob: str = "frame_*.jpg",
) -> List["object"]:
    """读取 CAVIAR 场景目录下的抽帧 JPG，返回 BGR 帧列表（按文件名排序）。

    依赖 cv2；任一环节缺失（cv2 未装 / 目录不存在 / 无 jpg）返回空列表，
    由调用方决定 skip（不抛异常，符合 AGENTS.md §2.5 不静默吞但需可恢复）。
    """
    try:
        import cv2
    except ImportError:  # pragma: no cover - 依赖缺失提示
        return []

    scenario_dir = Path(base_dir) / scenario
    if not scenario_dir.is_dir():
        return []
    files = sorted(scenario_dir.glob(frame_glob))
    if not files:
        return []

    frames: List["object"] = []
    for f in files:
        img = cv2.imread(str(f))
        if img is not None:
            frames.append(img)
    return frames
