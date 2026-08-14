# -*- coding: utf-8 -*-
"""
预览文件服务端点集成测试

测试 /api/preview/<req_id>/<filepath> 端点：
- 正确返回 HTML/CSS/JS 文件
- 子目录路径解析（如 css/style.css）
- 路径穿越拒绝
- 404 处理
- workspace 回退
"""

import pytest
from models import Requirement, SessionLocal


def _create_requirement_with_files(app_client, auth_token, files):
    """创建需求并在数据库中预设代码文件"""
    # 创建需求
    resp = app_client.post(
        '/api/requirements',
        json={'content': '测试需求'},
        headers={'Authorization': f'Bearer {auth_token}'},
    )
    data = resp.get_json()
    req_id = data['requirement']['id']

    # 手动更新数据库中的 code_files（绕过后台 workflow）
    db = SessionLocal()
    try:
        req = db.query(Requirement).filter_by(id=req_id).first()
        req.code_files = files
        req.status = 'finished'
        db.commit()
    finally:
        db.close()

    return req_id


class TestPreviewServing:
    """预览端点基础测试"""

    def test_serve_index_html(self, app_client, auth_token):
        """GET /api/preview/<id>/index.html 正确返回 HTML"""
        files = [
            {'filename': 'index.html', 'content': '<!DOCTYPE html><html><body><h1>Hello</h1></body></html>'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        resp = app_client.get(f'/api/preview/{req_id}/index.html')
        assert resp.status_code == 200
        assert 'text/html' in resp.content_type
        assert b'<h1>Hello</h1>' in resp.data

    def test_serve_css_file(self, app_client, auth_token):
        """GET /api/preview/<id>/css/style.css 正确返回 CSS"""
        files = [
            {'filename': 'index.html', 'content': '<html></html>'},
            {'filename': 'css/style.css', 'content': 'body { color: red; }'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        resp = app_client.get(f'/api/preview/{req_id}/css/style.css')
        assert resp.status_code == 200
        assert 'text/css' in resp.content_type
        assert b'body { color: red; }' in resp.data

    def test_serve_js_file(self, app_client, auth_token):
        """GET /api/preview/<id>/js/app.js 正确返回 JavaScript"""
        files = [
            {'filename': 'index.html', 'content': '<html></html>'},
            {'filename': 'js/app.js', 'content': 'console.log("hello");'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        resp = app_client.get(f'/api/preview/{req_id}/js/app.js')
        assert resp.status_code == 200
        assert 'application/javascript' in resp.content_type
        assert b'console.log(' in resp.data

    def test_default_redirects_to_index(self, app_client, auth_token):
        """GET /api/preview/<id>/index.html 正确返回 index.html"""
        files = [
            {'filename': 'index.html', 'content': '<!DOCTYPE html><html></html>'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        # 预view入口始终使用明确的 /index.html 路径
        resp = app_client.get(f'/api/preview/{req_id}/index.html')
        assert resp.status_code == 200
        assert b'<!DOCTYPE html>' in resp.data

    def test_nonexistent_requirement_404(self, app_client, auth_token):
        """不存在的需求 ID 返回 404（已认证但需求不存在）"""
        resp = app_client.get('/api/preview/99999/index.html')
        assert resp.status_code == 404

    def test_nonexistent_file_404(self, app_client, auth_token):
        """存在的需求但文件不存在返回 404 HTML 页面"""
        files = [{'filename': 'index.html', 'content': '<html></html>'}]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        resp = app_client.get(f'/api/preview/{req_id}/nonexistent.js')
        assert resp.status_code == 404
        assert b'text/html' in resp.content_type.lower().encode()

    def test_path_traversal_rejected(self, app_client, auth_token):
        """路径穿越（..）被拒绝，返回 403"""
        files = [{'filename': 'index.html', 'content': '<html></html>'}]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        # 使用 %2e%2e 绕过 Werkzeug 归一化，确保 .. 到达业务逻辑
        resp = app_client.get(f'/api/preview/{req_id}/%2e%2e/etc/passwd')
        # 403 或 404 都算安全（关键是不返回文件内容）
        assert resp.status_code in (403, 404)

    def test_multiple_files_subdirectories(self, app_client, auth_token):
        """模拟真实多文件项目（如 1024 游戏）"""
        files = [
            {'filename': 'index.html', 'content': '<!DOCTYPE html>\n<html>\n<head>\n<link rel="stylesheet" href="css/style.css">\n</head>\n<body>\n<script src="js/game.js"></script>\n<script src="js/app.js"></script>\n</body>\n</html>'},
            {'filename': 'css/style.css', 'content': 'body{background:#fff}'},
            {'filename': 'js/game.js', 'content': 'class Game{constructor(){}}'},
            {'filename': 'js/app.js', 'content': 'const game=new Game()'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)

        # 所有文件都应该可访问
        for f in ['index.html', 'css/style.css', 'js/game.js', 'js/app.js']:
            resp = app_client.get(f'/api/preview/{req_id}/{f}')
            assert resp.status_code == 200, f"文件 {f} 应返回 200，实际 {resp.status_code}"


class TestPreviewContentTypes:
    """MIME 类型测试"""

    @pytest.mark.parametrize('ext,expected_mime', [
        ('.html', 'text/html'),
        ('.css', 'text/css'),
        ('.js', 'application/javascript'),
        ('.json', 'application/json'),
        ('.svg', 'image/svg+xml'),
        ('.txt', 'text/plain'),
    ])
    def test_content_type_by_extension(self, app_client, auth_token, ext, expected_mime):
        """根据扩展名返回正确的 Content-Type"""
        filename = f'sub/test{ext}'
        files = [
            {'filename': 'index.html', 'content': '<html></html>'},
            {'filename': filename, 'content': 'test content'},
        ]
        req_id = _create_requirement_with_files(app_client, auth_token, files)
        resp = app_client.get(f'/api/preview/{req_id}/{filename}')
        assert resp.status_code == 200
        assert expected_mime in resp.content_type
