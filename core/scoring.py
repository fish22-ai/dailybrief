"""规则打分与金融相关性闸门。

对应 CLAUDE.md 第六节。不使用任何模型。

这里有两件事：
1. **相关性闸门**（`relevance`）：把"三明治店换供应商""66 号公路百年"这种
   生活/文化类报道挡在候选池外。BBC Business、NYTimes Business 这些频道本身
   就混杂大量软新闻，只靠来源权重和时间衰减是拦不住的 —— 必须看内容。
2. **排序打分**（`score`）：来源权重 + 时间衰减 + 热度 + 相关性强度。
"""
from __future__ import annotations

import math
import re

from . import config
from datetime import datetime

from .models import Item
from .timeutil import WINDOW_HOURS, now_local

# 官方一手源 > 通讯社/主流媒体 > 聚合媒体。键必须与 registry.py 里各源的 source
# 字段完全一致，拼错会静默落到 DEFAULT_SOURCE_WEIGHT —— tests 里有一致性自检。
SOURCE_WEIGHTS: dict[str, float] = {
    # 央行与多边机构：一手政策，最高权重
    "Federal Reserve": 1.0,
    "ECB": 1.0,
    "中国证监会": 1.0,
    "国家外汇管理局": 1.0,
    "US Treasury": 1.0,
    "White House": 1.0,
    "Federal Register": 0.95,
    "USTR": 0.95,
    # 专业财经媒体：市场解读主力
    "WSJ Markets": 0.9,
    "The Economist": 0.9,
    "CNBC Economy": 0.85,
    "CNBC Finance": 0.85,
    "FT Markets": 0.85,
    "华尔街见闻": 0.8,
    "东方财富": 0.7,
    # 综合媒体：地缘政治用，财经条目权重压低
    "BBC": 0.7,
    "DW": 0.65,
    "NPR": 0.65,
    "SCMP": 0.6,
    "UN News": 0.7,
    "Defense News": 0.7,
    "NYTimes Business": 0.6,
    "MarketWatch": 0.55,
}
DEFAULT_SOURCE_WEIGHT = 0.5

# ---------------------------------------------------------------------------
# 相关性词表：判断"这条是不是真的金融/政策大事"
# ---------------------------------------------------------------------------

# 核心信号：出现即高度相关（利率、汇率、财政、监管、地缘冲突）
_CORE = (
    # 货币政策
    "降息", "加息", "降准", "存款准备金", "利率决议", "议息", "LPR", "MLF", "逆回购",
    "量化宽松", "缩表", "扩表", "货币政策", "通胀", "通缩", "CPI", "PPI", "PCE",
    "rate cut", "rate hike", "interest rate", "monetary policy", "inflation",
    "deflation", "quantitative easing", "tapering", "fomc", "basis point",
    "yield curve", "bond yield", "treasury yield", "central bank",
    # 财政与债务
    "财政政策", "赤字", "国债", "地方债", "专项债", "减税", "加税", "财政刺激",
    "fiscal", "deficit", "debt ceiling", "stimulus", "tax cut", "sovereign debt",
    "budget", "bailout",
    # 汇率与资本流动
    "汇率", "人民币", "美元指数", "外汇储备", "资本外流", "贬值", "升值",
    "exchange rate", "currency", "devaluation", "capital outflow", "forex",
    # 监管与市场制度
    "监管", "处罚", "罚款", "反垄断", "征求意见", "新规", "退市", "注册制",
    "regulation", "regulator", "antitrust", "trade war",
    "export control", "sec filing", "ipo", "delisting",
    # 一级市场与银行资本（国内快讯的主要形态：募资、增发、资本补充）
    "募资", "增发", "定增", "配股", "可转债", "补充资本", "资本充足",
    "REITs", "并购", "重组", "回购", "减持", "解禁", "融资",
    "recapitalization", "share sale", "rights issue", "buyback",
    "capital raise", "capital adequacy", "merger", "acquisition",
    # 市场大幅波动。注意：这里只放金融语境下唯一的词。"surge"/"strike" 之类
    # 一词多义的（蟑螂数量 surge、工人 strike）绝不能进核心表 —— 见 _MARKET_CONTEXT。
    "暴跌", "暴涨", "熔断", "崩盘", "抛售", "selloff", "sell-off",
    "rout", "market crash", "bear market", "bull market",
    "recession", "sovereign default", "bankruptcy", "credit crunch", "liquidity",
    "bond market", "equity market", "stock market",
    # 地缘政治（能搬动市场的那类）
    "制裁", "关税", "贸易战", "军事冲突", "开战", "停火", "政变", "大选",
    "ceasefire", "invasion", "coup", "general election", "treaty",
    "geopolitic", "airstrike", "air strike", "military strike",
    "tariff", "sanction", "embargo", "retaliation", "retaliate",
    "executive order", "national emergency", "presidential proclamation",
    "行政令", "国家紧急状态",
)

