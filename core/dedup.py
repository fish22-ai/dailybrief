"""去重：URL 归一化 + 标题 3-gram Jaccard。对应 CLAUDE.md 第六节。全部纯字符串运算。"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .models import Item
from .scoring import SOURCE_WEIGHTS

# 跟踪参数：命中前缀或全名的一律去掉
_TRACK_PREFIXES = ("utm_", "spm", "ttzg", "_hs")
_TRACK_EXACT = {"from", "ref", "referer", "share", "share_token", "fr", "src", "cid"}

_PUNCT = re.compile(r"[\s　\-—–_·、，,。.！!？?：:；;“”\"'（）()\[\]【】《》<>/\\|~@#$%^&*+=]")

JACCARD_THRESHOLD = 0.6      # CLAUDE.md 给的初始值，实测后可调
NGRAM = 3


def normalize_url(url: str) -> str:
    """去跟踪参数、去 www.、统一 https、统一末尾斜杠。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower() in _TRACK_EXACT or k.lower().startswith(_TRACK_PREFIXES))
    ]

    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def _grams(title: str) -> set[str]:
    s = _PUNCT.sub("", title).lower()
    if len(s) <= NGRAM:
        return {s} if s else set()
    return {s[i:i + NGRAM] for i in range(len(s) - NGRAM + 1)}


def jaccard(a: str, b: str) -> float:
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    return inter / (len(ga) + len(gb) - inter)


def _rank(item: Item) -> tuple:
    """同一事件保留哪条：来源权重优先，其次已算出的分数，最后时间。"""
    return (
        SOURCE_WEIGHTS.get(item.source, 0.5),
        item.score,
        item.published.timestamp() if item.published else 0.0,
    )


def dedup(items: list[Item]) -> tuple[list[Item], int]:
    """返回 (去重后条目, 移除数量)。同一事件保留来源权重更高的那条。"""
    by_url: dict[str, Item] = {}
    for it in items:
        it.norm_url = normalize_url(it.url)
        exist = by_url.get(it.norm_url)
        if exist is None or _rank(it) > _rank(exist):
            by_url[it.norm_url] = it

    kept: list[Item] = []
    for it in sorted(by_url.values(), key=_rank, reverse=True):
        for k in kept:
            # 只在同板块内比标题，跨板块同题（如 AI 监管既是 AI 也是政策）保留两条
            if k.category == it.category and jaccard(k.title, it.title) >= JACCARD_THRESHOLD:
                break
        else:
            kept.append(it)

    return kept, len(items) - len(kept)
