# -*- coding: utf-8 -*-
"""
Web 工具：search_docs / fetch_cdn_library

每个工具对应一个 ToolHandler 子类。
"""

from harness.tools.registry import (
    ToolDefinition, ToolResult, ToolHandler, register_tool,
)


# ==================== ToolHandler 子类 ====================

class SearchDocsHandler(ToolHandler):
    """搜索 MDN/CanIUse 文档获取 API 兼容性信息"""

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        return ToolResult(
            content="文档搜索功能需要在网络环境中运行，当前返回基本提示。"
                    "建议使用 MDN Web Docs (developer.mozilla.org) 查阅最新文档。"
        )

    def search_docs(self, query: str) -> ToolResult:
        return self.execute({"query": query})


class FetchCdnLibraryHandler(ToolHandler):
    """获取主流 CDN 库的最新版本号和使用示例"""

    CDN_INFO = {
        "tailwind": {
            "css": '<script src="https://cdn.tailwindcss.com"></script>',
            "version": "latest via CDN",
            "note": "使用 Play CDN 快速原型开发，生产环境建议构建工具"
        },
        "bootstrap": {
            "css": '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">',
            "js": '<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>',
            "version": "5.3.3",
        },
        "react": {
            "js": '<script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>\n<script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>',
            "version": "18",
            "note": "React 需要 Babel 转译 JSX，推荐使用 standalone Babel: <script src=\"https://unpkg.com/@babel/standalone/babel.min.js\"></script>"
        },
        "vue": {
            "js": '<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>',
            "version": "3",
        },
        "chartjs": {
            "js": '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>',
            "version": "4.4.0",
        },
    }

    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        return self.fetch_cdn_library(args.get("library", ""))

    def fetch_cdn_library(self, library: str) -> ToolResult:
        key = library.lower()
        info = self.CDN_INFO.get(key)
        if not info:
            return ToolResult(content=f"未找到 {library} 的 CDN 信息。支持的库: {', '.join(self.CDN_INFO.keys())}")

        lines = [f"## {library} (v{info.get('version', 'N/A')})"]
        for tag_type in ["css", "js"]:
            if tag_type in info:
                lines.append(f"\n{tag_type.upper()}:")
                lines.append(f"```html\n{info[tag_type]}\n```")
        if "note" in info:
            lines.append(f"\n注意: {info['note']}")

        return ToolResult(content="\n".join(lines))


# ==================== 注册函数 ====================

def register_web_tools(registry):
    search_handler = SearchDocsHandler()
    cdn_handler = FetchCdnLibraryHandler()

    registry.register(ToolDefinition(
        name="search_docs",
        description="搜索 MDN/CanIUse 文档获取 API 兼容性信息",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词，如 'CSS Grid' 或 'localStorage'"}
            },
            "required": ["query"]
        },
        handler=lambda **kwargs: search_handler.execute(kwargs),
        permission="read",
        tool_handler=search_handler,
    ))

    registry.register(ToolDefinition(
        name="fetch_cdn_library",
        description="获取主流 CDN 库（Tailwind/React 等）的最新版本号和使用示例",
        parameters={
            "type": "object",
            "properties": {
                "library": {"type": "string", "description": "库名称，如 'tailwind' / 'react' / 'vue'"}
            },
            "required": ["library"]
        },
        handler=lambda **kwargs: cdn_handler.execute(kwargs),
        permission="read",
        tool_handler=cdn_handler,
    ))
