# -*- coding: utf-8 -*-
"""批量重跑规则模式的历史期次 —— 2026-10-30 一次性补数用。

对指定日期逐天跑 run_daily --date，跑完校验 data/<date>.json 的 mode：
  - mode=llm          → 成功，下一天
  - mode=rules/其他   → 重试，最多 --tries 次（模型抽风返回非 JSON 时靠这个兜底）
全部结束后打印汇总，任何一天没成功都退出码 1，方便外层察觉。

用法：
    python scripts/backfill_batch.py --dates 2026-09-29,2026-10-19
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_DAILY = ROOT / "scripts" / "run_daily.py"


def run_one(date: str) -> tuple[bool, str]:
    """跑一天，返回 (是否 llm 模式, mode 或错误说明)。"""
    proc = subprocess.run(
        [sys.executable, str(RUN_DAILY), "--date", date],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    tail = (proc.stdout or "").strip().splitlines()
    tail = tail[-1] if tail else ""
    if proc.returncode != 0:
        err = (proc.stderr or "").strip().splitlines()
        return False, f"run_daily 退出码 {proc.returncode}: {err[-1] if err else ''}"
    path = ROOT / "data" / f"{date}.json"
    if not path.exists():
        return False, "run_daily 成功但没生成 data 文件"
    mode = json.loads(path.read_text(encoding="utf-8")).get("mode", "?")
    return mode == "llm", mode


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dates", required=True, help="逗号分隔的日期列表")
    ap.add_argument("--tries", type=int, default=2, help="每天最多重跑次数")
    args = ap.parse_args()

    dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    print(f"待补 {len(dates)} 天（每天最多试 {args.tries} 次）", flush=True)
    failed = []
    for date in dates:
        ok, info = False, "?"
        for i in range(1, args.tries + 1):
            print(f"[{date}] 第 {i}/{args.tries} 次尝试 ...", flush=True)
            ok, info = run_one(date)
            print(f"[{date}] -> {'OK' if ok else f'失败（{info}）'}", flush=True)
            if ok:
                break
        if not ok:
            failed.append(date)
    print("\n===== 汇总 =====")
    if failed:
        print("未成功:", ", ".join(failed))
        return 1
    print("全部成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
