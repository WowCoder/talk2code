# -*- coding: utf-8 -*-
"""静态结构审计 —— 交付前的确定性契约校验（零 LLM 成本）

## 为什么需要

本项目刻意不用构建工具与 ES Module（file:// 直开 + IIFE + 全局命名空间）。
代价是：**跨文件契约失去了 import/export 提供的编译期检查**，JS 文件之间靠
全局约定在"运行时链接"，一旦断裂只能在浏览器里崩。

历史数据（28 条需求评测）显示，这类结构性缺陷是致命且反复出现的：
- 跨文件导出/API 断裂 4 次（req122 `Game is not defined`、
  req125 `Utils.on/$$ 未导出`、req131 `Game 未暴露 getState`、
  req136 `Timer 未挂 init/on`）
- 文件缺失/不完整 5 次
- 交互元素未接线（点击无响应）多次

这些全部可以用毫秒级静态分析检出，而不必等到"完整生成 + 浏览器自动化验收
+ LLM 评测"（数分钟 + 真实 token）之后。

## 设计原则

与 hooks.py 一致：**成功静默，失败喧哗**。
校验通过返回 None；检出问题返回可读的诊断文本，由 HookManager 塞回 Agent Loop，
既做交付门禁，也给 defect_repair 提供精确定位（例如
"timer.js 已定义 function init() 但未挂载到 Timer 上，被 app.js 调用"）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from harness.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Finding:
    """一条静态审计发现"""
    severity: str      # critical / major
    category: str      # cross_file_export / interaction_wiring / missing_file
    file: str
    issue: str
    suggestion: str

    def format(self) -> str:
        return f"[{self.severity.upper()}][{self.category}] {self.file}: {self.issue}\n    → 修复: {self.suggestion}"


# 调用形如 X.then()/X.push() 等内置/常见属性名，不作为缺失方法上报
_IGNORED_METHODS = {
    'then', 'catch', 'finally', 'call', 'apply', 'bind',
    'hasOwnProperty', 'toString', 'valueOf', 'constructor',
    'push', 'pop', 'shift', 'unshift', 'slice', 'map',
    'forEach', 'filter', 'includes', 'indexOf', 'length',
}

# 导出语句：window.Utils = Utils; / global.Timer = Timer; / globalThis.X = Y;
_EXPORT_RE = re.compile(
    r'\b(?:window|global|globalThis)\s*\.\s*(\w+)\s*=\s*(\w+)\s*;'
)
# 直接挂在全局对象上：window.Timer.init = function ...
_ATTACH_ON_GLOBAL_RE = re.compile(
    r'\b(?:window|global|globalThis)\s*\.\s*(\w+)\s*\.\s*(\w+)\s*=(?!=)'
)


# ==================== 工具函数 ====================

def _brace_body(src: str, open_idx: int) -> Optional[str]:
    """从 open_idx（指向 '{'）开始做大括号配对，返回花括号内的内容"""
    depth = 0
    for j in range(open_idx, len(src)):
        if src[j] == '{':
            depth += 1
        elif src[j] == '}':
            depth -= 1
            if depth == 0:
                return src[open_idx + 1:j]
    return None


def _object_literal_top_keys(body: str) -> Set[str]:
    """提取对象字面量的顶层 key

    注意：body 由 _brace_body 返回，**不含最外层花括号**，
    因此顶层 key 的嵌套深度为 0（而非 1）。

    例：`var Utils = { $: function(){}, formatTime: function(){} }` → {$, formatTime}
    """
    keys: Set[str] = set()
    depth = 0
    i, n = 0, len(body)
    while i < n:
        ch = body[i]
        if ch in '{[(':
            depth += 1
        elif ch in '}])':
            depth -= 1
        elif depth == 0 and (ch.isalpha() or ch in '_$'):
            m = re.match(r'[A-Za-z_$][\w$]*', body[i:])
            if not m:
                i += 1
                continue
            name = m.group(0)
            j = i + len(name)
            k = j
            while k < n and body[k] in ' \t\r\n':
                k += 1
            if k < n and body[k] == ':':
                keys.add(name)
            i = j
            continue
        i += 1
    return keys


def _find_object_literal_methods(src: str, alias: str) -> Set[str]:
    """`var alias = { ... }` 形式定义时，提取其顶层方法名"""
    m = re.search(r'\b(?:var|let|const)\s+' + re.escape(alias) + r'\s*=\s*\{', src)
    if not m:
        return set()
    body = _brace_body(src, m.end() - 1)
    if not body:
        return set()
    return _object_literal_top_keys(body)


# ==================== 检查 1：跨文件导出一致性 ====================

def check_export_consistency(js_files: Dict[str, str]) -> List[Finding]:
    """校验：被其他文件调用的全局对象方法，是否真的挂在导出对象上

    这是本项目最高频的致命缺陷。典型形态（req136）：
        var Timer = {};
        function init() { ... }        ← 私有函数，没挂上去
        global.Timer = Timer;          ← 导出空壳
        // app.js: window.Timer.init() → TypeError 崩溃
    """
    findings: List[Finding] = []

    # 1) 收集导出：全局名 -> (定义文件, 局部别名)
    exports: Dict[str, Tuple[str, str]] = {}
    for path, src in js_files.items():
        for gname, alias in _EXPORT_RE.findall(src):
            exports.setdefault(gname, (path, alias))
    if not exports:
        return findings

    for gname, (def_path, alias) in exports.items():
        # 2) 收集该全局对象上已挂载的方法（跨文件，写入即算）
        attached: Set[str] = set()
        for path, src in js_files.items():
            attached |= {m for g, m in _ATTACH_ON_GLOBAL_RE.findall(src) if g == gname}
            # 局部别名的挂载只在"定义该导出的文件"内可靠
            if path == def_path:
                attached |= set(re.findall(
                    r'\b' + re.escape(alias) + r'\.(\w+)\s*=(?!=)', src))
                attached |= set(re.findall(
                    r'\b' + re.escape(alias) + r'\.prototype\.(\w+)\s*=(?!=)', src))
                attached |= _find_object_literal_methods(src, alias)

        # 3) 收集调用点：Name.method(  —— 要求紧跟 '('，
        #    因此 `Utils.safeStorage.set(...)` 这类嵌套属性访问天然被排除
        call_re = re.compile(
            r'\b(?:window\s*\.\s*)?' + re.escape(gname) + r'\s*\.\s*(\w+)\s*\(')
        calls: Dict[str, List[str]] = {}
        for path, src in js_files.items():
            for m in call_re.findall(src):
                calls.setdefault(m, []).append(path)

        # 4) 差集 = 调用了但没挂载
        for method, callers in sorted(calls.items()):
            if method in attached or method in _IGNORED_METHODS:
                continue
            call_sites = sorted(set(callers))
            def_src = js_files.get(def_path, '')

            # 精确诊断：方法在定义文件里存在（只是没挂上去）vs 压根没实现
            if re.search(r'\bfunction\s+' + re.escape(method) + r'\s*\(', def_src):
                issue = (
                    f"导出对象 {gname} 未挂载方法 {method}()："
                    f"{def_path} 中已定义 function {method}()，但它是 IIFE 内的私有函数"
                )
                suggestion = (
                    f"在 {def_path} 的 `global.{gname} = {alias};` 之前补一行 "
                    f"`{alias}.{method} = {method};`"
                    f"（或把 {method} 作为 key 加进 {alias} 对象字面量）"
                )
            else:
                issue = f"导出对象 {gname} 上不存在方法 {method}()"
                suggestion = f"在 {def_path} 中实现并挂载 {gname}.{method}"

            findings.append(Finding(
                severity='critical',
                category='cross_file_export',
                file=def_path,
                issue=f"{issue}；被 {', '.join(call_sites)} 调用",
                suggestion=suggestion,
            ))

    return findings


# ==================== 检查 2：交互元素接线 ====================

_INTERACTIVE_TAG_RE = re.compile(
    r'<(button|input|select|textarea|a)\b([^>]*)>', re.IGNORECASE)
_ID_ATTR_RE = re.compile(r'\bid\s*=\s*["\']([^"\']+)["\']')
_DATA_ACTION_RE = re.compile(r'\bdata-action\s*=\s*["\']([^"\']+)["\']')
_INLINE_HANDLER_RE = re.compile(r'\bon(?:click|change|input|submit|keydown)\s*=')

# JS 中可能的引用形态：'#id' / "id" / getElementById('id') / [data-action="x"]
def _js_references(token: str, js_all: str) -> bool:
    if not token:
        return True
    patterns = (
        r'["\']#' + re.escape(token) + r'["\']',                 # "#startBtn"
        r'["\']' + re.escape(token) + r'["\']',                  # "startBtn"
        r'getElementById\(\s*["\']' + re.escape(token) + r'["\']',
        r'\b' + re.escape(token) + r'\b',                        # 裸标识符兜底
    )
    return any(re.search(p, js_all) for p in patterns)


def check_interaction_wiring(html_files: Dict[str, str],
                             js_files: Dict[str, str]) -> List[Finding]:
    """校验：HTML 中的交互元素是否在 JS 中被引用（未接线 → 点击无响应）

    历史失败里多次出现 `[no_interaction] 点击主交互入口后页面无任何变化`，
    其静态特征就是：按钮有 id，但没有任何 JS 引用它。
    """
    if not js_files:
        return []

    js_all = '\n'.join(js_files.values())
    findings: List[Finding] = []

    for path, html in html_files.items():
        for tag_name, attrs in _INTERACTIVE_TAG_RE.findall(html):
            # 内联 onclick/onchange 视为已接线
            if _INLINE_HANDLER_RE.search(attrs):
                continue

            tokens: List[Tuple[str, str]] = []  # (类型, 值)
            m = _ID_ATTR_RE.search(attrs)
            if m:
                tokens.append(('id', m.group(1)))
            m = _DATA_ACTION_RE.search(attrs)
            if m:
                tokens.append(('data-action', m.group(1)))
            if not tokens:
                continue  # 无标识的交互元素无法静态判定，跳过（避免误报）

            for kind, token in tokens:
                if _js_references(token, js_all):
                    continue
                findings.append(Finding(
                    severity='major',
                    category='interaction_wiring',
                    file=path,
                    issue=(
                        f"<{tag_name}> 的 {kind}=\"{token}\" 未在任何 JS 中被引用，"
                        f"点击后很可能无响应"
                    ),
                    suggestion=(
                        f"在 JS 中为该元素绑定事件，例如 "
                        f"document.getElementById('{token}').addEventListener('click', ...)；"
                        f"若它是纯装饰元素，请移除 {kind} 或改用非交互标签"
                    ),
                ))
                break  # 同一元素只报一次

    return findings


# ==================== 检查 3：契约文件完整性 ====================

def check_declared_files(declared: Sequence[str],
                         existing: Sequence[str]) -> List[Finding]:
    """校验：plan（task_list / spec）声明的文件是否真的生成了

    历史上有 5 次失败是"缺失 N 个关键文件，无法完成验证"，且分数直接归 0。
    """
    if not declared:
        return []
    existing_set = set(existing)
    findings: List[Finding] = []
    for f in declared:
        if f in existing_set:
            continue
        findings.append(Finding(
            severity='critical',
            category='missing_file',
            file=f,
            issue=f"计划中声明要生成 {f}，但工作区中不存在",
            suggestion=f"补齐 {f}；若不再需要，请同步更新任务清单",
        ))
    return findings


# ==================== 主入口 ====================

def audit_files(files: Dict[str, str],
                declared_files: Optional[Sequence[str]] = None) -> List[Finding]:
    """对一组文件做静态结构审计

    Args:
        files: {相对路径: 内容}
        declared_files: 计划声明应生成的文件（可选）

    Returns:
        Finding 列表（空列表 = 全部通过）
    """
    html_files = {p: c for p, c in files.items() if p.endswith('.html')}
    js_files = {p: c for p, c in files.items() if p.endswith('.js')}

    findings: List[Finding] = []
    try:
        findings += check_export_consistency(js_files)
        findings += check_interaction_wiring(html_files, js_files)
        findings += check_declared_files(declared_files or [], list(files.keys()))
    except Exception as e:  # 审计本身绝不能拖垮主流程
        logger.warning(f"[StaticAudit] 审计异常，已跳过: {e}")
        return []
    return findings


def format_findings(findings: Sequence[Finding]) -> str:
    """把 Findings 格式化为可塞回 Agent Loop 的诊断文本"""
    if not findings:
        return ""
    criticals = [f for f in findings if f.severity == 'critical']
    majors = [f for f in findings if f.severity == 'major']
    lines = [f"[静态结构审计] 发现 {len(criticals)} 个致命、{len(majors)} 个较重问题："]
    lines += [f.format() for f in criticals + majors]
    lines.append(
        "请优先修复上述致命问题后再次声明完成。"
        "跨文件导出问题必须把方法真正挂载到导出对象上（IIFE 内的私有函数外部拿不到）。"
    )
    return "\n".join(lines)


def static_audit_hook(ctx) -> Optional[str]:
    """Hook：ON_TASK_COMPLETE 时对工作区做静态结构审计

    成功静默（返回 None），失败喧哗（返回诊断文本）。
    """
    state = getattr(ctx, 'state', None) or {}
    workspace = state.get('_workspace')
    if workspace is None:
        return None

    file_list = state.get('file_list') or []
    try:
        existing = list(file_list or workspace.list())
    except Exception as e:
        logger.warning(f"[StaticAudit] 无法列取工作区文件: {e}")
        return None

    files: Dict[str, str] = {}
    for f in existing:
        if not (f.endswith('.html') or f.endswith('.js') or f.endswith('.css')):
            continue
        try:
            content = workspace.read(f)
        except Exception:
            continue
        if content:
            files[f] = content

    if not files:
        return None

    declared = state.get('declared_files') or []
    findings = audit_files(files, declared)
    if not findings:
        return None

    logger.info(
        f"[StaticAudit] 需求 {getattr(ctx, 'requirement_id', '?')} 检出 "
        f"{len(findings)} 个结构问题"
    )
    return format_findings(findings)
