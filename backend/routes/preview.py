# -*- coding: utf-8 -*-
"""预览文件服务 + Chat 意图路由辅助函数"""
from flask import jsonify, Response
from flask_jwt_extended import jwt_required, get_jwt_identity
from utils.db import get_db, transactional_db
from utils.sse import SSEMessage, get_current_timestamp
from services.sse_manager import sse_manager
from harness.agent_names import TL_NAME
from factory import app, logger

# ==================== 辅助函数 ====================

from utils.sse import SSEMessage
from utils.sse import get_current_timestamp


# ==================== Chat 意图路由辅助函数 ====================

def _handle_chat_quick(req_id, requirement, user_message, chat_router, db):
    """Chat 模式 QUICK 意图：直接回答用户关于代码的问题，不修改代码"""
    from sqlalchemy.orm.attributes import flag_modified
    from harness.observability.sse_reporter import SSEReporter

    # 构建代码上下文
    code_context = ""
    if requirement.code_files:
        lines = ["## 当前项目文件"]
        for f in requirement.code_files:
            fname = f.get('filename', 'unknown')
            content = f.get('content', '')
            line_count = content.count('\n') + 1 if content else 0
            preview = '\n'.join(content.split('\n')[:15]) if content else '(空)'
            lines.append(f"\n### {fname} ({line_count} 行)\n```\n{preview}\n```")
        code_context = '\n'.join(lines)

    sse_reporter = SSEReporter(sse_manager)

    answer = chat_router.handle_quick(
        requirement=user_message,
        history=requirement.dialogue_history or [],
        code_context=code_context,
        is_chat=True,
    )

    # 保存对话历史
    dialogue_list = list(requirement.dialogue_history or [])
    dialogue_list.append({
        'role': 'user', 'name': '用户',
        'content': user_message,
        'timestamp': get_current_timestamp(),
    })
    dialogue_list.append({
        'role': 'agent', 'name': TL_NAME,
        'content': answer,
        'status': 'completed',
        'timestamp': get_current_timestamp(),
    })
    requirement.dialogue_history = dialogue_list
    flag_modified(requirement, 'dialogue_history')
    requirement.status = 'finished'
    db.commit()

    # SSE 推送
    sse_reporter.dialogue(req_id, 'user', '用户', user_message)
    sse_reporter.dialogue(req_id, 'agent', TL_NAME, answer, 'completed')
    sse_reporter.complete(req_id)

    logger.info(f"Chat QUICK 回答完成 (req_id={req_id})")
    return jsonify({
        'message': 'success',
        'intent': 'quick',
        'answer': answer,
        'dialogue_history': dialogue_list,
    }), 200


def _handle_chat_ambiguous(req_id, requirement, user_message, db):
    """Chat 模式 AMBIGUOUS 意图：生成澄清问题"""
    from sqlalchemy.orm.attributes import flag_modified
    from harness.instructions.nodes import _generate_clarify_questions
    from llm.client import get_client as _get_llm_client

    try:
        client = _get_llm_client()
        questions = _generate_clarify_questions(client, user_message)
        if not questions:
            questions = [
                {"id": "q1", "type": "text", "label": "请更具体地描述你想要的修改效果"},
                {"id": "visual_style", "type": "radio",
                 "label": "修改后你偏好哪种视觉风格？",
                 "options": ["保持现有风格", "极简白", "暖柔风格", "暗黑科技", "活泼多彩", "无偏好"]},
            ]
    except Exception as e:
        logger.warning(f"Chat 澄清问题生成失败: {e}")
        questions = [
            {"id": "q1", "type": "text", "label": "请更具体地描述你想要的修改"},
        ]

    dialogue_list = list(requirement.dialogue_history or [])
    dialogue_list.append({
        'role': 'user', 'name': '用户',
        'content': user_message,
        'timestamp': get_current_timestamp(),
    })
    dialogue_list.append({
        'role': 'system', 'name': TL_NAME,
        'content': '修改意见不够明确，需要补充一些信息',
        'status': 'needs_clarification',
        'question_form': {'questions': questions},
    })
    requirement.dialogue_history = dialogue_list
    flag_modified(requirement, 'dialogue_history')
    requirement.status = 'finished'
    db.commit()

    # SSE 推送澄清表单
    msg = SSEMessage.format_event('question-form', {'questions': questions})
    sse_manager.broadcast(str(req_id), msg)

    logger.info(f"Chat 触发澄清 (AMBIGUOUS), req_id={req_id}")
    return jsonify({
        'needs_clarification': True,
        'question_form': {'questions': questions},
        'dialogue_history': dialogue_list,
    }), 200


