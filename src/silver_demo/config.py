"""Demo 网关配置（ADR-0015 §3）。

``DemoSettings`` 只管"怎么起网关服务"（bind host/port、scenario 路径、循环开关），
**不**触碰风险判定逻辑。风险阈值仍在 ``home_perception.core.config.Settings`` 内
（ADR-0015：Demo 是消费者，不是架构参与者）。

构造链：
    DemoSettings（本文件）
        └─ 经 gateway 装配 → PerceptionPipeline.from_settings(settings, ...)
                                   ↑
    home_perception.core.config.Settings（冻结包，读 config/default.yaml）
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class DemoSettings(BaseModel):
    """Demo 网关运行参数。

    字段：
    - ``host`` / ``port``：FastAPI/uvicorn 绑定地址（默认本地回环，演示足够）
    - ``scenario_path``：night_visit.yaml 路径（默认 config/demo/scenarios/night_visit.yaml）
    - ``home_perception_config``：冻结包 Settings 的 YAML 路径（默认 config/default.yaml）
    - ``dashboard_dir``：StaticFiles 托管目录（默认 silver_demo/dashboard）
    - ``ws_path``：WebSocket 端点路径（默认 /ws）
    - ``frame_loop_interval_s``：帧循环最小间隔（0 = 不限速；>0 模拟实时观感）
    - ``jpeg_quality``：base64 JPEG 编码质量（1-100；Demo 50 足够，降带宽）
    """

    host: str = "127.0.0.1"
    port: int = 8765
    scenario_path: str = "config/demo/scenarios/night_visit.yaml"
    home_perception_config: str = "config/default.yaml"
    dashboard_dir: str = str(Path(__file__).resolve().parent / "dashboard")
    ws_path: str = "/ws"
    frame_loop_interval_s: float = 0.0
    jpeg_quality: int = 50

    @classmethod
    def from_env(cls) -> "DemoSettings":
        """从环境变量构造（DEMO_HOST / DEMO_PORT / DEMO_SCENARIO 可覆盖）。

        保持与 AGENTS.md §1.3 一致：凭证/配置走环境变量，不硬编码。
        """
        import os

        kwargs: dict = {}
        if v := os.environ.get("DEMO_HOST"):
            kwargs["host"] = v
        if v := os.environ.get("DEMO_PORT"):
            try:
                kwargs["port"] = int(v)
            except ValueError:
                raise ValueError(f"DEMO_PORT 必须是整数，收到 {v!r}") from None
        if v := os.environ.get("DEMO_SCENARIO"):
            kwargs["scenario_path"] = v
        if v := os.environ.get("DEMO_HP_CONFIG"):
            kwargs["home_perception_config"] = v
        return cls(**kwargs)
