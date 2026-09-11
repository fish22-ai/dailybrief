"""数据源注册表 —— 每个源一个条目，单独可失败。对应 CLAUDE.md 第四节。

只做三个板块：
  markets  市场 / 货币 / 金融
  policy   政策 / 地缘大事件（能搬动市场的那类）
  tech     科技 / AI 与产业（2026-09-11 新增）

tier 语义：
  stable   主力源，失效要 WARNING 告警
  optional 无官方 RSS / 有付费墙 / 更新很慢，失效只记 debug，不产生日志噪音

每条 note 记录 2026-09-06 的实测结果，替换失效源时先看这里。

被 robots.txt 明确禁止、因此**不抓**的源（CLAUDE.md 第五节要求遵守 robots）：
  - 中国人民银行 pbc.gov.cn：robots 只允许 Baiduspider
  - 一财 yicai.com 的 JSON 接口：robots 禁止
  - export.arxiv.org、news.google.com、search.cnbc.com：`Disallow: /`
其他取不到的：IMF（403）、AP News（403）、BIS（返回 HTML 不是 feed）、
WSJ Economy（AccessDenied）、财联社（返回 HTML 壳）、Reuters Agency（连接失败）、
feeds.reuters.com（已停用）。全部不注册，避免每天固定的失败噪音。
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass
from typing import Callable

from core.http import PoliteClient
from core.models import Item
from core.timeutil import SLOW_WINDOW_HOURS

from . import api, html_cn
from .rss import fetch_rss

log = logging.getLogger(__name__)


@dataclass
class Source:
    key: str
    category: str
    tier: str
    fn: Callable[[PoliteClient], list[Item]]
    note: str = ""


def _rss(url: str, source: str, category: str, tier: str = "stable", **kw):
    return functools.partial(
        fetch_rss, url=url, source=source, category=category, tier=tier, **kw
    )


SOURCES: list[Source] = [
    # ---------------- 市场 / 货币 / 金融 ----------------
    Source("fed", "markets", "stable",
           _rss("https://www.federalreserve.gov/feeds/press_all.xml",
                "Federal Reserve", "markets", window_hours=SLOW_WINDOW_HOURS),
           "实测 20 条。其 robots.txt 对 urllib UA 返 403，故 http.py 自己取；"
           "发布频率低，用 7 天宽窗口"),
    Source("ecb", "markets", "stable",
           _rss("https://www.ecb.europa.eu/rss/press.html", "ECB", "markets",
                window_hours=SLOW_WINDOW_HOURS),
           "实测 15 条，发布频率低，用 7 天宽窗口"),
    Source("treasury_fr", "markets", "optional",
           functools.partial(api.fetch_federal_register),
           "Federal Register 里也有财政类文件，但主要归到 policy 板块"),
    Source("wsj_markets", "markets", "optional",
           _rss("https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
                "WSJ Markets", "markets", tier="optional",
                window_hours=SLOW_WINDOW_HOURS),
           "实测能取 20 条但 pubDate 停在 2025-01（feed 已不再更新），"
           "故降为 optional：能取到就用，取不到不告警。正文另有付费墙"),
    Source("cnbc_econ", "markets", "stable",
           _rss("https://www.cnbc.com/id/20910258/device/rss/rss.html",
                "CNBC Economy", "markets"),
           "实测 30 条，更新及时。注意用 cnbc.com 域名，search.cnbc.com 被 robots 禁"),
    Source("cnbc_fin", "markets", "stable",
           _rss("https://www.cnbc.com/id/10000664/device/rss/rss.html",
                "CNBC Finance", "markets", window_hours=SLOW_WINDOW_HOURS),
           "实测 30 条，金融机构与美联储人事；最新条目常在 1~2 天前，用宽窗口"),
    Source("economist_fin", "markets", "stable",
           _rss("https://www.economist.com/finance-and-economics/rss.xml",
                "The Economist", "markets", window_hours=SLOW_WINDOW_HOURS),
           "实测 300 条（含历史存档）。周刊性质、最新条目常在 2~3 天前，"
           "必须用宽窗口否则永远为空；分析深度最高"),
    Source("wallstreetcn", "markets", "stable", api.fetch_wallstreetcn,
           "国内金融快讯主力（央行被 robots 挡掉后靠它转述），实测接口正常"),
    Source("eastmoney", "markets", "optional", html_cn.fetch_eastmoney,
           "国内经济频道列表页，实测 31 条；软新闻较多，靠相关性闸门过滤"),
    Source("ft_markets", "markets", "optional",
           _rss("https://www.ft.com/markets?format=rss", "FT Markets", "markets",
                tier="optional"),
           "实测 25 条标题（正文付费墙，只取标题）"),
    Source("csrc_news", "markets", "optional", html_cn.fetch_csrc_news,
           "证监会要闻，日期形如 09-04；更新不频繁"),
    Source("safe", "markets", "optional", html_cn.fetch_safe,
           "外汇局政策法规，URL 内嵌 /safe/YYYY/MMDD/；更新慢"),

    # ---------------- 政策 / 地缘大事件 ----------------
    Source("federal_register", "policy", "stable",
           functools.partial(api.fetch_federal_register),
           "总统行政令与公告原文，实测 count=8560、可取最新 20 条。"
           "'特朗普又干了什么' 的一手来源，比媒体转述准"),
    Source("whitehouse", "policy", "stable",
           _rss("https://www.whitehouse.gov/presidential-actions/feed/",
                "White House", "policy", window_hours=SLOW_WINDOW_HOURS),
           "实测 30 条总统行动，与 Federal Register 互补（去重会合并）"),
    Source("ustr", "policy", "stable",
           _rss("https://ustr.gov/rss.xml", "USTR", "policy",
                window_hours=SLOW_WINDOW_HOURS),
           "美国贸易代表办公室，关税与贸易政策一手源，实测 10 条"),
    Source("bbc_world", "policy", "stable",
           _rss("https://feeds.bbci.co.uk/news/world/rss.xml", "BBC", "policy"),
           "实测 31 条。软新闻多，靠相关性闸门过滤"),
    Source("dw_world", "policy", "stable",
           _rss("https://rss.dw.com/rdf/rss-en-world", "DW", "policy"),
           "实测 11 条，欧洲视角"),
    Source("defensenews", "policy", "stable",
           _rss("https://www.defensenews.com/arc/outboundfeeds/rss/?outputType=xml",
                "Defense News", "policy"),
           "实测 25 条，军事与国防预算"),
    Source("un_news", "policy", "optional",
           _rss("https://news.un.org/feed/subscribe/en/news/all/rss.xml",
                "UN News", "policy", tier="optional"),
           "实测 30 条，但多为人道议题，与市场关联弱，靠闸门筛"),
    Source("npr_world", "policy", "optional",
           _rss("https://feeds.npr.org/1004/rss.xml", "NPR", "policy",
                tier="optional"),
           "实测 10 条"),
    Source("scmp_china", "policy", "optional",
           _rss("https://www.scmp.com/rss/91/feed", "SCMP", "policy",
                tier="optional"),
           "实测 50 条，含中国议题但混杂香港本地新闻"),

    # ---------------- 科技 / AI 与产业 ----------------
    # 2026-09-11 新增板块。尺子跟另外两个板块不同：看的是算力/芯片供应链格局、
    # 巨头资本开支与竞争位势、产业成本曲线，而不是"发了什么新产品"。所以源挑的是
    # 芯片与产业深度（Ars / Bloomberg），不是消费电子导购。消费品发布靠相关性闸门滤。
    Source("ars_technica", "tech", "stable",
           _rss("https://feeds.arstechnica.com/arstechnica/index",
                "Ars Technica", "tech"),
           "2026-09-11 实测 20 条。芯片、AI 基建、技术机制类偏多，最贴产业尺子"),
    Source("the_verge", "tech", "stable",
           _rss("https://www.theverge.com/rss/index.xml", "The Verge", "tech"),
           "2026-09-11 实测 10 条。产业动态为主，消费数码含量偏高，靠闸门筛"),
    Source("bloomberg_tech", "tech", "stable",
           _rss("https://feeds.bloomberg.com/technology/news.rss",
                "Bloomberg Technology", "tech"),
           "2026-09-11 实测 20 条。资本/产业视角，和 markets 板块的传导链最搭"),
    Source("qbitai", "tech", "optional",
           _rss("https://www.qbitai.com/feed", "量子位", "tech", tier="optional"),
           "2026-09-11 实测 10 条。中文 AI 源，补国内视角"),
]


def fetch_all(client: PoliteClient, *, only: set[str] | None = None) -> tuple[list[Item], dict]:
    """依次跑所有 fetcher，单源异常不影响整体。返回 (条目, 每源统计)。"""
    items: list[Item] = []
    stats: dict[str, dict] = {}

    for src in SOURCES:
        if only and src.key not in only:
            continue
        try:
            got = src.fn(client) or []
        except Exception as exc:                      # 单源崩溃不能让当天任务失败
            level = logging.WARNING if src.tier == "stable" else logging.DEBUG
            log.log(level, "fetcher %s crashed: %s: %s", src.key, type(exc).__name__, exc)
            stats[src.key] = {"count": 0, "tier": src.tier, "error": type(exc).__name__}
            continue

        if not got:
            level = logging.WARNING if src.tier == "stable" else logging.DEBUG
            log.log(level, "fetcher %s returned nothing", src.key)

        for it in got:
            it.tier = src.tier
        items.extend(got)
        stats[src.key] = {"count": len(got), "tier": src.tier}
        log.info("%-18s %-8s %d items", src.key, src.category, len(got))

    return items, stats
