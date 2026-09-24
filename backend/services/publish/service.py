# -*- coding: utf-8 -*-
"""发布服务（plan B6）：归一化产物 → 契约门禁 → 内容寻址 → 幂等 upsert 站点。

设计 §5.2 / §4 幂等语义：
- 内容变 → 新 bundle + version+1 + slug 不变
- 内容不变 → 零写入（连 version 都不动）
- (user_id, requirement_id) 一对一；重复发布同一需求更新同一站点

Ship C 复验通过 on_published 回调解耦（默认 None），由路由层注入，
使服务可单元测试、且不阻塞发布主流程。
"""
from typing import Callable, Dict, List, Optional

from harness.observability.logger import get_logger
from models.models import PublishedBundle, PublishedSite
from utils.db import transactional_db

from .bundle import build_bundle, BundleError, is_internal_path
from .slug import new_slug
from .store import LocalFSStore

logger = get_logger(__name__)


class PublishError(Exception):
    """发布失败（无产物 / 超体积 / 契约违规 / slug 分配耗尽 / 存储失败）。"""


# 体积 / 文件数软上限（设计 §8 开放问题 2）
MAX_BUNDLE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_FILE_COUNT = 100


def _extract_relpath(name: str) -> str:
    return name.strip().lstrip("/").replace("\\", "/")


def _to_bytes(content) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode("utf-8")
    if content is None:
        return b""
    return str(content).encode("utf-8")


class PublishService:
    def __init__(
        self,
        store: Optional[LocalFSStore] = None,
        db_session_factory: Optional[Callable] = None,
    ):
        self.store = store or LocalFSStore()
        # 默认用全局事务 session；测试可注入独立 engine 的 factory
        self._db_factory = db_session_factory or transactional_db

    # ---- 归一化：Requirement.code_files(JSON) → {relpath: bytes} ----
    def normalize_code_files(self, code_files: List[Dict]) -> Dict[str, bytes]:
        files: Dict[str, bytes] = {}
        skipped: List[str] = []
        for item in code_files or []:
            if not isinstance(item, dict):
                continue
            name = item.get("filename") or item.get("name")
            if not name:
                continue
            rel = _extract_relpath(name)
            if not rel:
                continue
            if is_internal_path(rel):
                skipped.append(rel)
                continue
            files[rel] = _to_bytes(item.get("content"))
        if skipped:
            logger.info(
                "发布产物剔除 %d 个平台内部文件（不对外发布）: %s",
                len(skipped),
                ", ".join(sorted(skipped)[:5]),
            )
        return files

    # ---- 发布入口 ----
    def publish(
        self,
        *,
        user_id: int,
        code_files: List[Dict],
        requirement_id: Optional[int] = None,
        title: str = "",
        visibility: str = "unlisted",
        runtime_tier: int = 0,
        on_published: Optional[Callable] = None,
    ) -> PublishedSite:
        files = self.normalize_code_files(code_files)
        if not files:
            raise PublishError("没有可发布的代码文件")
        total_bytes = sum(len(v) for v in files.values())
        if len(files) > MAX_FILE_COUNT:
            raise PublishError(f"文件数超过上限 {MAX_FILE_COUNT}")
        if total_bytes > MAX_BUNDLE_BYTES:
            raise PublishError(f"产物体积超过上限 {MAX_BUNDLE_BYTES} 字节")

        try:
            content_hash, files = build_bundle(files)
        except BundleError as e:
            raise PublishError(f"产物不满足发布契约: {e}") from e

        # 内容寻址 + 磁盘幂等（目录已存在则零写入）
        store_key = self.store.put_bundle(content_hash, files)

        published_slug = None
        with self._db_factory() as db:
            site = self._find_or_create(db, user_id, requirement_id)
            if site.current_hash == content_hash:
                # 幂等：内容未变，连 version 都不动，零写入
                return site
            # 首次发布（此前未指向任何产物）→ version 取列默认 1；已发布站点内容变更才 +1
            is_first_publish = site.current_hash is None
            # bundle 行幂等：PK 已存在则跳过（内容寻址天然去重）
            if db.get(PublishedBundle, content_hash) is None:
                db.add(PublishedBundle(
                    content_hash=content_hash,
                    store_key=store_key,
                    size_bytes=total_bytes,
                    file_count=len(files),
                    entry="index.html",
                ))
            site.current_hash = content_hash
            if not is_first_publish:
                site.version = (site.version or 0) + 1
            if title:
                site.title = title
            site.visibility = visibility or site.visibility
            site.runtime_tier = runtime_tier
            site.verify_status = "pending"
            site.verified_at = None
            db.flush()
            published_slug = site.slug

        # 退出事务 session 提交后，解耦触发 Ship C 复验（不阻断发布）
        if published_slug is not None and on_published is not None:
            try:
                on_published(published_slug, content_hash)
            except Exception:
                # 复验失败由复验逻辑自身把站点标 degraded，不在此处阻断发布
                pass

        # 返回最新持久化对象（重新取回，避免返回 detached 实例）
        with self._db_factory() as db:
            return db.query(PublishedSite).filter_by(slug=published_slug).first()

    # ---- 内部：按 (user_id, requirement_id) 找/建站点 ----
    def _find_or_create(self, db, user_id, requirement_id):
        site = None
        if requirement_id is not None:
            site = db.query(PublishedSite).filter_by(
                user_id=user_id, requirement_id=requirement_id
            ).first()
        if site is None:
            slug = self._alloc_slug(db)
            site = PublishedSite(
                slug=slug, user_id=user_id, requirement_id=requirement_id
            )
            db.add(site)
            db.flush()
        return site

    def _alloc_slug(self, db) -> str:
        for _ in range(10):
            slug = new_slug()
            if db.query(PublishedSite).filter_by(slug=slug).first() is None:
                return slug
        raise PublishError("slug 分配耗尽（碰撞重试 10 次仍冲突）")
