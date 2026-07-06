# -*- coding: utf-8 -*-
"""
真实端到端流程测试（需要 LLM API Key + Playwright）

测试完整的「需求创建 → Agent 生成 → 预览服务 → 浏览器验证」闭环。
这些测试标记为 slow/e2e，默认不运行，需要手动触发：

    cd backend && python -m pytest tests/integration/test_e2e_real.py -v -m e2e --timeout=300

依赖条件：
- LLM API Key 已配置（.env）
- Playwright + Chromium 已安装
"""

import json
import os
import time
from pathlib import Path

import pytest


# 跳过条件：未配置真实的 LLM API Key
_has_real_llm = bool(
    os.environ.get('LLM_API_KEY')
    and os.environ.get('LLM_API_KEY') not in ('test-api-key', '')
)

requires_llm = pytest.mark.skipif(
    not _has_real_llm,
    reason="需要真实 LLM API Key（设置 LLM_API_KEY 环境变量）",
)


def _has_playwright():
    """检查 Playwright 是否可用"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


requires_playwright = pytest.mark.skipif(
    not _has_playwright(),
    reason="需要 Playwright（pip install playwright && playwright install chromium）",
)


@pytest.mark.slow
@pytest.mark.e2e
class TestE2ERealGeneration:
    """真实 E2E 生成流程"""

    @requires_llm
    def test_simple_counter_creation_and_preview(self, app_client, auth_token):
        """
        完整闭环测试：创建简单计数器需求 → 等待生成完成 → 验证预览可用

        使用最简单的需求（计数器），预期 Agent 能快速完成。
        """
        # 1. 创建需求
        resp = app_client.post(
            '/api/requirements',
            json={
                'content': (
                    '创建一个计数器页面，包含一个数字显示和两个按钮（+1 和 -1），'
                    '使用 Tailwind CSS，按钮点击时实时更新数字'
                ),
            },
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        assert resp.status_code == 201, f"创建需求失败: {resp.get_json()}"
        req_id = resp.get_json()['requirement']['id']
        print(f"\n创建需求 #{req_id}，等待 Agent 生成...")

        # 2. 轮询等待生成完成（最长 5 分钟）
        max_wait = 300  # seconds
        start = time.time()
        while time.time() - start < max_wait:
            resp = app_client.get(
                f'/api/requirements/{req_id}',
                headers={'Authorization': f'Bearer {auth_token}'},
            )
            data = resp.get_json()
            status = data['requirement']['status']

            if status == 'finished':
                code_files = data['requirement'].get('code_files', [])
                file_names = [f['filename'] for f in code_files]
                print(f"生成完成，文件: {file_names}")
                break
            elif status == 'failed':
                # 检查 dialogue_history 中的错误信息
                history = data['requirement'].get('dialogue_history', [])
                error_msgs = [
                    m.get('content', '') for m in history
                    if m.get('role') == 'system' and '错误' in m.get('content', '')
                ]
                pytest.fail(
                    f"需求 #{req_id} 生成失败。"
                    f"错误信息: {'; '.join(error_msgs[-3:]) if error_msgs else '无'}"
                )
            elif status == 'pending':
                # 可能还没有被 task_queue 调度
                pass

            time.sleep(5)
        else:
            # 超时：获取当前状态
            resp = app_client.get(
                f'/api/requirements/{req_id}',
                headers={'Authorization': f'Bearer {auth_token}'},
            )
            status = resp.get_json()['requirement']['status']
            pytest.fail(f"需求 #{req_id} 在 {max_wait}s 内未完成（当前状态: {status}）")

        # 3. 验证生成结果
        resp = app_client.get(
            f'/api/requirements/{req_id}',
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        data = resp.get_json()
        code_files = data['requirement'].get('code_files', [])
        file_map = {f['filename']: f['content'] for f in code_files}

        # 必须包含 index.html
        assert 'index.html' in file_map, f"必须生成 index.html，实际文件: {list(file_map.keys())}"
        index_html = file_map['index.html']
        assert len(index_html) > 50, f"index.html 内容太少 ({len(index_html)} 字符)"

        # 应包含按钮和计数器相关元素
        assert 'button' in index_html.lower(), "index.html 应包含按钮"

        # 4. 验证预览端点
        preview_resp = app_client.get(f'/api/preview/{req_id}/index.html')
        assert preview_resp.status_code == 200
        assert 'text/html' in preview_resp.content_type

        # 如果存在 CSS 文件，验证预览端点能正确服务
        for fname in file_map:
            if fname != 'index.html':
                resp = app_client.get(f'/api/preview/{req_id}/{fname}')
                assert resp.status_code == 200, f"预览端点应能服务 {fname}"

        # 5. Playwright 浏览器验证（如果可用）
        if _has_playwright():
            self._verify_with_playwright(file_map)
        else:
            print("跳过 Playwright 验证（未安装）")

    def _verify_with_playwright(self, file_map: dict):
        """使用 Playwright 在真实浏览器中验证生成的页面"""
        from playwright.sync_api import sync_playwright

        # 将 index.html 写入临时文件（包含所有内联资源）
        html = file_map.get('index.html', '')
        tmp_dir = Path('/tmp/talk2code_e2e_test')
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # 写入所有文件（保持目录结构）
        for fname, content in file_map.items():
            fpath = tmp_dir / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding='utf-8')

        index_path = tmp_dir / 'index.html'
        errors = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                page = context.new_page()

                page.on('console', lambda msg: (
                    errors.append(f'[console.{msg.type}] {msg.text}')
                    if msg.type == 'error' else None
                ))
                page.on('pageerror', lambda err: errors.append(f'[pageerror] {err}'))

                page.goto(index_path.resolve().as_uri(), wait_until='domcontentloaded')
                page.wait_for_timeout(2000)  # 给 JS 初始化时间

                browser.close()
        except Exception as e:
            errors.append(f'[playwright_error] {e}')

        # 清理临时文件
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # 只报告真正的错误（忽略 CDN/Tailwind 警告）
        real_errors = [
            e for e in errors
            if 'cdn.tailwindcss.com should not be used' not in e
        ]
        assert not real_errors, f"页面运行时发现错误:\n" + '\n'.join(real_errors)

    @requires_llm
    def test_vague_requirement_triggers_clarification(self, app_client, auth_token):
        """
        测试模糊需求触发澄清流程（不生成代码，返回问题列表）
        """
        resp = app_client.post(
            '/api/requirements',
            json={'content': '做个应用'},
            headers={'Authorization': f'Bearer {auth_token}'},
        )
        assert resp.status_code == 201
        req_id = resp.get_json()['requirement']['id']

        # 等待处理完成（pending → 需要澄清 或 finished）
        max_wait = 120
        start = time.time()
        while time.time() - start < max_wait:
            resp = app_client.get(
                f'/api/requirements/{req_id}',
                headers={'Authorization': f'Bearer {auth_token}'},
            )
            data = resp.get_json()
            status = data['requirement']['status']

            if status in ('finished', 'failed'):
                # 检查是否有澄清问题
                history = data['requirement'].get('dialogue_history', [])
                # 模糊需求应该不会生成代码文件
                code_files = data['requirement'].get('code_files', [])
                print(f"状态={status}, 文件数={len(code_files)}")
                # 如果有澄清表单或没有生成代码 → 通过
                has_clarify = any(
                    m.get('question_form') for m in history
                )
                assert has_clarify or len(code_files) == 0, (
                    "模糊需求应触发澄清或至少不生成大段代码"
                )
                break

            time.sleep(3)
        else:
            pytest.fail(f"需求 #{req_id} 在 {max_wait}s 内未完成")
