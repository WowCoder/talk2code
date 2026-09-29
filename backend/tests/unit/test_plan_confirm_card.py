# -*- coding: utf-8 -*-
"""确认「需求理解」之后的落库形态。

守三件事：
1. 确认卡片内容不缩水 —— 与刚才那张待确认卡片同一份数据（含 file_structure 明细与
   每一条验收条件）。否则「已确认」那条消息会比用户刚看过的确认卡少东西。
2. **验收条件必须在确认卡片上**。它是 verify 判定通过与否的唯一依据；
   用户签字时也看不到它的话，「确认」这个动作就没有任何约束力。
2. 插入位置落在「本轮 TL 分析结果」之后。早期实现在之前，实时视图在之后，
   同一条动作在新老需求里会显示成两种顺序；带反馈重出计划时还会贴到上一轮计划后面。

任务队列在用例内被替换掉：确认接口只负责落卡片 + 入队，入队后真起编码线程
会去读检查点并让用例变得又慢又飘。
"""
import pytest

from models import Requirement, SessionLocal


PLAN = {
    'requirement_restated': '一个用方向键控制龙的贪吃龙小游戏',
    'features': ['方向键控制龙移动', '吃到龙珠龙身增长'],
    'assumptions': ['速度随时间加快（你没提，默认做成渐进加速）'],
    'acceptance_criteria': [
        {'id': 'AC-1', 'label': '点击开始按钮后游戏启动', 'feature': '方向键控制龙移动',
         'anchor': '开始遮罩层上的开始按钮',
         'how_to_verify': '点击开始按钮，遮罩消失后棋盘出现'},
        {'id': 'AC-2', 'label': '吃到龙珠龙身增长', 'feature': '吃到龙珠龙身增长',
         'anchor': '画面中央的棋盘区域',
         'how_to_verify': '连续移动吃到龙珠后，分数数值增加'},
    ],
    'file_structure': ['index.html', 'css/style.css', 'js/game.js', 'js/utils.js'],
    'tech_stack': {'framework': '原生 JS', 'css': '原生 CSS', 'storage': 'localStorage'},
    'complexity': 'S',
}


def _tl_plan_msg(round_label: str) -> dict:
    return {
        'role': 'agent',
        'name': 'Leon（技术负责人）',
        'content': f'## 📋 需求分析结果 {round_label}',
        'plan': dict(PLAN),
        'preserve': True,
        'timestamp': '2026-09-28 10:00:00',
    }


@pytest.fixture
def planning_req(app_client, auth_token, test_user, monkeypatch):
    """造一条停在待确认状态的需求，并屏蔽任务队列。"""
    from services.task_queue import task_queue

    monkeypatch.setattr(task_queue, 'submit', lambda *a, **k: 'fake-task-id')

    def _make(dialogue):
        db = SessionLocal()
        try:
            req = Requirement(
                user_id=test_user['id'],
                title='贪吃龙小游戏',
                content='做一个贪吃龙小游戏',
                status='planning',
                dialogue_history=dialogue,
            )
            db.add(req)
            db.commit()
            db.refresh(req)
            return req.id
        finally:
            db.close()

    return _make, auth_token


def _confirm(app_client, token, req_id, feedback=''):
    return app_client.post(
        f'/api/requirements/{req_id}/confirm',
        json={'feedback': feedback},
        headers={'Authorization': f'Bearer {token}'},
    )


def _load_dialogue(req_id):
    db = SessionLocal()
    try:
        req = db.query(Requirement).filter(Requirement.id == req_id).first()
        return list(req.dialogue_history or [])
    finally:
        db.close()


def test_confirm_card_follows_plan_and_keeps_full_content(app_client, planning_req):
    make, token = planning_req
    req_id = make([
        {'role': 'user', 'name': '用户', 'content': '做一个贪吃龙小游戏'},
        _tl_plan_msg('第 1 轮'),
        {'role': 'tool_call', 'name': 'tool', 'content': '已创建 index.html'},
    ])

    resp = _confirm(app_client, token, req_id)
    assert resp.status_code == 200, resp.get_json()

    dh = _load_dialogue(req_id)
    plan_idx = next(i for i, m in enumerate(dh) if m.get('plan'))
    card_idx = next(i for i, m in enumerate(dh) if m.get('plan_confirmed'))
    assert card_idx == plan_idx + 1, '确认卡片应紧跟在 TL 分析结果之后'

    card_msg = dh[card_idx]
    assert card_msg['role'] == 'user'
    card = card_msg['plan_confirmed']
    # 内容与浮层卡同源：少任何一项都会表现为「确认后计划变简略了」。
    # 尤其 acceptance_criteria —— 它是判定的依据，若确认卡上没有它，
    # 用户签字的内容和系统验收的内容就重新分裂成两份。
    for field in PLAN:
        assert card.get(field) == PLAN[field], f"{field} 在确认卡片上缺失或不一致"
    assert card['acceptance_criteria'][0]['anchor'] == PLAN['acceptance_criteria'][0]['anchor']


def test_confirm_card_follows_latest_plan_after_feedback_round(app_client, planning_req):
    """带反馈重出计划后：卡片跟在「本轮」计划之后，不能贴到上一轮后面。"""
    make, token = planning_req
    req_id = make([
        {'role': 'user', 'name': '用户', 'content': '做一个贪吃龙小游戏'},
        _tl_plan_msg('第 1 轮'),
        {'role': 'user', 'name': '用户', 'content': '对 Plan 的反馈：请用 React', 'plan_feedback': True},
        _tl_plan_msg('第 2 轮'),
        {'role': 'tool_call', 'name': 'tool', 'content': '已创建 index.html'},
    ])

    resp = _confirm(app_client, token, req_id)
    assert resp.status_code == 200, resp.get_json()

    dh = _load_dialogue(req_id)
    plan_idxs = [i for i, m in enumerate(dh) if m.get('plan')]
    card_idx = next(i for i, m in enumerate(dh) if m.get('plan_confirmed'))
    assert card_idx == plan_idxs[-1] + 1, '确认卡片应跟在最新一轮计划之后'
    assert card_idx > plan_idxs[0], '不能贴到上一轮计划后面'


def test_feedback_confirm_does_not_persist_card(app_client, planning_req):
    """带反馈时计划要重出，不能先落一张「已确认」卡。"""
    make, token = planning_req
    req_id = make([
        {'role': 'user', 'name': '用户', 'content': '做一个贪吃龙小游戏'},
        _tl_plan_msg('第 1 轮'),
    ])

    resp = _confirm(app_client, token, req_id, feedback='请改成暗色主题')
    assert resp.status_code == 200, resp.get_json()

    dh = _load_dialogue(req_id)
    assert not any(m.get('plan_confirmed') for m in dh)
