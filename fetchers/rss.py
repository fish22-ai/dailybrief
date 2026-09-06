"""通用 RSS/Atom fetcher。绝大多数源走这里，不需要单独写代码。"""
from __future__ import annotations

import logging
import re

import feedparser

from core.http import PoliteClient
from core.models import Item
from core.timeutil import WINDOW_HOURS, in_window, parse_rss_date

log = logging.getLogger(__name__)

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_text(raw: str, limit: int = 300) -> str:
    text = _WS.sub(" ", _TAGS.sub(" ", raw or "")).strip()
    return text[:limit]


def fetch_rss(
    client: PoliteClient,
    *,
    url: str,
    source: str,
    category: str,
    tier: str = "stable",
    limit: int = 20,
    window_hours: int = WINDOW_HOURS,
) -> list[Item]:
    body = client.get(url)
    if not body:
        return []

    parsed = feedparser.parse(body)
    if not parsed.entries:
        log.info("no entries parsed from %s (%s)", source, url)
        return []

    items: list[Item] = []
    for entry in parsed.entries:
        title = clean_text(entry.get("title", ""), 200)
        link = entry.get("link") or ""
        if not title or not link:
            continue

        published = parse_rss_date(entry)
        if not in_window(published, window_hours):
            continue

        summary = clean_text(
            entry.get("summary") or entry.get("description") or "", 300
        )
        items.append(Item(
            title=title, url=link, source=source, category=category,
            published=published, summary=summary, tier=tier,
        ))
        if len(items) >= limit:
            break

    return items
