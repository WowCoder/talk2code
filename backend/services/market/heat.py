# -*- coding: utf-8 -*-
"""市集热度算法（设计文档 §4）。

    heat = (独立访客 × 1 + 点赞 × 3 + 留言 × 2) / (上架小时数 + 2) ^ 1.5 × 100

三个信号按"主动程度"加权：主动表达 > 被动曝光。时间衰减保证新作品有出头窗口，
否则早期站点会永久霸榜。HEAT_SCALE 只影响展示量级（避免新作品显示 0），
不改变排序的相对关系。
"""
W_VISIT = 1.0
W_LIKE = 3.0
W_COMMENT = 2.0

DECAY_EXP = 1.5          # Hacker News 用 1.8；本平台内容少，1.5 让内容活得久一点
DECAY_BASE_HOURS = 2.0   # 防止刚发布（hours=0）时除数过小导致分数爆炸

HEAT_SCALE = 100.0


def compute_heat(
    uniq_visitors: int,
    likes: int,
    comments: int,
    hours_since_listed: float,
) -> float:
    """计算热度。全部入参都可缺省为 0。"""
    hours = max(0.0, float(hours_since_listed or 0.0))
    score = (
        (uniq_visitors or 0) * W_VISIT
        + (likes or 0) * W_LIKE
        + (comments or 0) * W_COMMENT
    )
    return score / ((hours + DECAY_BASE_HOURS) ** DECAY_EXP) * HEAT_SCALE


def display_heat(heat: float) -> int:
    """对外展示的热度。最小 1 —— 显示 0 会让"刚上架"看起来像"没人要"。"""
    return max(1, int(round(heat)))
