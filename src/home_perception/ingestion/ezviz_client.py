"""萤石开放平台客户端：获取 access token 与直播流地址。

已落地（沿用 prototypes/ 的验证逻辑）：支持 RTSP（低延迟，默认）与 HLS（回退）。
凭证通过环境变量 EZVIZ_APP_KEY / EZVIZ_APP_SECRET 注入，禁止硬编码。
"""
from __future__ import annotations

import os

import requests

from ..common.logging import get_logger

log = get_logger(__name__)

# 架构终稿：RTSP 优先（低延迟），HLS 为已验证回退
PROTOCOL = {"rtsp": 1, "hls": 2}


class EZVIZClient:
    TOKEN_URL = "https://open.ys7.com/api/lapp/token/get"
    ADDR_URL = "https://open.ys7.com/api/lapp/v2/live/address/get"

    def __init__(self, app_key: str | None = None, app_secret: str | None = None):
        self.app_key = app_key or os.environ.get("EZVIZ_APP_KEY")
        self.app_secret = app_secret or os.environ.get("EZVIZ_APP_SECRET")
        if not self.app_key or not self.app_secret:
            raise RuntimeError("缺少 EZVIZ_APP_KEY / EZVIZ_APP_SECRET，请在 .env 中配置")
        self._token: str | None = None

    def get_token(self) -> str:
        resp = requests.post(
            self.TOKEN_URL,
            data={"appKey": self.app_key, "appSecret": self.app_secret},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != "200":
            raise RuntimeError(f"获取 token 失败: {data.get('msg')}")
        self._token = data["data"]["accessToken"]
        log.info("ezviz.token_ok")
        return self._token

    def get_stream_url(
        self,
        serial: str,
        protocol: str = "rtsp",
        quality: int = 1,
        channel_no: int = 1,
    ) -> str:
        if self._token is None:
            self.get_token()
        p = PROTOCOL.get(protocol, 1)
        resp = requests.post(
            self.ADDR_URL,
            data={
                "accessToken": self._token,
                "deviceSerial": serial,
                "channelNo": channel_no,
                "protocol": p,
                "quality": quality,
            },
            timeout=10,
        )
        data = resp.json()
        if data.get("code") != "200":
            raise RuntimeError(f"获取流地址失败: {data.get('msg')}")
        log.info("ezviz.stream_url_ok", serial=serial, protocol=protocol)
        return data["data"]["url"]
