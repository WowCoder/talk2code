# -*- coding: utf-8 -*-
"""
约束检查 Hook 集合 —— 合并自 craft_enforcer.py / security.py / quality.py

所有 Hook 遵循统一模式：
    def _xxx_hook(ctx: HookContext) -> Optional[str]
    返回 None = 通过，返回 str = 失败信息
"""

import re

from harness.observability.logger import get_logger
from harness.constraints.hooks import HookContext

logger = get_logger(__name__)


# ==================== Craft 规则检查 ====================

def _anti_ai_slop_hook(ctx: HookContext):
    """检查 AI 生成代码中的常见坏味道 (placeholder/TODO/占位文本)"""
    if not ctx.tool_args:
        return
    content = ctx.tool_args.get("content", "")
    if not content:
        return

    slop_patterns = [
        (r'lorem ipsum', '检测到 AI 坏味道: "lorem ipsum" 占位文本'),
        (r'TODO: implement', '检测到 AI 坏味道: 空洞的 TODO 注释'),
        (r'add your code here', '检测到 AI 坏味道: "add your code here" 占位标记'),
    ]
    for pattern, msg in slop_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            return msg


# ==================== 安全检查 ====================

def _xss_hook(ctx: HookContext):
    """检查生成的代码中是否有 XSS 风险"""
    if ctx.tool_args:
        content = ctx.tool_args.get("content", "")
        if not content:
            return
        checks = [
            (r'innerHTML\s*=', '使用 innerHTML 存在 XSS 风险，建议使用 textContent 或 createElement'),
            (r'document\.write\(', '使用 document.write() 存在 XSS 风险'),
            (r'eval\(', '使用 eval() 存在安全风险'),
        ]
        for pattern, msg in checks:
            if re.search(pattern, content):
                return f"安全风险: {msg}"


# ==================== 代码质量检查 ====================

def _html_validity_hook(ctx: HookContext):
    """检查 HTML 语法有效性"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".html"):
            content = ctx.tool_args.get("content", "")
            try:
                from html.parser import HTMLParser
                parser = HTMLParser()
                parser.feed(content)
                parser.close()
            except Exception as e:
                return f"HTML 语法错误 ({filename}): {e}"


def _css_lint_hook(ctx: HookContext):
    """检查 CSS 括号平衡"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".css"):
            content = ctx.tool_args.get("content", "")
            open_braces = content.count('{')
            close_braces = content.count('}')
            if open_braces != close_braces:
                return f"CSS 括号不匹配 ({filename}): {{ {open_braces}, }} {close_braces}"


