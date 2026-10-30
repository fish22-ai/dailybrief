# -*- coding: utf-8 -*-
"""临时诊断：用真实候选池的 prompt 调 gpt-6-astra，倒出原始响应结构。"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import config  # noqa: E402
from core.dedup import dedup  # noqa: E402
from core.http import PoliteClient  # noqa: E402
from core.scoring import score_all  # noqa: E402
from core.seen import SeenStore  # noqa: E402
from core.select import build_prompt  # noqa: E402
from core.timeutil import set_target_date  # noqa: E402
from fetchers.registry import fetch_all  # noqa: E402
from core.llm import make_client  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
set_target_date("2026-10-29")

from pathlib import Path
client = PoliteClient(Path(ROOT) / "cache" / "2026-10-29")
raw, _ = fetch_all(client)
scored, _ = score_all(raw)
deduped, _ = dedup(scored)
seen = SeenStore(Path(ROOT) / "data" / "seen.json")
deduped, _ = seen.filter_new(deduped)
pools = {}
for it in deduped:
    pools.setdefault(it.category, []).append(it)
prompt, index = build_prompt(pools)
print(f"prompt {len(prompt)} chars, 候选 {len(index)} 条")

api, err = make_client()
if api is None:
    raise SystemExit(f"客户端构建失败：{err}")

import time
for label, extra in [("带thinking参数", {"thinking": {"type": "disabled"}}),
                     ("不带thinking参数", {})]:
    t0 = time.time()
    r = api.messages.create(model="gpt-6-astra", max_tokens=16000,
                            messages=[{"role": "user", "content": prompt}], **extra)
    dt = time.time() - t0
    print(f"\n===== {label} =====")
    print(f"耗时 {dt:.0f}s | stop={r.stop_reason} | in={r.usage.input_tokens} out={r.usage.output_tokens}")
    print("blocks:", [(b.type, len(getattr(b, 'text', '') or getattr(b, 'thinking', '') or '')) for b in r.content])
    text = "".join(b.text for b in r.content if b.type == "text")
    print("text 前 600:", repr(text[:600]))
    print("text 末 300:", repr(text[-300:]))
    print("含'{'：", "{" in text, "| 含 '}'：", "}" in text)
    with open(os.path.join(ROOT, "preview", f"diag-{'th' if extra else 'noth'}.txt"), "w", encoding="utf-8") as f:
        f.write(text or "(空)")
