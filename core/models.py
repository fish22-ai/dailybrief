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
# 2026-09-11 改版：结构升级成参考站 fish22-ai/dailybrief-ui 的数据模型。
# 起因是上一版把 chain 写成一根平文本（"A → B → C"），页面为了摆出参考站那种
# 「4 个 STEP + 详情面板」的样子，只能用 _chain_stages() 把字符串**按位置硬切成
# 4 段**。三个字段同源，渲染出来自然是同一句话 —— 实测 Step 1 的「输入触发源」
# 「底层传导机理」「关键受波及大类资产定价」全是"美国与伊朗冲突升级"。
# 所以这次让模型直接产出结构化字段，页面不再猜：
#
#   summary30s            30秒事实内核，3 条客观事实
#   transmissionChain     金融传导全景脉络，固定 4 段，每段：
#                           step / name / trigger / mechanism /
#                           assetImpacts[{asset, direction, note}] /
#                           timeHorizon / keySensitivity
#                         direction ∈ DIRECTION_VALUES，页面据此决定涨跌配色
#   historicalAnalogy     历史参照系 {event, year, comparison}
#   gameTheoryStakeholders 博弈各方 [{party, stance, bottomLine}]
#   forwardIndicators     前瞻红线指标 [{indicator, threshold, significance}]
#   takeaways             行动启示 {investor, industry, personal}
#   notes                 解析（字符串数组，把链条里的专业概念摊开讲）
#
# 参考站还有 surfaceVsCore（表面直觉 vs 机构内核）和 reflectionQuestion
# （内化自测思考题），**用户明确要求不要**，所以这边既不产出也不渲染。
# 同理，所有小节标题只用中文，不挂 "(Fact Nucleus)" 这类英文括注。
#
# what / why / actions 已删除：what/why 被事实内核与传导机理覆盖，actions（盯什么）
# 被 forwardIndicators（指标 + 阈值 + 意义）取代，后者更具体。
INSIGHT_LIST_FIELDS = ("notes",)
# 传输链每段的资产影响方向，只有这四个值合法 —— 页面按它选绿涨/红跌/琥珀震荡。
DIRECTION_VALUES = ("up", "down", "volatile", "neutral")
# 参考站是 4 段，页面也按 4 个 STEP 摆。少于这个数说明机制又被压成结论了。
CHAIN_STAGES = 4
INSIGHT_FIELDS = ("summary30s", "transmissionChain", "historicalAnalogy",
                  "gameTheoryStakeholders", "forwardIndicators", "takeaways", "notes")

# ── 旧归档（2026-09-11 之前）的字段 ──
# data/ 里已有的历史 JSON 还是 what/why/chain/watch/term 或四段老格式。
# 渲染层必须继续认它们，重跑 build_site.py 不能让历史页面掉内容，见 build_site.render_card。
LEGACY_TEXT_FIELDS = ("what", "why", "chain")
LEGACY_OPTIONAL_LIST_FIELDS = ("actions",)

