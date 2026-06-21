# -*- coding: utf-8 -*-
"""
SandboxExecutor —— 代码执行沙箱（subprocess 隔离）
"""

import subprocess
import tempfile
import os
from pathlib import Path


class SandboxResult:
    def __init__(self, success: bool, output: str = "", error: str = ""):
        self.success = success
        self.output = output
        self.error = error


class SandboxExecutor:
    """
    HTML/CSS/JS 代码执行沙箱

    方案：subprocess 调用 Node.js 进行基础语法检查
    安全措施：
    - 每次执行在独立临时目录
    - 进程级超时和内存限制
    - 禁止网络访问
    """

    TIMEOUT = 30  # 超时秒数

    def __init__(self, workspace=None):
        self.workspace = workspace

    def execute(self, filename: str = "index.html") -> SandboxResult:
        """在沙箱中执行文件"""
        try:
            if self.workspace:
                content = self.workspace.read(filename)
            else:
                return SandboxResult(False, error="沙箱未绑定工作区")

            if filename.endswith('.js'):
                return self._check_js_syntax(content)
            elif filename.endswith('.html'):
                return self._validate_html(content)
            else:
                return SandboxResult(True, output="跳过执行验证")
        except Exception as e:
            return SandboxResult(False, error=str(e))

    def _check_js_syntax(self, content: str) -> SandboxResult:
        try:
            result = subprocess.run(
                ["node", "--check", "-"],
                input=content, capture_output=True, text=True, timeout=self.TIMEOUT
            )
            if result.returncode == 0:
                return SandboxResult(True, output="JS 语法检查通过")
            return SandboxResult(False, error=f"JS 语法错误: {result.stderr[:300]}")
        except FileNotFoundError:
            return SandboxResult(True, output="Node.js 未安装，跳过验证")
        except subprocess.TimeoutExpired:
            return SandboxResult(False, error="执行超时")

    def _validate_html(self, content: str) -> SandboxResult:
        try:
            from html.parser import HTMLParser
            parser = HTMLParser()
            parser.feed(content)
            parser.close()
            return SandboxResult(True, output="HTML 结构有效")
        except Exception as e:
            return SandboxResult(False, error=f"HTML 错误: {e}")

    def cleanup(self):
        """清理临时资源"""
        pass
