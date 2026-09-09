"""时区与时间窗口。对应 CLAUDE.md 第三节：统一 Asia/Shanghai，抓取窗口 36 小时。"""
from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_tz
from typing import Optional
from zoneinfo import ZoneInfo

from . import config

# 判定"今天"用的时区，也决定产物文件名。写错时区名会让 ZoneInfo 直接抛错，
# 所以这里 try 一次并回落到上海 —— 配置写错不该让整站跑不起来。
_TZ_NAME = config.get_str("schedule", "timezone", "Asia/Shanghai")
try:
    TZ = ZoneInfo(_TZ_NAME)
except Exception:                                          # noqa: BLE001
    logging.getLogger(__name__).warning(
        "config [schedule] timezone = %r 不是合法的 IANA 时区名，"
        "改用 Asia/Shanghai", _TZ_NAME)
    TZ = ZoneInfo("Asia/Shanghai")

WINDOW_HOURS = config.get_int("fetch", "window_hours", 36, lo=1, hi=24 * 30)
# 官方一手源（央行/证监会/Fed/ECB/各家 AI 官博）常常几天才发一条，用 36 小时窗口
# 会导致它们几乎永远进不了候选池 —— 而这些恰恰是最该看的内容。给它们放宽到 7 天，
# 靠时间衰减打分压低排序，再靠 seen 归档保证同一条不会天天重复出现。
SLOW_WINDOW_HOURS = config.get_int("fetch", "slow_window_hours", 24 * 7,
                                   lo=1, hi=24 * 90)


def now_local() -> datetime:
    return datetime.now(TZ)


# 补跑历史日期时由 run_daily.py --date 注入，让产物文件名 / seen / cache 都按这天走
_TARGET_DATE: str | None = None


def set_target_date(date: str) -> None:
    global _TARGET_DATE
    _TARGET_DATE = date


def today_str() -> str:
    """产物文件名用的上海时区日期；补跑时返回注入的目标日期。"""
    return _TARGET_DATE or now_local().strftime("%Y-%m-%d")


def window_start(hours: int = WINDOW_HOURS) -> datetime:
    return now_local() - timedelta(hours=hours)


def in_window(dt: Optional[datetime], hours: int = WINDOW_HOURS) -> bool:
    """无发布时间的条目一律保留 —— 宁可多送候选，也不要漏掉正经新闻。"""
    if dt is None:
        return True
    return dt >= window_start(hours)


def parse_rss_date(entry) -> Optional[datetime]:
    """从 feedparser entry 解析发布时间，统一转成上海时区。"""
    for key in ("published_parsed", "updated_parsed"):
        st = getattr(entry, key, None) or (entry.get(key) if hasattr(entry, "get") else None)
        if st:
            # feedparser 的 *_parsed 已是 UTC struct_time
            return datetime.fromtimestamp(calendar.timegm(st), timezone.utc).astimezone(TZ)

    for key in ("published", "updated"):
        raw = entry.get(key) if hasattr(entry, "get") else None
        if raw:
            dt = parse_any(raw)
            if dt:
                return dt
    return None


def parse_any(raw: str) -> Optional[datetime]:
    """尽量宽松地解析各种日期字符串（含中文站点常见格式），失败返回 None。"""
    raw = (raw or "").strip()
    if not raw:
        return None

    tup = parsedate_tz(raw)
    if tup:
        offset = tup[9] or 0
        naive = datetime(*tup[:6])
        return (naive - timedelta(seconds=offset)).replace(tzinfo=timezone.utc).astimezone(TZ)

    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y年%m月%d日", "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return dt.astimezone(TZ) if dt.tzinfo else dt.replace(tzinfo=TZ)
    return None