# ==================== 预览文件服务 ====================

# MIME 类型映射，用于预览端点返回正确的 Content-Type
_PREVIEW_MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.mjs': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.ttf': 'font/ttf',
    '.txt': 'text/plain; charset=utf-8',
}


def _get_mime_type(filepath: str) -> str:
    """根据文件扩展名返回 MIME 类型"""
    import os as _os
    _, ext = _os.path.splitext(filepath)
    return _PREVIEW_MIME_TYPES.get(
        ext.lower(), 'application/octet-stream'
    )


@app.route('/api/preview/<int:req_id>/<path:filepath>')
@jwt_required()
def preview_serve(req_id: int, filepath: str):
    """
    预览文件服务端点

    为前端 iframe 提供生成的代码文件，支持子目录路径（如 css/style.css）。
    相对路径引用（<link href="css/style.css">、<script src="js/game.js">）
    通过此端点自动解析。

    数据源优先级：
    1. Workspace 磁盘文件（实时生成中，SSE 推送后立即可用）
    2. 数据库 code_files（已完成的任务，作为持久化兜底）

    安全：JWT 认证 + 拒绝路径穿越（.. / ~）+ 用户归属校验。
    """
    # 安全校验：拒绝路径穿越
    if '..' in filepath or filepath.startswith('/') or filepath.startswith('~'):
        logger.warning(f"预览请求拒绝非法路径: req_id={req_id}, path={filepath}")
        return _preview_error_html('非法文件路径', 403)

    from models import Requirement

    current_user_id = int(get_jwt_identity())

    with get_db() as db:
        requirement = db.query(Requirement).filter(
            Requirement.id == req_id,
            Requirement.user_id == current_user_id
        ).first()
        if not requirement:
            return _preview_error_html('需求不存在', 404)

        # 优先从 workspace 读取（实时生成时更及时）
        from harness.state.workspace import WorkspaceFS
        user_id = requirement.user_id
        workspace = WorkspaceFS(user_id, req_id)
        if workspace.exists(filepath):
            content = workspace.read(filepath)
            if content.strip():  # 非空才返回
                mime = _get_mime_type(filepath)
                return Response(content, mimetype=mime)

        # 回退到数据库 code_files（已完成的任务）
        if requirement.code_files:
            for f in requirement.code_files:
                if f.get('filename') == filepath:
                    content = f.get('content', '')
                    if content.strip():  # 非空才返回
                        mime = _get_mime_type(filepath)
                        return Response(content, mimetype=mime)

        # 文件不存在（返回 HTML 而非 JSON，iframe 可友好展示）
        return _preview_error_html(f'文件尚未生成: {filepath}', 404)



def _preview_error_html(message: str, status: int = 404):
    """生成预览错误页面（HTML 格式，iframe 友好）"""
    import html as _html
    # 转义用户可控的 filepath，防止反射型 XSS
    message = _html.escape(message)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<style>
  body {{ display:flex; align-items:center; justify-content:center; min-height:100vh;
         margin:0; font-family:system-ui,-apple-system,sans-serif;
         background:#f8f9fa; color:#6b7280; }}
  .box {{ text-align:center; padding:40px; }}
  .code {{ font-size:64px; font-weight:200; color:#d1d5db; margin-bottom:12px; }}
  .msg {{ font-size:15px; }}
</style></head>
<body><div class="box">
<div class="code">{status}</div><div class="msg">{message}</div>
</div></body></html>"""
    return Response(
        html,
        status=status,
        mimetype='text/html; charset=utf-8',
        headers={'X-Content-Type-Options': 'nosniff'},
    )


