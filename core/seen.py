"""已出现过的条目归档。

官方一手源用 7 天宽窗口（见 timeutil.SLOW_WINDOW_HOURS），否则它们几天才发一条、
永远进不了 36 小时窗口。代价是同一条会连着几天出现，所以这里记下已经上过站的
归一化 URL，之后的日子直接排除。

只存 URL 哈希与日期，不存标题内容，文件不会膨胀。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .models import Item
from .timeutil import today_str

log = logging.getLogger(__name__)

RETENTION_DAYS = 30      # 只保留近 30 天，早于此的记录清掉


def _key(norm_url: str) -> str:
    return hashlib.sha1(norm_url.encode("utf-8")).hexdigest()[:16]


class SeenStore:
    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, str] = {}          # url_hash -> 首次出现日期
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("seen.json 读取失败，按空档案处理：%s", exc)

    def filter_new(self, items: list[Item]) -> tuple[list[Item], int]:
        """剔除**往日**已经上过站的条目。items 必须已经填好 norm_url。

        只排除早于今天的记录：同一天重跑（抓取失败后重试、手动 re-run）应该得到
        同样的结果，而不是把上一轮自己刚发布的内容当成"已上站"排除掉。
        """
        today = today_str()
        fresh = [
            it for it in items
            if self._data.get(_key(it.norm_url), today) >= today
        ]
        return fresh, len(items) - len(fresh)

    def mark(self, items: list[Item]) -> None:
        """把今天真正产出的条目记进档案。只在非 dry-run 时调用。"""
        date = today_str()
        for it in items:
            self._data.setdefault(_key(it.norm_url), date)

    def save(self) -> None:
        keep = set(sorted(set(self._data.values()), reverse=True)[:RETENTION_DAYS])
        pruned = {k: v for k, v in self._data.items() if v in keep}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(pruned, ensure_ascii=False, indent=0), encoding="utf-8"
        )
