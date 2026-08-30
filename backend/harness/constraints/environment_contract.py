# -*- coding: utf-8 -*-
"""
EnvironmentContract —— 运行环境契约单一事实源

生成物运行在沙箱 iframe / file:// 环境（无网络 CDN、ES module 被 CORS 拦截、
localStorage 被禁用、必须单入口可双击打开）。这些硬约束此前散落在
tl_analysis.md / coder_*.md / skills / smoke 测试四处且互相矛盾，
本模块是它们唯一的定义点：

- 规则文本：`render_environment_contract()` 渲染给 prompt（TL/coder/技能共用）
- 程序化检查：`find_module_violations()` / `find_cdn_references()`
  / `extract_local_refs()` 供 PRE_WRITE Hook、lint_js、plan 校验器复用

修改环境规则时只改这里，所有下游自动同步。
"""

import re
from pathlib import Path
from typing import Iterable

from harness.observability.logger import get_logger

logger = get_logger(__name__)


# ==================== 契约规则（唯一权威定义） ====================

ENVIRONMENT_RULES: list[dict] = [
    {
        "id": "ENV-1",
        "title": "单入口离线可打开",
        "detail": (
            "交付物必须是一个可直接双击打开的静态站点：index.html 是唯一入口，"
            "所有资源用相对路径引用，无网络环境下功能完整。"
        ),
    },
    {
        "id": "ENV-2",
        "title": "禁止外部 CDN / 网络资源",
        "detail": (
            "禁止引入任何 http(s):// 外部资源（Tailwind CDN、Google Fonts、图标 CDN 等）。"
            "样式一律写本地 css 文件或 <style> 内联，第三方能力用原生实现等价替代。"
        ),
    },
    {
        "id": "ENV-3",
        "title": "JS 加载方式限定 classic script",
        "detail": (
            "禁止 ES Module：<script type=\"module\">、import、export 全部禁用"
            "（file:// 协议下 ES Module 被 CORS 拦截，全部脚本不执行）。"
            "每个 JS 文件用 IIFE 包裹 (function (global) { ... })(window)，"
            "通过 window.XXX 或全局变量暴露接口；HTML 里按依赖顺序用普通 "
            "<script src=\"js/xxx.js\"></script> 引入，或直接内联到 index.html。"
        ),
    },
    {
        "id": "ENV-4",
        "title": "存储访问必须兜底",
        "detail": (
            "预览沙箱可能禁用 localStorage：任何 localStorage/sessionStorage 访问"
            "必须包 try/catch 并降级为内存对象，不允许未捕获的 SecurityError。"
        ),
    },
    {
        "id": "ENV-5",
        "title": "交互入口初始可见",
        "detail": (
            "开始按钮/主操作入口在页面加载后必须立即可见可点击：遮罩层/覆盖层"
            "不得默认带 hidden 类且初始化时不移除。"
        ),
    },
    {
        "id": "ENV-6",
        "title": "引用闭合",
        "detail": (
            "index.html 引用的每一个本地 js/css 文件都必须真实存在（写入集内或工作区已有），"
            "不允许出现 404/ERR_FILE_NOT_FOUND 的悬空引用。"
        ),
    },
]


def render_environment_contract() -> str:
    """渲染契约 Markdown 片段，注入 TL / coder / 技能 prompt。

    Returns:
        形如 "## 运行环境硬约束（违反即验收失败）\\n- **ENV-1 单入口...** ..." 的文本。
    """
    lines = ["## 运行环境硬约束（由验证沙箱确定性检查，违反即返工）"]
    for rule in ENVIRONMENT_RULES:
        lines.append(f"- **{rule['id']} {rule['title']}**：{rule['detail']}")
    return "\n".join(lines)


def get_rule(rule_id: str) -> dict | None:
    """按 ID 取规则定义（供缺陷根因卡片引用条款原文）。"""
    for rule in ENVIRONMENT_RULES:
        if rule["id"] == rule_id:
            return rule
    return None


# ==================== 程序化检查（PRE_WRITE Hook / lint 共用） ====================

# <script type="module"> 或 type='module'
_MODULE_SCRIPT_RE = re.compile(
    r"<script[^>]*type\s*=\s*[\"']module[\"'][^>]*>", re.IGNORECASE
)

# JS 源码中的 ES Module 语法（import ... from / export {...} / export default 等）
_ES_SYNTAX_RE = re.compile(
    r"^\s*(?:import\s+(?:[\w{},*\s]+\s+from\s+)?[\"'][^\"']+[\"']"
    r"|export\s+(?:\{|default|const|let|var|function|class|\*))",
    re.MULTILINE,
)

