# -*- coding: utf-8 -*-
"""预览能力 URL 的令牌签名

预览走 iframe 时，浏览器发起的子资源请求（css/js/图片）无法携带
Authorization 头，cookie 又高度依赖部署环境（localhost↔127.0.0.1 域不互通、
代理配置差异），导致"验证全过、用户看到无样式死页面"。

方案：HMAC 确定性能力令牌。/api/pt/<req_id>/<token>/<path> 无需任何凭证，
token = HMAC(JWT_SECRET, "preview:{user_id}:{req_id}:{day}")，不可猜测且绑定归属，
所有相对路径子资源自动落在同一令牌路径下，彻底摆脱 cookie/origin 依赖。

有效期：令牌按自然日滚动（day = UTC 日期序号），跨日自动失效，
避免泄漏后长期可用。
"""
import hashlib
import hmac
import time

from config import settings


def _token_day() -> int:
    """当前 UTC 自然日序号（令牌有效期粒度）"""
    return int(time.time() // 86400)


def make_preview_token(user_id: int, req_id: int, day: int = None) -> str:
    if day is None:
        day = _token_day()
    msg = f"preview:{int(user_id)}:{int(req_id)}:{int(day)}".encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def verify_preview_token(token: str, user_id: int, req_id: int) -> bool:
    # 当前日与昨日都接受（跨日边缘的短窗口容错）
    for day in (_token_day(), _token_day() - 1):
        if hmac.compare_digest(token, make_preview_token(user_id, req_id, day)):
            return True
    return False


def make_preview_url(user_id: int, req_id: int, filepath: str = "index.html") -> str:
    """构造预览能力 URL。

    默认返回同源相对路径（Agent 内嵌 iframe / 前端页面均同源使用），
    彻底规避把 127.0.0.1 硬编码进 URL 导致非本机部署不可达的问题。
    若配置了 PREVIEW_PUBLIC_BASE_URL（如 https://preview.example.com），
    则生成该域下的绝对 URL。
    """
    base = (settings.PREVIEW_PUBLIC_BASE_URL or "").rstrip("/")
    path = f"/api/pt/{int(req_id)}/{make_preview_token(user_id, req_id)}/{filepath}"
    return f"{base}{path}"