def _js_syntax_hook(ctx: HookContext):
    """检查 JS 语法 + 环境契约 ENV-3（禁止 ES Module）"""
    if ctx.tool_name == "write_file" and ctx.tool_args:
        filename = ctx.tool_args.get("filename", "")
        if filename.endswith(".js"):
            content = ctx.tool_args.get("content", "")
            # 环境契约 ENV-3：file:// 预览下 ES Module 被 CORS 拦截，先于语法检查拦截
            es_usage = re.search(
                r'^\s*(?:import\s+(?:[\w{},*\s]+\s+from\s+)?[\"\x27][^\"\x27]+[\"\x27]'
                r'|export\s+(?:\{|default|const|let|var|function|class|\*))',
                content, re.MULTILINE,
            )
            if es_usage:
                return (
                    f"环境契约违规 ENV-3 ({filename}): 禁止 ES Module（{es_usage.group(0)[:60]}）。"
                    f"请改用 IIFE (function (global) {{ ... }})(window) 包裹，"
                    f"通过 window.XXX 暴露接口，HTML 里用普通 <script src> 引入"
                )
            import subprocess
            try:
                result = subprocess.run(
                    ["node", "--check", "-"],
                    input=content, capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0:
                    return f"JavaScript 语法错误 ({filename}): {result.stderr[:300]}"
            except FileNotFoundError:
                pass  # Node.js 未安装，跳过
            except subprocess.TimeoutExpired:
                return f"JS 语法检查超时 ({filename})"


def _required_files_hook(ctx: HookContext):
    """任务完成时检查是否生成了 index.html"""
    file_list = ctx.state.get("file_list", [])
    if "index.html" not in file_list and not any(f.endswith("index.html") for f in file_list):
        return "缺少必需的 index.html 文件"


# ==================== 环境契约写入时刻校验（PRE_TOOL_USE，零 LLM 成本） ====================

def _validate_environment_on_write(ctx: HookContext):
    """写文件瞬间按环境契约做确定性拦截（审查报告 Phase 2.1）

    拦截七连败中在写入时刻即可判死的失败模式：
    - `<script type="module">`（ENV-3：file:// 下 CORS 全灭）
    - 外部 CDN / 绝对地址资源引用（ENV-2）
    - JS 源码 import/export（ENV-3）

    引用闭合（ENV-6）不在写入时硬拦——同一轮多文件写入存在顺序问题，
    放到完成声明时由 _check_reference_closure_on_complete 兜底。
    """
    if ctx.tool_name not in ("write_file", "edit_file") or not ctx.tool_args:
        return None
    filename = ctx.tool_args.get("filename", "")
    content = ctx.tool_args.get("content", "") or ""
    if not content:
        return None

    from harness.constraints import environment_contract as env

    if filename.endswith(".html"):
        module_tags = env.find_module_violations(content)
        if module_tags:
            return (
                f"[环境契约 ENV-3] {filename} 使用了 <script type=\"module\">。"
                f"预览环境以 file:// 协议加载，ES Module 会被 CORS 拦截导致所有脚本不执行"
                f"（历史需求 #115 即因此七连败）。请改为普通 <script src> 按依赖顺序引入，"
                f"JS 文件用 IIFE 包裹并通过 window.XXX 暴露接口。"
            )
        cdn_refs = env.find_cdn_references(content)
        if cdn_refs:
            return (
                f"[环境契约 ENV-2] {filename} 引用了外部资源: {', '.join(cdn_refs[:3])}。"
                f"预览沙箱无网络，CDN 资源必然加载失败、布局塌陷。"
                f"样式请写本地 css/style.css 或 <style> 内联，第三方能力用原生实现等价替代。"
            )

    if filename.endswith(".js"):
        es_usage = env.find_es_syntax(content)
        if es_usage:
            return (
                f"[环境契约 ENV-3] {filename} 含 ES Module 语法（{es_usage[0][:50]}）。"
                f"file:// 预览下 import/export 被 CORS 拦截。"
                f"请改用 IIFE (function (global) {{ ... }})(window) 包裹，通过 window.XXX 暴露接口。"
            )
    return None


def _check_reference_closure_on_complete(ctx: HookContext):
    """任务完成时校验 index.html 引用闭合（环境契约 ENV-6）

    写入时刻不拦（同轮多文件写入有顺序问题），完成声明时统一兜底：
    引用的本地 js/css 既不在工作区也不在 plan 清单中 → 阻断并列出悬空引用。
    """
    is_complete_signal = (
        ctx.tool_name == "task_complete" or
        (ctx.tool_name is None and (ctx.state or {}).get("current_step") == "task_complete")
    )
    if not is_complete_signal:
        return None

    state = ctx.state or {}
    workspace = state.get("_workspace")
    file_list = state.get("file_list") or []
    if workspace is None and not file_list:
        return None

    from harness.constraints import environment_contract as env

    # 找到入口 HTML（优先 index.html）
    html_files = [f for f in file_list if f.endswith(".html")] if file_list else \
        [f for f in workspace.list() if f.endswith(".html")]
    entry = next((f for f in html_files if f.endswith("index.html")), html_files[0] if html_files else None)
    if not entry:
        return None

    try:
        content = workspace.read(entry) if workspace is not None else None
    except Exception:
        return None
    if content is None:
        return None

    planned = []
    plan = state.get("plan") or {}
    if isinstance(plan, dict):
        planned = plan.get("file_structure") or []

    dangling = env.check_reference_closure(
        content,
        existing_files=file_list or workspace.list(),
        planned_files=planned,
    )
    if dangling:
        return (
            f"[环境契约 ENV-6] {entry} 引用了不存在的本地文件: {', '.join(dangling[:5])}。"
            f"这些引用运行时会报 ERR_FILE_NOT_FOUND（历史需求 #112 即因此失败）。"
            f"请创建缺失文件，或修正/移除无效引用后再声明完成。"
        )

    # 跨文件 API 契约闭合（需求 124 事故：调用了未导出的方法）
    # 完成声明是最后一道确定性关卡——此类断层页面不报错、纯靠静默失效
    try:
        js_css = {}
        all_files = file_list or (workspace.list() if workspace is not None else [])
        for fname in all_files:
            if fname.endswith((".js", ".css")) and not fname.startswith(".task"):
                try:
                    js_css[fname] = workspace.read(fname) if workspace is not None else None
                except Exception:
                    pass
        js_css = {k: v for k, v in js_css.items() if v}
        if js_css:
            defects, _warnings = env.check_cross_file_contract(js_css)
            if defects:
                first = defects[0]
                return (
                    f"[环境契约 ENV-6] 跨文件 API 契约断裂（{len(defects)} 处）: "
                    f"{first['message'][:200]}"
                    + (f" 等共 {len(defects)} 处。" if len(defects) > 1 else "")
                    + " 请补齐导出实现或改为调用已声明的方法后再声明完成。"
                )
    except Exception as e:
        logger.debug(f"跨文件契约检查跳过: {e}")
    return None


# ==================== 统一注册入口 ====================

def register_all_hooks(manager):
    """将所有约束检查 Hook 注册到 HookManager"""
    from harness.constraints.hooks import HookPoint

    # Craft 规则
    manager.register(HookPoint.POST_TOOL_USE, _anti_ai_slop_hook)

    # 安全
    manager.register(HookPoint.POST_TOOL_USE, _xss_hook)

    # 质量
    manager.register(HookPoint.POST_TOOL_USE, _html_validity_hook)
    manager.register(HookPoint.POST_TOOL_USE, _css_lint_hook)
    manager.register(HookPoint.POST_TOOL_USE, _js_syntax_hook)
    manager.register(HookPoint.ON_TASK_COMPLETE, _required_files_hook)
    # ON_TASK_COMPLETE: 跨文件导出一致性 / 交互元素接线 / 契约文件完整性
    # 针对「IIFE 全局命名空间无编译期检查」的架构代价，零 LLM 成本拦截致命结构缺陷，
    # 并为 defect_repair 提供精确定位（如"已定义 function init() 但未挂载到 Timer"）。
    from harness.constraints.static_audit import static_audit_hook
    manager.register(HookPoint.ON_TASK_COMPLETE, static_audit_hook)

    # ---- 进度约束 Hook（硬阻断） ----
    from harness.constraints.progress_hooks import (
        block_unnecessary_read,
        block_premature_completion,
        track_write_success,
    )
    # PreToolUse: 在工具执行前阻断不合理行为
    manager.register(HookPoint.PRE_TOOL_USE, block_unnecessary_read)
    manager.register(HookPoint.PRE_TOOL_USE, block_premature_completion)
    # PreToolUse: 写文件瞬间按环境契约拦截 module/CDN/ES 语法（零 LLM 成本）
    manager.register(HookPoint.PRE_TOOL_USE, _validate_environment_on_write)
    # ON_TASK_COMPLETE / 完成声明: index.html 引用闭合兜底（ENV-6）
    manager.register(HookPoint.PRE_TOOL_USE, _check_reference_closure_on_complete)
    # PostToolUse: write_file 成功后追踪写入、更新 contract
    manager.register(HookPoint.POST_TOOL_USE, track_write_success)