# 外部资源引用：<script src="https://..."> / <link href="https://..."> / img 等
_EXTERNAL_REF_RE = re.compile(
    r"<(?:script[^>]*\bsrc|link[^>]*\bhref|img[^>]*\bsrc|source[^>]*\bsrc)"
    r"\s*=\s*[\"'](https?://[^\"']+)[\"']",
    re.IGNORECASE,
)

# 本地 js/css 引用（src/href = 相对路径，非协议开头、非 // 开头）
_LOCAL_REF_RE = re.compile(
    r"<(?:script[^>]*\bsrc|link[^>]*\bhref)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
_ABSOLUTE_RE = re.compile(r"^(?:[a-z]+:)?//", re.IGNORECASE)


def find_module_violations(html_content: str) -> list[str]:
    """检测 HTML 中的 `<script type="module">`（违反 ENV-3）。"""
    return [m.group(0)[:120] for m in _MODULE_SCRIPT_RE.finditer(html_content)]


def find_es_syntax(content: str) -> list[str]:
    """检测 JS 源码中的 import/export 语句（违反 ENV-3）。"""
    return [line.strip()[:120] for line in _ES_SYNTAX_RE.findall(content)]


def find_cdn_references(content: str) -> list[str]:
    """检测外部 CDN / 绝对地址资源引用（违反 ENV-2）。"""
    return sorted({m.group(1) for m in _EXTERNAL_REF_RE.finditer(content)})


def extract_local_refs(html_content: str, base_dir: Path | str = ".") -> list[str]:
    """提取 HTML 引用的本地 js/css 文件路径（相对路径形式原样返回）。

    过滤掉外链、内联 data:、锚点和纯样式表以外的链接。
    """
    refs: list[str] = []
    base = Path(base_dir)
    for m in _LOCAL_REF_RE.finditer(html_content):
        raw = m.group(1).strip()
        if not raw or raw.startswith("#") or raw.startswith("data:"):
            continue
        if _ABSOLUTE_RE.match(raw):
            continue
        # 去掉查询串/锚点
        path = raw.split("?")[0].split("#")[0]
        if not (path.endswith(".js") or path.endswith(".css")):
            continue
        # 以 html 所在目录为基准解析相对路径
        resolved = (base / path).as_posix() if base != Path(".") else path
        if resolved not in refs:
            refs.append(resolved)
    return refs


def check_reference_closure(
    html_content: str,
    existing_files: Iterable[str],
    pending_writes: Iterable[str] | None = None,
    planned_files: Iterable[str] | None = None,
) -> list[str]:
    """校验 ENV-6 引用闭合：index.html 引用的本地文件是否真实存在。

    Args:
        html_content: index.html 内容
        existing_files: 工作区已存在的文件列表
        pending_writes: 本次写入集中尚未落盘的文件（同轮多文件写入时容忍顺序差异）
        planned_files: plan 承诺的文件清单（承诺过的也不算悬空，交给完成门禁兜底）

    Returns:
        悬空引用列表（空列表 = 通过）。
    """
    existing = {f.lstrip("./") for f in existing_files}
    pending = {f.lstrip("./") for f in (pending_writes or [])}
    planned = {f.lstrip("./") for f in (planned_files or [])}

    dangling = []
    for ref in extract_local_refs(html_content):
        normalized = ref.lstrip("./")
        if normalized in existing or normalized in pending or normalized in planned:
            continue
        # 容忍目录层级差异：按 basename 匹配
        basename = normalized.split("/")[-1]
        if any(e.split("/")[-1] == basename for e in existing | pending):
            continue
        dangling.append(ref)
    return dangling


# ==================== 缺陷类别路由（P3-2 根因卡片） ====================

# 架构类缺陷类型：小上下文最小改动修不了，必须回 coder 重构
ARCHITECTURAL_DEFECT_TYPES = frozenset({
    "es_module_cors",      # ES module 被 CORS 拦截
    "cdn_dependency",      # 外部 CDN 依赖
    "missing_file",        # SPEC 定义但未生成的文件
    "missing_element",     # 入口/关键元素缺失（引用断裂）
    "missing_api",         # 跨文件调用了未导出的方法（API 断裂）
    "missing_global",      # 引用了项目中未定义的全局对象
    "dom_id_mismatch",     # JS 引用的 DOM id 在 HTML 中不存在，绑定静默失效
})

# 冒烟/浏览器缺陷类型 → 契约条款映射（根因卡片用）
_DEFECT_TYPE_TO_RULE = {
    "es_module_cors": "ENV-3",
    "cdn_dependency": "ENV-2",
    "missing_file": "ENV-6",
    "missing_element": "ENV-6",
    "missing_api": "ENV-6",
    "missing_global": "ENV-6",
    "dom_id_mismatch": "ENV-6",
    "no_interaction": "ENV-5",
    "instant_death": "ENV-5",
    "storage_crash": "ENV-4",
}


def classify_defects(defects: list[dict]) -> tuple[list[dict], list[dict]]:
    """把缺陷分为架构类与局部类。

    架构类（模块加载/CDN/文件缺失/入口断裂）不允许走 defect_repair
    （它被结构上禁止新建/重构文件），必须携带根因卡片回 coder；
    局部语法/存储类走小上下文定向修复。

    Returns:
        (architectural, local) 两个列表。
    """
    architectural, local = [], []
    for d in defects or []:
        dtype = (d.get("type") or "").lower()
        message = (d.get("message") or "").lower()
        evidence = (d.get("evidence") or "").lower()
        is_arch = (
            dtype in ARCHITECTURAL_DEFECT_TYPES
            # 浏览器报错里隐含的架构问题（消息级判断，类型级缺失时兜底）
            or ("es module" in message or "esm" in message)
            or ("cors" in message and "module" in (message + evidence))
            or ("err_file_not_found" in message + evidence)
            or ("failed to load module" in message + evidence)
        )
        (architectural if is_arch else local).append(d)
    return architectural, local


def build_root_cause_card(defects: list[dict]) -> str:
    """为架构类缺陷构建「根因卡片」：缺陷类型 + 契约条款 + 允许的重构方案。

    该卡片注入 coder 对话上下文，指导其做跨文件重构而非局部语法修补。
    """
    if not defects:
        return ""
    lines = [
        "## 🔴 架构级缺陷（最小改动无法修复，必须按以下方案重构）",
        "",
        "> 这些缺陷源于违反运行环境契约，逐个打补丁不会收敛，请一次性调整架构。",
        "",
    ]
    seen_rules = set()
    for d in defects:
        dtype = (d.get("type") or "?").lower()
        rule_id = _DEFECT_TYPE_TO_RULE.get(dtype, "")
        lines.append(f"- **[{d.get('type', '?')}]** {d.get('message', '')}")
        if d.get("evidence"):
            lines.append(f"  - 证据: {d['evidence']}")
        if rule_id and rule_id not in seen_rules:
            rule = get_rule(rule_id)
            if rule:
                seen_rules.add(rule_id)
                lines.append(f"  - 契约条款 {rule_id} {rule['title']}：{rule['detail']}")
        if d.get("suggestion"):
            lines.append(f"  - 允许的重构方向: {d['suggestion']}")
    lines.append("")
    lines.append(
        "重构要求：可以新建/重写文件（write_file），允许调整 index.html 的脚本加载方式；"
        "修复后必须 run_preview 自验通过再声明完成。"
    )
    return "\n".join(lines)


# ==================== 跨文件 API 契约检查（F3，需求 124 事故） ====================
# 事故：app.js 调用了 utils.js 从未实现的 Utils.toast/copyText——多文件批量生成时
# 各文件对共享工具的想象不一致。LLM 评估抓不到这种「静默断层」（页面不报错、
# 只是按钮死了），但它是确定性可查的：解析每个 JS 的全局导出集合，比对全项目引用。

# 浏览器内置全局（被引用不算 missing_global）
_BROWSER_GLOBALS = frozenset({
    "Math", "JSON", "Object", "Array", "Date", "Number", "String", "Boolean",
    "Promise", "RegExp", "Map", "Set", "Symbol", "Error", "TypeError",
    "console", "document", "window", "localStorage", "sessionStorage",
    "performance", "history", "location", "navigator", "screen", "URL",
    "Blob", "File", "FileReader", "FormData", "Request", "Response",
    "fetch", "alert", "confirm", "prompt", "requestAnimationFrame",
    "cancelAnimationFrame", "setTimeout", "setInterval", "clearTimeout",
    "clearInterval", "parseInt", "parseFloat", "isNaN", "encodeURIComponent",
    "decodeURIComponent", "Event", "CustomEvent", "Audio", "Image",
})

_OBJECT_ASSIGN_RE = re.compile(
    r'(?:(?:window|global|self)\s*\.\s*)?\b([A-Za-z_$][\w$]*)\s*=\s*\{'
)
_ALIAS_RE = re.compile(
    r'(?:window|global|self)\s*\.\s*([A-Za-z_$][\w$]*)\s*=\s*([A-Za-z_$][\w$]*)\s*[,;\n]'
)
_FUNCTION_DECL_RE = re.compile(r'\bfunction\s+([A-Za-z_$][\w$]*)')
_CLASS_DECL_RE = re.compile(r'\bclass\s+([A-Za-z_$][\w$]*)')
_PROTO_METHOD_RE = re.compile(
    r'\b([A-Z][\w$]*)\s*\.\s*prototype\s*\.\s*([A-Za-z_$][\w$]*)\s*=\s*function'
)
# Utils.method = function(...) / window.Utils.method = function(...) ——
# 「先建空对象再逐个赋值」是原生 JS 最常见的封装写法，此前漏检导致大量误报
_ATTR_METHOD_RE = re.compile(
    r'(?:window|global|self)\s*\.\s*([A-Za-z_$][\w$]*)\.([A-Za-z_$][\w$]*)\s*='
    r'\s*function|([A-Z][\w$]*)\.([A-Za-z_$][\w$]*)\s*=\s*function'
)

_CALL_RE = re.compile(r'\b(?:window\s*\.\s*)?([A-Z][\w$]*)\s*\.\s*([a-z_$][\w$]*)\s*\(')
_CLASSLIST_RE = re.compile(r'classList\s*\.\s*(?:add|remove|toggle)\s*\(\s*[\'"]([^\'"]+)[\'"]')


def _match_brace(src: str, open_idx: int) -> int:
    """返回与 src[open_idx]='{' 配对的 '}' 下标（跳过字符串/正则字面量从简处理）。"""
    depth = 0
    i = open_idx
    in_str = None
    n = len(src)
    while i < n:
        ch = src[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'`":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _extract_js_apis(src: str) -> tuple[dict[str, set[str]], set[str], set[str]]:
    """提取单个 JS 源码里的全局 API 信息。

    Returns:
        (apis, opaque_globals, declared_names)
        apis: {全局名: 方法集} —— 仅对象字面量/prototype 方式可枚举的
        opaque_globals: 存在定义但方法不可静态枚举的（构造函数/class 声明）
        declared_names: 所有声明过的顶层名字
    """
    apis: dict[str, set[str]] = {}
    opaque: set[str] = set()
    declared: set[str] = set()

    # 构造函数 / class 声明 → 不透明全局
    for name in _FUNCTION_DECL_RE.findall(src):
        if name[0].isupper():
            opaque.add(name)
            declared.add(name)

    # 顶层函数声明名集合：对象字面量里的 `key: funcName`（ES5 简写引用）
    # 指向这些函数，是合法的实现方式，不得误报为缺失（需求 127 事故）
    declared_funcs = set(_FUNCTION_DECL_RE.findall(src))
    for name in _CLASS_DECL_RE.findall(src):
        opaque.add(name)
        declared.add(name)

    # X.prototype.method = function → 可枚举方法
    for gname, meth in _PROTO_METHOD_RE.findall(src):
        apis.setdefault(gname, set()).add(meth)
        declared.add(gname)

    # Utils.method = function / window.Utils.method = function —— 对象逐方法赋值
    for fm in _ATTR_METHOD_RE.finditer(src):
        gname = fm.group(1) or fm.group(3)
        meth = fm.group(2) or fm.group(4)
        apis.setdefault(gname, set()).add(meth)
        declared.add(gname)

    # 对象字面量赋值：X = { ... } / window.X = { ... }
    pos = 0
    literals: dict[str, str] = {}   # 名字 -> 方法集
    literal_order: list[tuple[str, int]] = []  # (名字, 出现顺序) 用于别名解析
    while True:
        m = _OBJECT_ASSIGN_RE.search(src, pos)
        if not m:
            break
        name = m.group(1)
        open_idx = m.end() - 1
        close_idx = _match_brace(src, open_idx)
        body = src[open_idx + 1:close_idx]
        methods = set(re.findall(r'(?:^|[{,])\s*([A-Za-z_$][\w$]*)\s*:\s*function', body, re.M))
        methods |= set(re.findall(r'(?:^|[{,])\s*([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{', body, re.M))
        # `key: funcName`（ES5 简写引用指向本文件已声明函数）→ 合法方法
        for kname, refname in re.findall(
            r'(?:^|[{,])\s*([A-Za-z_$][\w$]*)\s*:\s*([A-Za-z_$][\w$]*)',
            body, re.M,
        ):
            if refname in declared_funcs:
                methods.add(kname)
        prefixed = m.group(0).lstrip().startswith(("window.", "global.", "self"))
        key = f"__direct__{name}" if prefixed else name
        literals[key] = methods
        literal_order.append((key, m.start()))
        declared.add(name)
        if prefixed:
            apis.setdefault(name, set()).update(methods)
        pos = close_idx + 1

    # 别名导出：window.X = Y（Y 是此前解析过的对象字面量）
    for m in _ALIAS_RE.finditer(src):
        gname, source = m.group(1), m.group(2)
        declared.add(gname)
        if source in literals:
            apis.setdefault(gname, set()).update(literals[source])
        elif source in opaque:
            opaque.add(gname)

    return apis, opaque, declared


def check_cross_file_contract(files: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """确定性跨文件契约检查（零 LLM）。

    - API 闭合：所有 `Global.method(...)` 引用必须能在某个 JS 文件的
      导出集合中找到；引用未定义全局同样报缺陷（需求 122 的 Game is not defined）
    - classList 启发式：JS 动态操作的类名在所有 CSS 中无任何规则 → warning
      （需求 124：JS 切换 .show 而 CSS 只有 .hidden，遮罩永远关不掉）

    Args:
        files: {相对路径: 文本内容}，含 .js/.css/.html

    Returns:
        (defects, warnings)：defects 为架构类确定性缺陷（severity=critical，
        会经 classify_defects 路由回 coder 并携带根因卡片）；warnings 不参与路由。
    """
    js_files = {f: c for f, c in files.items() if f.endswith(".js")}
    css_text = "\n".join(c for f, c in files.items() if f.endswith(".css"))
    html_text = "\n".join(c for f, c in files.items() if f.endswith(".html"))

    all_apis: dict[str, set[str]] = {}
    all_opaque: set[str] = set()
    api_source: dict[str, str] = {}
    for fname, src in js_files.items():
        # 内联 <script> 场景由调用方拆好传入；这里只处理纯 js 文件
        apis, opaque, _declared = _extract_js_apis(src)
        for gname, methods in apis.items():
            all_apis.setdefault(gname, set()).update(methods)
            api_source.setdefault(gname, fname)
        all_opaque.update(opaque)

    defects: list[dict] = []
    warnings: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for fname, src in js_files.items():
        # 去掉字符串与注释内容再找引用点（行级近似即可）
        cleaned_lines = []
        for line in src.split("\n"):
            code = re.sub(r"'[^']*'|\"[^\"]*\"|`[^`]*`", '""', line)
            code = re.sub(r"//.*$", "", code)
            cleaned_lines.append(code)
        cleaned = "\n".join(cleaned_lines)

        for m in _CALL_RE.finditer(cleaned):
            gname, meth = m.group(1), m.group(2)
            if gname in _BROWSER_GLOBALS:
                continue
            line_no = cleaned.count("\n", 0, m.start()) + 1

            if gname in all_opaque:
                continue  # 构造函数/class 全局，方法不可枚举，不误报
            if gname in all_apis:
                if meth not in all_apis[gname]:
                    key = (gname, meth)
                    if key in seen:
                        continue
                    seen.add(key)
                    src_file = api_source.get(gname, "?")
                    defects.append({
                        "type": "missing_api",
                        "severity": "critical",
                        "message": (
                            f"跨文件 API 断裂：{fname} 第 {line_no} 行调用了 "
                            f"{gname}.{meth}(...)，但 {src_file} 导出的 {gname} "
                            f"并未实现该方法（现有: {sorted(all_apis[gname])[:8]}）。"
                            f"要么在 {src_file} 补实现并更新 exports，"
                            f"要么在调用方自行实现该能力"
                        ),
                        "evidence": f"{gname}.{meth}(",
                        "source_file": fname,
                        "_source": "api_closure",
                    })
            else:
                key = (f"__global__{gname}", "")
                if key in seen:
                    continue
                seen.add(key)
                defects.append({
                    "type": "missing_global",
                    "severity": "critical",
                    "message": (
                        f"{fname} 引用了项目中从未定义的全局对象 {gname}"
                        f"（第 {line_no} 行 {gname}.{meth}）。"
                        f"检查 script 引入顺序与导出名拼写"
                    ),
                    "evidence": f"{gname}.{meth}(",
                    "source_file": fname,
                    "_source": "api_closure",
                })

    # classList ↔ CSS 契约启发式
    if css_text.strip():
        css_classes = set(re.findall(r'\.([A-Za-z_][\w-]*)', css_text))
        used_dynamic: dict[str, str] = {}
        for fname, src in js_files.items():
            for cls in _CLASSLIST_RE.findall(src):
                used_dynamic.setdefault(cls, fname)
        for cls, fname in sorted(used_dynamic.items()):
            if cls not in css_classes:
                warnings.append({
                    "type": "css_class_missing",
                    "severity": "warning",
                    "message": (
                        f"{fname} 通过 classList 操作了类名 .{cls}，"
                        f"但没有任何 CSS 规则定义它——样式切换可能不生效"
                    ),
                })

    # DOM-id 契约（需求 132：JS 绑了 HTML 不存在的 id，Utils.on 静默 no-op，交互失效无报错）
    defects += check_dom_id_contract(js_files, html_text)
    return defects, warnings


# ==================== DOM-id 契约检查（需求 132 事故） ====================
# 事故：blogs.js 把 submit handler 绑到 #blogForm，但 index.html 只有 #createForm/
# #editForm。Utils.on 对 null 元素静默 return，提交回退原生表单刷新，CRUD 全废且无报错。
# 与 missing_api 同源（跨文件契约不一致），但 LLM 评估和现有 api_closure 都抓不到——
# 后者只查 JS↔JS 符号导出，不覆盖 JS 的 DOM 选择器 ↔ HTML 的 id=。确定性零 LLM。
_HTML_ID_RE = re.compile(r"\bid\s*=\s*[\"']([A-Za-z_][\w-]*)[\"']")
_JS_DYNAMIC_ID_RE = re.compile(
    r"\.id\s*=\s*[\"']([A-Za-z_][\w-]*)[\"']"
    r"|setAttribute\s*\(\s*[\"']id[\"']\s*,\s*[\"']([A-Za-z_][\w-]*)[\"']"
)
_JS_ID_REF_RE = re.compile(
    r"getElementById\s*\(\s*[\"']([A-Za-z_][\w-]*)[\"']"
    r"|querySelector(?:All)?\s*\(\s*[\"']#([A-Za-z_][\w-]*)"
    r"|\$\s*\(\s*[\"']#([A-Za-z_][\w-]*)"
)


def check_dom_id_contract(js_files: dict, html_text: str) -> list[dict]:
    """确定性 DOM-id 契约检查（零 LLM）。

    扫描 JS 中引用的 DOM id（getElementById/querySelector('#x')/$('#x')），与 HTML
    声明的 id= 及 JS 动态创建的 id 对账。引用了不存在的 id → 架构类缺陷
    （dom_id_mismatch，经 classify_defects 路由回 coder 携根因卡片）。

    这类 bug 特征：Utils.on/getElementById 对 null 元素静默 no-op，页面不报错、只是对应
    交互失效（需求 132：blogs.js 绑 #blogForm，提交走原生刷新，CRUD 全废且零报错）。
    """
    if not js_files or not html_text:
        return []

    declared: set[str] = set(_HTML_ID_RE.findall(html_text))
    # JS 动态创建的 id（el.id= / setAttribute('id',...)）+ innerHTML 模板里的 id="x"
    dynamic: set[str] = set()
    for src in js_files.values():
        for m in _JS_DYNAMIC_ID_RE.finditer(src):
            dynamic.update(g for g in m.groups() if g)
        dynamic.update(_HTML_ID_RE.findall(src))
    known = declared | dynamic

    defects: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for fname, src in js_files.items():
        for m in _JS_ID_REF_RE.finditer(src):
            ref_id = next((g for g in m.groups() if g), None)
            if not ref_id or ref_id in known:
                continue
            key = (fname, ref_id)
            if key in seen:
                continue
            seen.add(key)
            line_no = src.count("\n", 0, m.start()) + 1
            defects.append({
                "type": "dom_id_mismatch",
                "severity": "critical",
                "dimension": "runtime",
                "message": (
                    f"DOM-id 契约断裂：{fname} 第 {line_no} 行引用了 #{ref_id}"
                    f"，但 index.html 及 JS 动态创建中均无此 id。"
                    f"Utils.on/getElementById 对不存在的元素静默 no-op，对应交互会失效"
                    f"（如提交回退原生表单刷新）。请把引用改成 HTML 中真实存在的 id。"
                ),
                "evidence": m.group(0)[:80],
                "source_file": fname,
                "_source": "dom_id_contract",
            })
    return defects