# 一词多义的词：只有和金融/地缘语境同现时才算核心信号。
# 例："Hokkaido battles cockroach surge" 里的 surge 不是市场词；
#     "heatstroke as an occupational" 里没有任何金融语境。
_AMBIGUOUS = (
    "surge", "plunge", "rally", "crash", "strike", "default", "war",
    "election", "summit", "nuclear", "missile", "troops", "escalation",
    "attack", "oil tanker", "border", "emergency",
)
_MARKET_CONTEXT = (
    "market", "stock", "bond", "yield", "currency", "dollar", "euro", "yen",
    "oil", "gold", "price", "trade", "economy", "economic", "fed", "central bank",
    "investor", "share", "index", "futures", "commodity", "inflation", "rate",
    "tanker", "export", "import", "military", "troops", "defense", "defence",
    "president", "government", "minister", "sanction", "tariff",
    "市场", "股", "债", "汇", "油", "金价", "经济", "贸易", "投资", "利率",
    "军", "政府", "总统", "制裁", "关税",
)

# 次级信号：相关但强度弱，需要配合其他信号
_SECONDARY = (
    "经济", "增长", "GDP", "就业", "失业", "PMI", "楼市", "房地产", "银行",
    "券商", "基金", "股市", "债市", "大宗商品", "原油", "黄金", "美联储",
    "央行", "财政部", "政府", "总统", "首相", "议会", "国会",
    "economy", "growth", "employment", "jobs", "unemployment", "payroll",
    "housing", "bank", "fund", "stocks", "equities", "commodities", "oil",
    "gold", "fed", "treasury", "president", "parliament", "congress",
    "minister", "policy", "trump", "powell", "warsh", "lagarde",
)

# 排除信号：命中即判为软新闻（生活、文化、体育、名人、健康）
_EXCLUDE = (
    # 餐饮 / 生活
    "sandwich", "deli", "restaurant", "recipe", "menu", "coffee", "wine",
    "tourist", "tourism", "travel guide", "road trip", "vacation",
    "cockroach", "insect", "gardening", "zoo", "wildlife", "pet ",
    # 名人 / 娱乐
    "celebrity", "royal family", "wedding", "divorce", "dating", "fashion",
    "beauty", "gossip", "influencer", "reality tv", "movie review", "album",
    "concert tour", "box office", "netflix series", "tv programme",
    # 体育
    "sport", "football", "soccer", "cricket", "olympic", "tennis", "nba",
    "cycling accident", "marathon",
    # 生活服务 / 健康 / 猎奇
    "horoscope", "astrology", "puzzle", "crossword", "quiz",
    "heatstroke", "obituary", "rescued", "airlifted", "found alive",
    "sentenced to death", "drugs case", "serial killer",
    "美食", "菜谱", "旅游", "景点", "明星", "综艺", "球赛", "演唱会", "星座",
    "网红", "粉丝", "楼市新政",
)

# 低于此值不进候选池。0.30 是实测甜点值（拦下率约七成且不漏硬新闻），
# 可在 config.toml 的 [fetch] relevance_threshold 覆盖。
RELEVANCE_THRESHOLD = config.get_float("fetch", "relevance_threshold", 0.30,
                                       lo=0.0, hi=1.0)

