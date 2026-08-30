# -*- coding: utf-8 -*-
"""
测试 lint_js ES Module 检测
"""

import pytest
from unittest.mock import Mock, patch

from harness.tools.code_tools import CodeToolHandler


class TestLintJsESModule:
    """lint_js ES Module 语法检测测试"""

    def _make_handler(self, file_contents=None):
        """创建带 mock workspace 的 CodeToolHandler"""
        ws = Mock()
        contents = dict(file_contents or {})

        def _read(filename):
            if filename in contents:
                return contents[filename]
            raise FileNotFoundError(filename)

        ws.read = _read
        return CodeToolHandler(ws)

    @patch('subprocess.run')
    def test_es_module_file_uses_input_type_module(self, mock_run):
        """ES Module 文件使用 --input-type=module 参数"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/app.js": "export function init() { console.log('hello'); }\nimport { util } from './utils.js';"
        })
        result = handler.lint_js("js/app.js")

        # 环境契约 ENV-3：ES Module 是确定性违规，与语法错误同级
        assert not result.success
        called_args = mock_run.call_args[0][0]
        assert "--input-type=module" in called_args
        assert "ENV-3" in result.error and "ES Module" in result.error

    @patch('subprocess.run')
    def test_commonjs_file_uses_default_mode(self, mock_run):
        """CommonJS 文件不使用 --input-type=module"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/app.js": "const fs = require('fs');\nfunction main() { return 42; }\nmodule.exports = main;"
        })
        result = handler.lint_js("js/app.js")

        assert result.success
        called_args = mock_run.call_args[0][0]
        assert "--input-type=module" not in called_args

    @patch('subprocess.run')
    def test_plain_script_uses_default_mode(self, mock_run):
        """普通脚本（无 export/import/require）使用默认模式"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/app.js": "let count = 0;\nfunction add() { count++; }\nadd();"
        })
        result = handler.lint_js("js/app.js")

        assert result.success
        called_args = mock_run.call_args[0][0]
        assert "--input-type=module" not in called_args

    @patch('subprocess.run')
    def test_real_syntax_error_reported(self, mock_run):
        """真实语法错误正确报告"""
        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "SyntaxError: Unexpected token '}'"
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/app.js": "function broken() { return } }"
        })
        result = handler.lint_js("js/app.js")

        assert not result.success
        assert "SyntaxError" in result.error

    @patch('subprocess.run')
    def test_es_module_with_export_default(self, mock_run):
        """export default 语法正确识别为 ES Module"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/game.js": "export default class Game { constructor() {} start() {} }"
        })
        result = handler.lint_js("js/game.js")

        assert not result.success
        assert "ENV-3" in result.error
        called_args = mock_run.call_args[0][0]
        assert "--input-type=module" in called_args

    @patch('subprocess.run')
    def test_es_module_with_import_type(self, mock_run):
        """import type 语法识别为 ES Module"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/types.js": "import type { Config } from './types';\nexport { Config };"
        })
        result = handler.lint_js("js/types.js")

        assert not result.success
        assert "ENV-3" in result.error
        called_args = mock_run.call_args[0][0]
        assert "--input-type=module" in called_args

    def test_node_not_installed_graceful(self):
        """Node.js 不可用时优雅降级"""
        handler = self._make_handler({"js/app.js": "console.log('test')"})

        with patch('subprocess.run', side_effect=FileNotFoundError()):
            result = handler.lint_js("js/app.js")
            assert result.success
            assert "未安装" in result.content

    @patch('subprocess.run')
    def test_timeout_graceful(self, mock_run):
        """超时时优雅降级"""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["node"], timeout=10)

        handler = self._make_handler({"js/app.js": "while(true){}"})
        result = handler.lint_js("js/app.js")

        assert not result.success
        assert "超时" in result.error

    @patch('subprocess.run')
    def test_es_module_syntax_correct_still_violates_contract(self, mock_run):
        """ES Module 语法正确也违反环境契约 ENV-3（file:// 下 CORS 全灭）"""
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        mock_run.return_value = mock_result

        handler = self._make_handler({
            "js/storage.js": (
                "const STORAGE_KEY = 'app_data';\n"
                "export function save(data) {\n"
                "  localStorage.setItem(STORAGE_KEY, JSON.stringify(data));\n"
                "}\n"
                "export function load() {\n"
                "  const raw = localStorage.getItem(STORAGE_KEY);\n"
                "  return raw ? JSON.parse(raw) : null;\n"
                "}\n"
            )
        })
        result = handler.lint_js("js/storage.js")

        assert not result.success
        assert "ENV-3" in result.error and "IIFE" in result.error
