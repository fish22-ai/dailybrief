"""人工/Agent 代笔通道：没有可用 LLM 时，由 Agent 自己写解读。

背景（2026-11-02）：中转站 agentrouter.org 账户额度耗尽，每天 15:xx 的定时任务
在 select() 阶段撞 403，连续几天降级成规则模式（页面上「今日无 AI 解读」）。
换 key / 充值都要等，简报却天天要出。于是把「精选 + 写解读」这一步从模型手里
拿出来，做成可注入的环节：

    # 1) 导出候选池（走 cache，不重新抓网）
    python scripts/agent_brief.py --date 2026-11-02 --export candidates.json

    # 2) Agent 读 candidates.json，按同样的 schema 写 insights.json
    #    格式与 core.select 期望的模型输出完全一致，可带 *_en 英文字段
    python scripts/agent_brief.py --date 2026-11-02 --insights insights.json

第 2 步复用 core.select.validate()：编造 id、缺字段、chain 环数不够一律拒收，
代笔不等于放水。校验通过后按 LLM 模式落盘（mode=llm），页面渲染与平时一致。

insights.json 结构：
    {"markets": [{"id": 3, "what": "...", "why": "...", "chain": "a → b → c",
                  "notes": ["...", "..."],
                  "what_en": "...", "why_en": "...", "chain_en": "...",
                  "notes_en": ["...", "..."]}],
     "policy": [...], "tech": [...]}

英文四字段可省；省了就回落显示中文（.zhsrc）。
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
sys.path.insert(0, str(ROOT / "scripts"))

from core.dedup import dedup                                    # noqa: E402
from core.http import PoliteClient                              # noqa: E402
from core.models import CATEGORIES, INSIGHT_TEXT_FIELDS, Item    # noqa: E402
from core.scoring import score_all                              # noqa: E402
from core.seen import SeenStore                                  # noqa: E402
from core.select import build_prompt, validate                   # noqa: E402
from core.timeutil import set_target_date, today_str             # noqa: E402
from fetchers.registry import fetch_all                          # noqa: E402
from run_daily import CACHE_DIR, DATA_DIR, build_pools, write_outputs  # noqa: E402

log = logging.getLogger("agent_brief")


def collect_pools(date: str) -> tuple[dict[str, list[Item]], dict]:
    """按指定日期重建候选池（尽量命中 cache）。返回 (pools, stats)。"""
    client = PoliteClient(CACHE_DIR / date)
    raw, stats = fetch_all(client)
    log.info("抓取合计 %d 条", len(raw))
    scored, off_topic = score_all(raw)
    log.info("相关性闸门拦下 %d 条，剩余 %d 条", off_topic, len(scored))
    deduped, removed = dedup(scored)
    log.info("去重移除 %d 条，剩余 %d 条", removed, len(deduped))

    seen = SeenStore(DATA_DIR / "seen.json")
    deduped, stale = seen.filter_new(deduped)
    log.info("排除往日已上站 %d 条，剩余 %d 条", stale, len(deduped))
    if not deduped:
        raise SystemExit("候选池为空（cache 缺失或 RSS 已过期），这天补不了")

    pools = build_pools(deduped)
    for cat in CATEGORIES:
        log.info("候选池 %-12s %d 条", cat, len(pools[cat]))
    return pools, stats


def do_export(date: str, out_path: Path) -> int:
    pools, _stats = collect_pools(date)
    prompt, index = build_prompt(pools)

    rows = []
    for pid, it in sorted(index.items()):
        rows.append({
            "id": pid,
            "category": it.category,
            "source": it.source,
            "published": it.published.strftime("%Y-%m-%d %H:%M") if it.published else "",
            "title": it.title,
            "summary": it.summary,
            "url": it.url,
        })

    payload = {"date": date, "candidates": rows, "prompt": prompt}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导出 {len(rows)} 条候选 → {out_path}")
    return 0


def _split_en(pick: dict) -> tuple[dict, dict]:
    """把一条 pick 拆成 (中文字段, 英文字段)。"""
    zh = {k: v for k, v in pick.items() if not k.endswith("_en")}
    en: dict = {}
    for f in INSIGHT_TEXT_FIELDS:
        v = pick.get(f + "_en")
        if isinstance(v, str) and v.strip():
            en[f] = v.strip()
    notes_en = pick.get("notes_en")
    if isinstance(notes_en, list):
        cleaned = [str(x).strip() for x in notes_en if str(x).strip()]
        if cleaned:
            en["notes"] = cleaned
    complete = all(en.get(f) for f in INSIGHT_TEXT_FIELDS) and bool(en.get("notes"))
    return zh, (en if complete else {})


def do_insights(date: str, in_path: Path) -> int:
    pools, stats = collect_pools(date)
    _prompt, index = build_prompt(pools)

    raw = json.loads(in_path.read_text(encoding="utf-8"))
    zh_only = {cat: [] for cat in CATEGORIES}
    en_map: dict[tuple[str, int], dict] = {}
    for cat in CATEGORIES:
        for pick in raw.get(cat) or []:
            zh, en = _split_en(pick)
            zh_only[cat].append(zh)
            if en:
                en_map[(cat, int(zh["id"]))] = en

    # 复用模型输出的校验：编造 id / 缺字段 / chain 环数不够 / LaTeX 全部拒收
    cards = validate(zh_only, index)

    # 校验后按 cat+顺序 回填英文（validate 会把串台的条目归位，顺序会变，
    # 所以用 id 反查而不是按下标）
    by_id = {int(p["id"]): p for cat in CATEGORIES for p in (raw.get(cat) or [])}
    filled = 0
    for cat in CATEGORIES:
        for card in cards[cat]:
            # 卡片没带 id，用 url 反查候选池再取 id
            pid = next((i for i, it in index.items() if it.url == card["url"]), None)
            if pid is not None and pid in by_id:
                _zh, en = _split_en(by_id[pid])
                if en:
                    card["insight_en"] = en
                    filled += 1

    total = sum(len(cards[c]) for c in CATEGORIES)
    out = write_outputs(cards, "llm", "", stats)
    log.info("英文解读 %d/%d 条就绪", filled, total)

    seen = SeenStore(DATA_DIR / "seen.json")
    published = {c["url"] for cat in CATEGORIES for c in cards[cat]}
    seen.mark([it for it in index.values() if it.url in published])
    seen.save()

    print(f"写出 {out}（mode=llm，中文 {total} 条，英文 {filled} 条）")
    for cat in CATEGORIES:
        print(f"  {cat:8} {len(cards[cat])} 条")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="目标日期 YYYY-MM-DD")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", help="导出候选池 JSON 供代笔")
    g.add_argument("--insights", help="读入解读 JSON 并落盘")
    args = ap.parse_args()

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"--date {args.date!r} 不是 YYYY-MM-DD", file=sys.stderr)
        return 2
    set_target_date(args.date)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    if args.export:
        return do_export(args.date, Path(args.export))
    return do_insights(args.date, Path(args.insights))


if __name__ == "__main__":
    raise SystemExit(main())