W_SOURCE, W_RECENCY, W_HEAT, W_RELEVANCE = 2.0, 2.5, 1.0, 3.5
NO_DATE_RECENCY = 0.25


def _matches(text: str, word: str) -> bool:
    """拉丁词按词边界匹配，中文按子串匹配。

    子串匹配对英文很危险："rout"（暴跌）会命中 "Route 66"，"euro" 会命中
    "Europeans"，"war" 会命中 "warm" —— 实测这两个 bug 让旅游报道拿到 0.375 的
    相关性。中文没有词边界概念，仍用子串。
    """
    if word.isascii():
        # 允许常见词尾变化，否则 "tariff" 匹配不到 "tariffs"、"strike" 匹配不到
        # "strikes"。但仍要求左边界，这样 "rout" 不会命中 "Route 66"。
        return re.search(rf"\b{re.escape(word)}(?:s|es|ed|ing)?\b", text) is not None
    return word in text


def _hits(text: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if _matches(text, w))


def relevance(item: Item) -> float:
    """0~1 的金融/政策相关性。0.05 表示明确无关。

    评分逻辑刻意简单直白：核心词一个就够，次级词要两个以上才算，命中排除词
    直接砍到 0.1（不直接归零 —— 万一是"制裁某体育组织"这类边缘情况，留给
    核心词翻盘的机会）。

    一词多义的词（surge / strike / war…）单独处理：只有当同一条里还出现了
    金融或地缘语境词时才计入核心分。否则"蟑螂数量激增"会被当成市场异动。
    """
    text = f"{item.title} {item.summary}".lower()

    core = _hits(text, _CORE)
    secondary = _hits(text, _SECONDARY)
    excluded = _hits(text, _EXCLUDE)

    if (_hits(text, _AMBIGUOUS) and _hits(text, _MARKET_CONTEXT)):
        core += 1

    if core >= 2:
        score = 1.0
    elif core == 1:
        score = 0.75
    elif secondary >= 3:
        score = 0.55
    elif secondary == 2:
        score = 0.40
    elif secondary == 1:
        score = 0.22
    else:
        score = 0.05

    if excluded:
        # 排除词优先级高于次级词：软新闻里常出现"经济""政府"之类的顺带提及。
        # 两个以上排除词时连核心词也压住 —— 那基本就是纯生活报道了。
        if core == 0 or excluded >= 2:
            score = min(score, 0.10)
        else:
            score *= 0.5

    return round(score, 3)


def _recency(published: datetime | None) -> float:
    """按 36 小时半衰的指数衰减。

    无发布时间的条目给 0.25：实测这类多是列表页幻灯位的旧存档（如证监会主席
    历次演讲），不该和当天新闻平起平坐，但也不该直接丢。
    """
    if published is None:
        return NO_DATE_RECENCY
    age_h = (now_local() - published).total_seconds() / 3600.0
    if age_h < 0:
        age_h = 0.0
    return math.exp(-age_h / (WINDOW_HOURS / 2.0))


def _heat(raw: float) -> float:
    """热度量纲差别大（HN points 几百，多数源为 0），取 log 压平到 0~1。"""
    if raw <= 0:
        return 0.0
    return min(1.0, math.log1p(raw) / math.log1p(500))


def score_item(item: Item) -> Item:
    src = SOURCE_WEIGHTS.get(item.source, DEFAULT_SOURCE_WEIGHT)
    rec, heat, rel = _recency(item.published), _heat(item.heat), relevance(item)
    item.score_detail = {
        "source": round(src, 3),
        "recency": round(rec, 3),
        "heat": round(heat, 3),
        "relevance": rel,
    }
    item.score = round(
        W_SOURCE * src + W_RECENCY * rec + W_HEAT * heat + W_RELEVANCE * rel, 4
    )
    return item


def score_all(items: list[Item]) -> list[Item]:
    """打分并按相关性闸门过滤。返回 (保留条目, 被闸门拦下的数量)。"""
    scored = [score_item(i) for i in items]
    kept = [i for i in scored if i.score_detail["relevance"] >= RELEVANCE_THRESHOLD]
    kept.sort(key=lambda i: i.score, reverse=True)
    return kept, len(scored) - len(kept)
