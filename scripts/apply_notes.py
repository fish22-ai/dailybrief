"""按 2026-11-18 用户反馈重写历史解读的 notes：
- 风格对齐 9 月：一条 note 只讲一个概念、大白话、40~100 字；
- 不写公式、不用反引号（prompt.zh.md 2026-11-18 版的要求）；
- 只替换 notes 字段，what/why/chain/insight_en 一律不动。

用法：python scripts/apply_notes.py --date 2026-10-28 --patch work/rewrite-2026-10-28.json
patch 格式：{"markets": [[note,...], ...], "policy": [...], "tech": [...]}
外层数组长度必须与该板块卡片数一致（不想改的板块给 null）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATS = ("markets", "policy", "tech")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--patch", required=True)
    args = ap.parse_args()

    data_path = ROOT / "data" / f"{args.date}.json"
    patch_path = Path(args.patch)
    data = json.loads(data_path.read_text(encoding="utf-8"))
    patch = json.loads(patch_path.read_text(encoding="utf-8"))

    for cat in CATS:
        new_notes = patch.get(cat)
        if not new_notes:
            continue
        cards = data.get(cat) or []
        if len(new_notes) != len(cards):
            print(f"{args.date} {cat}: patch {len(new_notes)} 条 != 卡片 {len(cards)} 条，跳过",
                  file=sys.stderr)
            return 1
        for card, notes in zip(cards, new_notes):
            ins = card.get("insight") or {}
            if len(notes) < 3:
                print(f"{args.date} {cat} {card['title'][:30]}: notes 少于 3 条", file=sys.stderr)
                return 1
            for n in notes:
                if "`" in n or "\\" in n:
                    print(f"{args.date} {cat}: 出现反引号/反斜杠: {n[:40]}", file=sys.stderr)
                    return 1
            ins["notes"] = notes
            card["insight"] = ins

    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    print(f"OK {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
