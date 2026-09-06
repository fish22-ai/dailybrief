"""每日主流程：抓取 → 去重 → 打分 → 一次 LLM 精选（或规则降级）→ 写 JSON。

用法：
    python scripts/run_daily.py              # 完整流程
    python scripts/run_daily.py --dry-run    # 不调 LLM，零成本验证抓取质量
    python scripts/run_daily.py --only ai    # 只跑某个板块
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config                                         # noqa: E402
from core.dedup import dedup                                    # noqa: E402
from core.http import PoliteClient                              # noqa: E402
from core.models import CATEGORIES, CATEGORY_LABELS, Item       # noqa: E402
from core.scoring import score_all                              # noqa: E402
from core.seen import SeenStore                                  # noqa: E402
from core.select import build_prompt, select                     # noqa: E402
from core.timeutil import today_str                              # noqa: E402
from fetchers.registry import SOURCES, fetch_all                 # noqa: E402

DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
CACHE_DIR = ROOT / "cache"

# 两个都能在 config.toml 的 [output] 里改，见那里的注释
POOL_SIZE = config.get_int("output", "pool_size", 18, lo=3, hi=60)
PER_SOURCE_CAP = config.get_int("output", "candidates_per_source", 4, lo=1, hi=20)

log = logging.getLogger("run_daily")


def setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.FileHandler(LOG_DIR / f"{today_str()}.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )


def build_pools(items: list[Item]) -> dict[str, list[Item]]:
    """按板块分组，各自按分数排序后取前 POOL_SIZE 条。

    这里必须重新按 score 排 —— dedup() 返回的顺序是按"保留哪条"的优先级
    （来源权重优先）排的，不是最终展示顺序。规则模式直接取前 5 条，顺序错了
    就会把权重高但过时的条目顶到前面。
    """
    pools: dict[str, list[Item]] = {c: [] for c in CATEGORIES}
    for it in items:
        if it.category in pools:
            pools[it.category].append(it)

    out: dict[str, list[Item]] = {}
    for cat, group in pools.items():
        ranked = sorted(group, key=lambda i: i.score, reverse=True)
        # 单源限额：BBC 一家能发几十条，HN 一天上百条，不限的话官方一手源
        # （Fed / 证监会 / 各家 AI 官博）会被整版挤出候选池。先按限额取一轮，
        # 不够 POOL_SIZE 再用剩下的高分条目补齐。
        picked, spill, used = [], [], {}
        for it in ranked:
            if used.get(it.source, 0) < PER_SOURCE_CAP:
                picked.append(it)
                used[it.source] = used.get(it.source, 0) + 1
            else:
                spill.append(it)
        out[cat] = (picked + spill)[:POOL_SIZE]
    return out


def write_outputs(cards: dict[str, list[dict]], mode: str, note: str, stats: dict) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    date = today_str()

    payload = {"date": date, "mode": mode, **cards}
    if note:
        payload["fallback_reason"] = note
    payload["source_stats"] = stats

    out = DATA_DIR / f"{date}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 归档索引：倒序日期列表，静态站靠它渲染导航，不让前端猜文件存不存在
    dates = sorted(
        (p.stem for p in DATA_DIR.glob("20*.json") if p.name != "index.json"),
        reverse=True,
    )
    (DATA_DIR / "index.json").write_text(
        json.dumps({"dates": dates, "latest": dates[0] if dates else None},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="不调用 LLM，只验证抓取与打分")
    ap.add_argument("--only", help="只跑指定板块（finance/ai/gov_military）或源 key，逗号分隔")
    ap.add_argument("--no-cache", action="store_true", help="忽略当日缓存，强制重新抓取")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    setup_logging(args.verbose)

    only_keys = None
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        only_keys = {
            s.key for s in SOURCES if s.key in wanted or s.category in wanted
        }
        if not only_keys:
            log.error("--only %s 没有匹配到任何源", args.only)
            return 2

    client = PoliteClient(CACHE_DIR / today_str())
    if args.no_cache:
        for f in (CACHE_DIR / today_str()).glob("*.bin"):
            f.unlink()

    raw, stats = fetch_all(client, only=only_keys)
    log.info("抓取合计 %d 条", len(raw))
    if not raw:
        log.error("所有源都没拿到数据，终止（不覆盖已有产物）")
        return 1

    scored, off_topic = score_all(raw)
    log.info("相关性闸门拦下 %d 条软新闻，剩余 %d 条", off_topic, len(scored))
    if not scored:
        log.error("所有条目都被相关性闸门拦下，终止（不覆盖已有产物）")
        return 1

    deduped, removed = dedup(scored)
    log.info("去重移除 %d 条，剩余 %d 条", removed, len(deduped))

    seen = SeenStore(DATA_DIR / "seen.json")
    deduped, stale = seen.filter_new(deduped)
    log.info("排除往日已上站 %d 条，剩余 %d 条", stale, len(deduped))
    if not deduped:
        log.error("窗口内全是往日已上站的条目，终止（不覆盖已有产物）")
        return 1

    pools = build_pools(deduped)
    for cat in CATEGORIES:
        log.info("候选池 %-12s %d 条", cat, len(pools[cat]))

    if args.dry_run:
        print("\n===== DRY RUN（未调用 LLM）=====")
        for cat in CATEGORIES:
            print(f"\n## {cat}  {CATEGORY_LABELS[cat]}")
            for i, it in enumerate(pools[cat][:10], 1):
                date = it.published.strftime("%m-%d %H:%M") if it.published else "  --  "
                rel = it.score_detail.get("relevance", 0)
                print(f"{i:2}. [{it.score:5.2f} rel={rel:4.2f}] {date} "
                      f"{it.source:16} {it.title[:52]}")
        print("\n候选池规模：" + ", ".join(
            f"{c}={len(pools[c])}" for c in CATEGORIES))
        prompt, index = build_prompt(pools)
        print(f"prompt 字符数 {len(prompt)}（粗估 {len(prompt) // 2.5:.0f} 输入 token）"
              f"，候选 {len(index)} 条")
        return 0

    cards, mode, note = select(pools)
    out = write_outputs(cards, mode, note, stats)

    # 只把真正上站的条目记进 seen 档案，落选的候选明天还有机会
    published_urls = {c["url"] for cat in CATEGORIES for c in cards[cat]}
    seen.mark([it for it in deduped if it.url in published_urls])
    seen.save()

    log.info("写出 %s（mode=%s%s）", out, mode, f", 原因: {note}" if note else "")
    for cat in CATEGORIES:
        log.info("产出 %-12s %d 条", cat, len(cards[cat]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
