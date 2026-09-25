# -*- coding: utf-8 -*-
"""
品类 Skill 目录守卫。

Skill 机制本身已存在（manifest.json + SKILL.md + 正则 trigger），缺的是**品类覆盖**：
很长一段时间里 9 个 skill 中只有 `game` 一个品类 skill，其余都是配色/排版/无障碍
这类通用素养——于是"做个待办清单"和"做个数据看板"拿到的是同一套套路。

守住两件事：
1. 五个主流品类都有对应 skill，且能被各自的需求稳定命中；
2. 一个需求**最多命中一个品类 skill**——品类 skill 正文不短，
   多个同时注入会把 prompt 撑大，而 prompt 中位已达 13.7k tokens，
   prefill 是每轮固定成本。
"""

from harness.instructions.skill_loader import get_skill_loader

CATEGORY_SKILLS = {"crud", "tool", "dashboard", "landing", "admin", "game"}

# 品类 -> 代表性需求（至少一条能命中，且不与其他品类交叉）
CATEGORY_SAMPLES = {
    "crud": ["做一个待办清单应用", "写个记账本，能记收入和支出"],
    "tool": ["做一个温度单位换算工具", "写个 JSON 格式化工具"],
    "dashboard": ["做一个销售数据看板，有柱状图和饼图", "做个月度统计报表，展示趋势"],
    "landing": ["做一个产品官网落地页", "写个个人作品集首页"],
    "admin": ["做一个订单管理系统", "做个运营平台后台"],
    "game": ["做一个贪吃蛇小游戏", "写个 2048 游戏"],
}


def _loader():
    sl = get_skill_loader()
    sl._ensure_loaded()
    return sl


def test_all_category_skills_registered():
    names = {m.name for m in _loader()._manifests}
    missing = CATEGORY_SKILLS - names
    assert not missing, f"缺少品类 skill: {missing}"


def test_every_skill_has_parseable_manifest_and_body():
    for m in _loader()._manifests:
        assert m.name, "manifest 必须有 name"
        assert m.description, f"skill '{m.name}' 缺少 description"
        assert m.trigger, f"skill '{m.name}' 缺少 trigger"
        body = m.load_body()
        assert body.strip(), f"skill '{m.name}' 的 SKILL.md 正文为空"


def test_each_category_matches_its_samples():
    sl = _loader()
    for category, samples in CATEGORY_SAMPLES.items():
        for text in samples:
            hit = {m.name for m in sl.match_skills(text)} & CATEGORY_SKILLS
            assert category in hit, f"'{text}' 未命中品类 {category}，实得 {hit}"


def test_category_match_is_mutually_exclusive():
    """一个需求最多命中一个品类 skill，避免 prompt 被多重注入撑大。"""
    sl = _loader()
    for category, samples in CATEGORY_SAMPLES.items():
        for text in samples:
            hit = {m.name for m in sl.match_skills(text)} & CATEGORY_SKILLS
            assert len(hit) <= 1, f"'{text}' 命中多个品类 {hit}，会造成 prompt 膨胀"


def test_injected_body_stays_within_budget():
    """品类 skill 注入后总长要有上限——prompt 每轮都要重发，长度即成本。"""
    sl = _loader()
    for category, samples in CATEGORY_SAMPLES.items():
        body = sl.load_for_task(samples[0])
        assert len(body) < 12000, (
            f"品类 {category} 注入 {len(body)} 字符，超出预算"
        )
