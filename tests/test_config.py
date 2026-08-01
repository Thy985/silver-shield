"""配置加载契约测试。"""
from home_perception.core.config import ImgszProfile, Settings


def test_default_config_loads():
    s = Settings.load("config/default.yaml")
    assert s.ingestion.fps_target > 0
    # 第一阶段仅关注 4 类：person / backpack / handbag / cell phone
    assert s.detection.classes == [0, 24, 26, 67]
    assert s.detection.model == "yolo11n.pt"
    # P0-4 实测结论：CPU 边缘部署默认 480（balanced），满足 <100ms 且 >10FPS
    assert s.detection.imgsz == 480
    assert s.detection.imgsz_profile == ImgszProfile.BALANCED
    assert s.output.transport in {"mqtt", "http"}


def test_imgsz_profile_resolves():
    # 显式 imgsz 优先
    assert ImgszProfile.resolve(None, 320) == 320
    # 仅 profile：accurate=640 / balanced=480 / realtime=416
    assert ImgszProfile.resolve("accuracy", None) == 640
    assert ImgszProfile.resolve(ImgszProfile.BALANCED, None) == 480
    assert ImgszProfile.resolve("realtime", None) == 416
    # 无 profile 无 explicit：回退 balanced(480)
    assert ImgszProfile.resolve(None, None) == 480
    # 非法 profile 回退 balanced
    assert ImgszProfile.resolve("nope", None) == 480
    # 显式 imgsz 压过 profile
    assert ImgszProfile.resolve("accuracy", 416) == 416


def test_env_override(monkeypatch):
    monkeypatch.setenv("CENTER_MQTT_HOST", "broker.local")
    monkeypatch.setenv("CENTER_MQTT_PORT", "1884")
    s = Settings.load("config/default.yaml")
    assert s.output.mqtt.host == "broker.local"
    assert s.output.mqtt.port == 1884
