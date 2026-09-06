"""有公开 JSON API 的源：华尔街见闻快讯、Federal Register 总统文件。

这两个都免费无 key。华尔街见闻是国内金融快讯主力（央行被 robots 挡掉后，
它承担了国内货币政策的转述）；Federal Register 提供总统行政令原文，
是"特朗普又干了什么"这类政策事件最权威的一手来源。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from core.http import PoliteClient
from core.models import Item
from core.timeutil import TZ, in_window, parse_any

from .rss import clean_text

log = logging.getLogger(__name__)

WSCN_LIVE = ("https://api-one.wallstcn.com/apiv1/content/lives"
             "?channel=global-channel&limit=40")

# 总统行政令 / 公告原文。PRESDOCU = Presidential Documents。
# 注意 fields 白名单很严：`presidential_document_type` 不是合法字段（会返 400），
# 只能取下面这四个。加字段前先单独试一次请求。
FED_REGISTER = (
    "https://www.federalregister.gov/api/v1/documents.json"
    "?per_page=20&order=newest&conditions[type][]=PRESDOCU"
    "&fields[]=title&fields[]=publication_date&fields[]=html_url"
    "&fields[]=abstract"
)


def fetch_wallstreetcn(client: PoliteClient) -> list[Item]:
    """华尔街见闻全球快讯。条目短、时效强，标题即结论。"""
    body = client.get(WSCN_LIVE)
    if not body:
        return []
    try:
        items_raw = json.loads(body)["data"]["items"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.info("华尔街见闻响应结构变了：%s", exc)
        return []

    items: list[Item] = []
    for it in items_raw:
        title = clean_text(it.get("title") or it.get("content_text") or "", 200)
        url = it.get("uri") or ""
        if not title or not url:
            continue

        ts = it.get("display_time")
        published = (
            datetime.fromtimestamp(ts, timezone.utc).astimezone(TZ) if ts else None
        )
        if not in_window(published):
            continue

        # 快讯的 title 往往就是全部信息，content_text 是同一句的长版本
        summary = clean_text(it.get("content_text") or "", 300)
        if summary[:20] == title[:20]:
            summary = ""

        items.append(Item(
            title=title, url=url, source="华尔街见闻", category="markets",
            published=published, summary=summary,
        ))
    return items


def fetch_federal_register(client: PoliteClient, *, window_hours: int = 24 * 7) -> list[Item]:
    """总统行政令与公告。政策板块的一手来源，比媒体转述准确。"""
    body = client.get(FED_REGISTER)
    if not body:
        return []
    try:
        results = json.loads(body).get("results", [])
    except (json.JSONDecodeError, AttributeError) as exc:
        log.info("Federal Register 响应不是 JSON：%s", exc)
        return []

    items: list[Item] = []
    for doc in results:
        title = clean_text(doc.get("title") or "", 200)
        url = doc.get("html_url") or ""
        if not title or not url:
            continue

        published = parse_any(doc.get("publication_date") or "")
        if not in_window(published, window_hours):
            continue

        # 总统文件的 abstract 多为 null，标题本身已经说清是什么令；补一句来源说明，
        # 顺便给相关性闸门一点信号（行政令天然属于政策板块）。
        summary = clean_text(doc.get("abstract") or "", 300)
        if not summary:
            summary = "美国总统行政令 / 公告原文（Federal Register 一手文件）"

        items.append(Item(
            title=title, url=url, source="Federal Register", category="policy",
            published=published, summary=summary,
        ))
    return items
