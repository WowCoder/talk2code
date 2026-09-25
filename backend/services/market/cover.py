# -*- coding: utf-8 -*-
"""市集封面（作者自定义缩略图）。

默认封面是**首页截图**（服务时按需生成，见 thumbs.py）；作者可以上传一张图覆盖它，
也可以删掉回到自动截图。

安全：
- 文件名不用作者传来的原文件名，一律用 slug —— 杜绝路径穿越与覆盖他人文件；
- 只认 PNG / JPEG 的**魔数**，不信 Content-Type（客户端可伪造）；
- 体积上限 2 MB，超限直接拒。
  不重新编码图片（避免引入 Pillow 依赖）：这里的目标是「作者自己挑的图」，
  重新编码既不必要也会改变作者的原图。
"""
from pathlib import Path

from config import settings
from harness.observability.logger import get_logger

logger = get_logger(__name__)

MAX_COVER_BYTES = 2 * 1024 * 1024

_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)


def cover_dir() -> Path:
    d = Path(settings.PUBLISH_STORE_PATH) / "_covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _detect(data: bytes):
    for magic, ext, mime in _MAGIC:
        if data.startswith(magic):
            return ext, mime
    return None, None


def validate_cover(data: bytes) -> str | None:
    """返回错误文案；通过则返回 None。"""
    if not data:
        return "请选择一张图片"
    if len(data) > MAX_COVER_BYTES:
        return f"封面不能超过 {MAX_COVER_BYTES // 1024 // 1024} MB"
    ext, _ = _detect(data)
    if not ext:
        return "只支持 PNG / JPEG 图片"
    return None


def save_cover(slug: str, data: bytes) -> str | None:
    """保存封面（覆盖同名）。返回 mime；失败返回 None。"""
    ext, mime = _detect(data)
    if not ext:
        return None
    # 先清掉另一种扩展名的旧文件，避免 png/jpg 各留一份导致换图后取的还是旧图
    for other in ("png", "jpg"):
        p = cover_dir() / f"{slug}.{other}"
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass
    (cover_dir() / f"{slug}.{ext}").write_bytes(data)
    return mime


def get_cover(slug: str):
    """返回 (path, mime)；无封面返回 (None, None)。"""
    for ext, mime in (("png", "image/png"), ("jpg", "image/jpeg")):
        p = cover_dir() / f"{slug}.{ext}"
        if p.exists() and p.stat().st_size > 0:
            return p, mime
    return None, None


def delete_cover(slug: str) -> bool:
    removed = False
    for ext in ("png", "jpg"):
        p = cover_dir() / f"{slug}.{ext}"
        if p.exists():
            try:
                p.unlink()
                removed = True
            except OSError:
                pass
    return removed
