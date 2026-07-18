"""配置加载契约测试。"""
from home_perception.core.config import Settings


def test_default_config_loads():
    s = Settings.load("config/default.yaml")
    assert s.ingestion.fps_target > 0
    # 第一阶段仅关注 4 类：person / backpack / handbag / cell phone
    assert s.detection.classes == [0, 24, 26, 67]
    assert s.detection.model == "yolo11n.pt"
    assert s.detection.imgsz == 640
    assert s.output.transport in {"mqtt", "http"}


def test_env_override(monkeypatch):
    monkeypatch.setenv("CENTER_MQTT_HOST", "broker.local")
    monkeypatch.setenv("CENTER_MQTT_PORT", "1884")
    s = Settings.load("config/default.yaml")
    assert s.output.mqtt.host == "broker.local"
    assert s.output.mqtt.port == 1884
