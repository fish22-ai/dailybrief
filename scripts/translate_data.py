"""给已有的期次补翻英文解读 —— 回填历史用。

英文版上线后，只有上线当天起的新一期才带 insight_en；更早的期次英文版会回落显示
中文（卡片上标一句「此期暂无英文」）。想把历史空白补上就跑这个脚本：

    python scripts/translate_data.py --date 2026-10-11   # 只补这一期
    python scripts/translate_data.py --recent 7          # 最近 7 期
    python scripts/translate_data.py --all               # 全部（一期一次调用，慢且烧钱）

规律：**每期一次 LLM 调用**，和当天主流程里那次翻译是同一个函数。
补完记得重建站点：python scripts/build_site.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models import CATEGORIES             # noqa: E402
from core.translate import translate_insights  # noqa: E402

DATA_DIR = ROOT / "data"


def all_dates() -> list[str]:
    return sorted((p.stem for p in DATA_DIR.glob("20*.json")), reverse=True)


def do_one(stem: str) -> int:
    path = DATA_DIR / f"{stem}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = {c: (payload.get(c) or []) for c in CATEGORIES}
    n = translate_insights(cards)                 # 就地写进同一批 dict
    if not n:
        print(f"{stem}: 没有译出任何条目（已跳过或失败）")
        return 0
    for c in CATEGORIES:
        payload[c] = cards[c]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"{stem}: 译出 {n} 条，已写回 {path.name}")
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="补翻已有期次的英文解读")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--date", help="只补这一天（YYYY-MM-DD）")
    g.add_argument("--recent", type=int, help="补最近 N 期")
    g.add_argument("--all", action="store_true", help="补全部期次")
    ap.add_argument("--exclude", default="",
                    help="逗号分隔的日期列表，从待补名单里剔除（另有任务在写这些文件时用）")
    args = ap.parse_args()

    dates = all_dates()
    if not dates:
        print("data/ 下没有日期 JSON", file=sys.stderr)
        return 1

    if args.exclude:
        skip = {d.strip() for d in args.exclude.split(",") if d.strip()}
        dates = [d for d in dates if d not in skip]

    if args.date:
        todo = [args.date] if args.date in dates else []
        if not todo:
            print(f"data/ 下没有 {args.date}.json", file=sys.stderr)
            return 1
    elif args.recent:
        todo = dates[:args.recent]
    else:
        todo = dates

    print(f"待补 {len(todo)} 期：{', '.join(todo)}")
    total = sum(do_one(d) for d in todo)
    print(f"\n合计译出 {total} 条。记得跑 python scripts/build_site.py 重建站点。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
