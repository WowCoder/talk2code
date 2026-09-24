# -*- coding: utf-8 -*-
"""发布地址的唯一真值来源（协议 + 主机 + 端口）。

为什么单独成模块
----------------
同一个「发布链接」被三处消费：发布路由（写进响应体的 url 字段）、复验服务
（拿它对线上 URL 重跑验收）、前端（只展示、不拼接）。此前每处各自拼
``https://{slug}.{apex}``，于是分叉出两个必然故障：

1. 本地开发（``PUBLISH_APEX=localhost``、后端在 ``:5001``）拼出的是
   ``https://x.localhost``——无端口、错协议，点开与复验都必然失败；
2. 协议/端口只要有一处改动，前端展示的链接与复验用的链接就不是同一个，
   会出现「页面能打开但复验判 degraded」这种无法解释的组合。

故把拼接收敛到这里：其余位置一律调用，不得自行拼接字符串。
"""
from config import settings

from .slug import is_valid_slug


def published_host(slug: str) -> str | None:
    """站点对外主机名。未配置 apex（Host 路由整体关闭）或 slug 非法 → None。"""
    apex = (settings.PUBLISH_APEX or '').strip()
    if not apex or not is_valid_slug(slug):
        return None
    return f"{slug}.{apex}"


def published_url(slug: str) -> str | None:
    """站点对外访问 URL。

    生产：``https`` 且不带端口（nginx 终结 TLS）。
    本地开发：``http`` + 后端端口，配合 ``PUBLISH_APEX=localhost``——``*.localhost``
    由系统解析器直接指向 127.0.0.1，不需要任何外部通配 DNS。
    """
    host = published_host(slug)
    if not host:
        return None
    scheme = (settings.PUBLISH_URL_SCHEME or 'https').strip()
    port = (settings.PUBLISH_URL_PORT or '').strip()
    return f"{scheme}://{host}{':' + port if port else ''}"
