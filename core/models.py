"""候选条目数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


# 只做两个板块：市场与货币金融、能搬动市场的政策/地缘大事件。
# AI 不再是独立板块 —— AI 只作为"解读"能力出现在每条卡片右侧。
CATEGORIES = ("markets", "policy")

CATEGORY_LABELS = {
    "markets": "市场 · 货币 · 金融",
    "policy": "政策 · 地缘大事件",
}


@dataclass
class Item:
    """一条候选情报。fetcher 只负责填前六个字段，打分阶段回填 score。"""

    title: str
    url: str
    source: str            # 展示用来源名，如 "中国人民银行"
    category: str          # CATEGORIES 之一
    published: Optional[datetime] = None   # 带时区；无法解析时为 None
    summary: str = ""      # RSS/页面自带摘要，规则模式下直接用作卡片摘要
    heat: float = 0.0      # 源自带热度（HN points 等），没有就 0
    tier: str = "stable"   # stable | optional

    # 以下由 pipeline 回填
    norm_url: str = ""
    score: float = 0.0
    score_detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d


# 卡片右侧的 AI 解读字段。这是整个项目的核心产出 —— 不是复述新闻，而是回答
# "这条为什么重要、钱怎么一环一环流动、背后是哪个公式在动"。规则模式下这些字段
# 为空，页面会明确标注"今日无解读"，而不是拿新闻摘要冒充解读。
#
# 2026-09-06 改版：原先是 what/why/chain/watch/term 五个短字段，用户反馈"看不懂"。
# 根因是 chain 只有 3~4 环，每环之间的机制被压缩掉了，读起来像结论清单而不是推演；
# term 又只给一个概念一句话，链条里其余专业词全靠猜。现在改成四段：
#   what   发生了什么（大白话，不用行话缩写）
#   why    市场为什么在意（它改变了原本被定价的什么预期）
#   chain  金融传导（8~12 环，每环写出机制，不只写结果）
#   notes  解析（把链条里的每个专业概念摊开讲，涉及定价就给公式并解释公式怎么动）
# notes 是字符串数组，其余三个是字符串 —— 校验时要分开处理。
INSIGHT_TEXT_FIELDS = ("what", "why", "chain")
INSIGHT_LIST_FIELDS = ("notes",)
INSIGHT_FIELDS = INSIGHT_TEXT_FIELDS + INSIGHT_LIST_FIELDS

