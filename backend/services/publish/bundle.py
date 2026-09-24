# -*- coding: utf-8 -*-
"""产物打包：content_hash 稳定寻址 + 发布前静态契约门禁(ENV-2/ENV-6)。

设计文档 §3.1：content_hash = sha256("\0".join(sorted(f"{rel}\0{sha256(bytes)}")))
门禁复用 harness.constraints.environment_contract（与验收共用一套代码，见设计 §5.2）。
"""
import hashlib
from typing import Dict

# 别名，便于调用方与测试统一类型
BundleFiles = Dict[str, bytes]


class BundleError(Exception):
    """产物不满足发布契约（ENV-2 外链 / ENV-6 悬空引用等）。"""


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def is_internal_path(rel: str) -> bool:
    """路径中含点开头的段（.task/、.git/ 等）→ 平台内部文件，不属于发布产物。

    两个调用点共用同一条策略，缺一不可：
    - 打包侧（PublishService.normalize_code_files）：新发布的产物不含内部文件
    - 服务侧（routes/published_site.py）：历史产物里已存在的也被挡住
    背景：requirement.code_files 混有 .task/TASK_STATE.md、.task/contract.json、
    .task/evaluator/result.json 等工作区文件；unlisted 是 noindex 而不是保密。
    """
    return any(seg.startswith(".") for seg in rel.split("/") if seg)


def compute_content_hash(files: BundleFiles) -> str:
    """稳定 content_hash：按 relpath 排序后拼接 "rel\0sha"，再整体 sha256。

    顺序无关：同一份产物无论 dict 插入顺序如何，hash 相同。
    """
    parts = [f"{rel}\0{_sha256(files[rel])}" for rel in sorted(files)]
    return _sha256("\0".join(parts).encode("utf-8"))


def _check_constraints(files: BundleFiles) -> None:
    """发布前静态契约门禁（与验收共用）。违反即抛 BundleError。"""
    # 延迟 import：避免 publish 模块在纯 hash/存储场景下被迫拉入约束检查器依赖
    from harness.constraints.environment_contract import (
        find_cdn_references,
        check_reference_closure,
    )
    html = files.get("index.html") or files.get("index.htm")
    if html is not None:
        html_text = html.decode("utf-8", "replace")
        external = find_cdn_references(html_text)
        if external:
            raise BundleError(f"ENV-2 禁止外部资源引用: {external}")
        missing = check_reference_closure(
            html_text,
            existing_files=list(files.keys()),
        )
        if missing:
            raise BundleError(f"ENV-6 悬空引用: {missing}")


def build_bundle(files: BundleFiles) -> "tuple[str, BundleFiles]":
    """打包产物。

    Returns:
        (content_hash, files) —— files 原样返回（内容寻址，调用方据此 put_bundle）。
    Raises:
        BundleError: 违反 ENV-2 / ENV-6。
    """
    _check_constraints(files)
    return compute_content_hash(files), files
