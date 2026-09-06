"""国内源的 HTML 列表页解析。

实测结论（2026-09-06）：
- 央行官网 robots.txt 只允许 Baiduspider，`Disallow: /` 对其他 UA 生效 → 按 CLAUDE.md
  "遵守 robots.txt" 的要求，**不抓央行**，已从注册表移除。
- 证监会有 robots.txt 且允许抓取；`c100028` 那个列表页的数据是 2021 年的旧存档，
  真正在更新的是 `xwfb/index.shtml`（要闻，日期为 MM-DD）与 `c100039`（政策法规，
  日期为 YYYY-MM-DD），两个都用。
- 外汇局无 robots.txt，政策法规列表页 URL 内嵌 /safe/YYYY/MMDD/。
- 东方财富 np-listapi 接口已 404，改用国内经济频道列表页，日期在文章 URL 里。

日期提取有两条路：URL 内嵌（date_pattern），或列表项文本里的日期（回退）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.http import PoliteClient
from core.models import Item
from core.timeutil import (
    SLOW_WINDOW_HOURS, TZ, WINDOW_HOURS, in_window, now_local, parse_any,
)

log = logging.getLogger(__name__)

_DATE_YMD_SLASH = re.compile(r"/(20\d{2})(\d{2})(\d{2})")            # 外汇局等
_DATE_EM = re.compile(r"/a/(20\d{2})(\d{2})(\d{2})\d+\.html")        # 东方财富文章
_TEXT_FULL_DATE = re.compile(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})")
_TEXT_MD = re.compile(r"\b(\d{1,2})-(\d{1,2})\b")                    # 证监会要闻 "09-04"
_MIN_TITLE = 10


def _decode(body: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _date_from_url(url: str, pattern: Optional[re.Pattern]) -> Optional[datetime]:
    if pattern is None:
        return None
    m = pattern.search(url)
    return parse_any("-".join(m.groups())) if m else None


def _strip_noise(soup: BeautifulSoup) -> None:
    """去掉 script/style，避免它们的文本混进日期匹配。"""
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()


def _date_near(anchor) -> Optional[datetime]:
    """在锚点周围找日期。

    证监会要闻页有两种版式：一种是 <li>标题 09-04</li>，日期在同一 li 里；
    另一种是幻灯片区的 <div>，日期不在容器内而在紧邻的兄弟节点上。所以先看
    li/父节点，再看前后兄弟，最后看祖父 —— 逐级放宽，避免抓到隔壁条目的日期。
    """
    li = anchor.find_parent("li")
    candidates = [li] if li else [anchor.parent]
    candidates += [anchor.find_next_sibling(), anchor.find_previous_sibling()]
    if not li and anchor.parent is not None:
        candidates.append(anchor.parent.parent)

    for node in candidates:
        if node is None:
            continue
        dt = _date_from_text(re.sub(r"\s+", " ", node.get_text(" ", strip=True)))
        if dt:
            return dt
    return None


def _date_from_text(text: str) -> Optional[datetime]:
    """列表项文本里的日期。MM-DD 形式按"不晚于今天"推断年份（跨年时不会算成未来）。"""
    m = _TEXT_FULL_DATE.search(text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, tzinfo=TZ)
        except ValueError:
            return None

    m = _TEXT_MD.search(text)
    if m:
        mo, d = (int(x) for x in m.groups())
        today = now_local()
        for year in (today.year, today.year - 1):
            try:
                dt = datetime(year, mo, d, tzinfo=TZ)
            except ValueError:
                return None
            if dt <= today:
                return dt
    return None


def fetch_list_page(
    client: PoliteClient,
    *,
    url: str,
    source: str,
    category: str,
    link_pattern: str,
    date_pattern: Optional[re.Pattern] = _DATE_YMD_SLASH,
    tier: str = "stable",
    limit: int = 15,
    window_hours: int = WINDOW_HOURS,
) -> list[Item]:
    """通用列表页提取：取 href 含 link_pattern 且锚文本够长的链接。

    用 URL 特征而不是 class 名来筛正文链接 —— 站点改版时不容易碎。
    """
    body = client.get(url)
    if not body:
        return []

    soup = BeautifulSoup(_decode(body), "lxml")
    _strip_noise(soup)
    items: list[Item] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if link_pattern not in href:
            continue
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if len(title) < _MIN_TITLE:
            continue

        full = urljoin(url, href)
        if full in seen:
            continue
        seen.add(full)

        published = _date_from_url(full, date_pattern)
        if published is None:
            published = _date_near(a)
        # 列表页上找不到日期的，基本都是幻灯位的旧存档（如证监会主席历次演讲），
        # 不是当期新闻。RSS 那边缺日期还值得保留，列表页这边直接丢。
        if published is None or not in_window(published, window_hours):
            continue

        items.append(Item(
            title=title[:200], url=full, source=source, category=category,
            published=published, tier=tier,
        ))
        if len(items) >= limit:
            break

    if not items:
        log.info("%s: 列表页解析成功但窗口内无条目 (%s)", source, url)
    return items


def fetch_csrc_news(client: PoliteClient) -> list[Item]:
    """证监会要闻，日期形如 09-04。官方源用 7 天宽窗口。"""
    return fetch_list_page(
        client,
        url="http://www.csrc.gov.cn/csrc/xwfb/index.shtml",
        source="中国证监会", category="finance",
        link_pattern="/content.shtml", date_pattern=None,
        window_hours=SLOW_WINDOW_HOURS,
    )


def fetch_csrc_policy(client: PoliteClient) -> list[Item]:
    """证监会政策法规，日期形如 2026-05-22。更新慢，但条目质量最高。"""
    return fetch_list_page(
        client,
        url="http://www.csrc.gov.cn/csrc/c100039/common_list.shtml",
        source="中国证监会", category="finance",
        link_pattern="/content.shtml", date_pattern=None,
        window_hours=SLOW_WINDOW_HOURS,
    )


def fetch_safe(client: PoliteClient) -> list[Item]:
    return fetch_list_page(
        client,
        url="https://www.safe.gov.cn/safe/zcfg/index.html",
        source="国家外汇管理局", category="finance", link_pattern="/safe/20",
        date_pattern=re.compile(r"/safe/(20\d{2})/(\d{2})(\d{2})/"),
        window_hours=SLOW_WINDOW_HOURS,
    )


def fetch_eastmoney(client: PoliteClient) -> list[Item]:
    return fetch_list_page(
        client,
        url="https://finance.eastmoney.com/a/cgnjj.html",
        source="东方财富", category="finance", link_pattern="/a/20",
        date_pattern=_DATE_EM, limit=20,
    )
