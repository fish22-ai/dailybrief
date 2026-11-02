"""人工/Agent 代译通道：没有可用 LLM 时，由 Agent 补英文解读。

与 agent_brief.py 同属「没有 key 也能出简报」的兜底链路：
    python scripts/agent_translate.py --date 2026-10-29 --export work/tr-10-29.json
    # Agent 填好译文后
    python scripts/agent_translate.py --date 2026-10-29 --import work/tr-10-29.json

导出结构（key 与 core.translate 的扁平化一致）：
    {"markets-1": {"what": "...", "why": "...", "chain": "a → b → c", "notes": ["..."]}}

导入时逐条校验四段齐全才写入 insight_en，缺字段的整条跳过（宁可回落中文，
不渲染半截英文）。已译过的条目默认跳过，--force 可覆盖。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models import CATEGORIES, INSIGHT_TEXT_FIELDS    # noqa: E402
from core.timeutil import set_target_date                  # noqa: E402

DATA_DIR = ROOT / "data"
log = logging.getLogger("agent_translate")


def load(date: str) -> dict:
    p = DATA_DIR / f"{date}.json"
    if not p.exists():
        raise SystemExit(f"{p} 不存在")
    return json.loads(p.read_text(encoding="utf-8"))


def save(date: str, data: dict) -> None:
    p = DATA_DIR / f"{date}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def do_export(date: str, out: Path, force: bool) -> int:
    data = load(date)
    flat: dict[str, dict] = {}
    for cat in CATEGORIES:
        for i, card in enumerate(data.get(cat) or []):
            ins = card.get("insight") or {}
            if not ins:
                continue
            if card.get("insight_en") and not force:
                continue
            notes = ins.get("notes") or []
            if isinstance(notes, str):
                notes = [ln for ln in notes.splitlines() if ln.strip()]
            flat[f"{cat}-{i + 1}"] = {
                "title": card.get("title", ""),
                "what": str(ins.get("what") or ""),
                "why": str(ins.get("why") or ""),
                "chain": str(ins.get("chain") or ""),
                "notes": [str(n) for n in notes],
            }
    out.write_text(json.dumps(flat, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{date}: 导出 {len(flat)} 条待译 → {out}")
    return 0


def do_import(date: str, in_path: Path) -> int:
    data = load(date)
    tr = json.loads(in_path.read_text(encoding="utf-8"))
    done = skipped = 0
    for cat in CATEGORIES:
        for i, card in enumerate(data.get(cat) or []):
            key = f"{cat}-{i + 1}"
            got = tr.get(key)
            if not isinstance(got, dict):
                continue
            en: dict = {}
            for f in INSIGHT_TEXT_FIELDS:
                v = got.get(f)
                if isinstance(v, str) and v.strip():
                    en[f] = v.strip()
            notes = got.get("notes")
            if isinstance(notes, list):
                cleaned = [str(x).strip() for x in notes if str(x).strip()]
                if cleaned:
                    en["notes"] = cleaned
            if all(en.get(f) for f in INSIGHT_TEXT_FIELDS) and en.get("notes"):
                card["insight_en"] = en
                done += 1
            else:
                log.warning("%s 译文不全，跳过", key)
                skipped += 1
    save(date, data)
    print(f"{date}: 写入 {done} 条英文解读" + (f"，{skipped} 条不全跳过" if skipped else ""))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--force", action="store_true", help="导出时包含已译条目")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export")
    g.add_argument("--import", dest="imp")
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"--date {args.date!r} 不是 YYYY-MM-DD", file=sys.stderr)
        return 2
    set_target_date(args.date)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.export:
        return do_export(args.date, Path(args.export), args.force)
    return do_import(args.date, Path(args.imp))


if __name__ == "__main__":
    raise SystemExit(main())
