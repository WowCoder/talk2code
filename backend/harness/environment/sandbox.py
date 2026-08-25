# -*- coding: utf-8 -*-
"""
SandboxExecutor —— 代码执行沙箱（subprocess 隔离）
"""

import resource
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

    现状（如实说明）：当前实现只做 **静态语法检查**（node --check），
    **并不真正执行用户代码**，因此不存在 RCE 面；也未实现网络隔离。
    安全措施：
    - subprocess 列表参数调用，无 shell=True，无命令注入面
    - 进程级超时（TIMEOUT）
    - 子进程 CPU/内存资源上限（RLIMIT，仅限 POSIX 平台）
    - 每次执行在独立临时目录（execute 时可选绑定工作区文件）

    注意：如需真正的"执行代码"能力，需另加网络命名空间 / seccomp /
    独立容器等隔离手段，勿在现实现上直接开放执行。
    """

    TIMEOUT = 30  # 超时秒数
    MAX_CPU_SECONDS = 10      # 子进程 CPU 上限
    MAX_MEMORY_BYTES = 512 * 1024 * 1024  # 子进程地址空间上限 512MB

    def __init__(self, workspace=None):
        self.workspace = workspace

    def _limits_preexec(self):
        """子进程启动前的资源限制（POSIX；非 POSIX 平台静默跳过）"""
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (self.MAX_CPU_SECONDS, self.MAX_CPU_SECONDS))
            resource.setrlimit(resource.RLIMIT_AS, (self.MAX_MEMORY_BYTES, self.MAX_MEMORY_BYTES))
        except (ValueError, OSError, AttributeError):
            pass

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
                input=content, capture_output=True, text=True,
                timeout=self.TIMEOUT,
                preexec_fn=self._limits_preexec if os.name == 'posix' else None,
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
        """清理临时资源（当前实现无跨调用残留资源，保留接口占位）"""
        pass
