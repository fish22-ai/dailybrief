"""把中文解读译成英文 —— 站点英文版的内容来源。

站点的主体是「英文新闻原文 + 中文 AI 解读」。2026-10-12 起顶栏加了中/EN 切换：
英文版把解读换成英文，中文原文折叠保留（渲染见 build_site.py 的 .enread / .zhsrc）。

译文落进**数据文件**（每张卡片的 insight_en 字段），不在渲染时调模型 —— 静态站
保持零运行时依赖，重渲历史页面也不会重新烧 token。

**单独一次调用，不复用精选那次**：精选的输出已经贴着 max_tokens 上限（6 条约
16000），再往里塞翻译必然截断。翻译是纯搬运，token 量只有原文的一倍多。

翻译是**尽力而为**：没 key / 网络挂 / JSON 不合规都只记一条 warning，这一期留成
纯中文，英文版回落显示中文（`.zhsrc[data-forceopen]`），**不影响当天出简报**。
"""
from __future__ import annotations

import json
import logging
import re

from . import config
from .models import CATEGORIES, INSIGHT_TEXT_FIELDS
from .select import DISABLE_THINKING, MODEL, make_client

log = logging.getLogger(__name__)

MAX_TOKENS = config.get_int("llm", "translate_max_tokens", 8000, lo=1000, hi=32000)

PROMPT = """下面是一份中文金融解读，请**逐条翻译成英文**。只做翻译：不改写、不增删
信息、不加解释、不合并条目。

硬性要求：
1. 输出 JSON 对象，key 与输入完全一致，每个 key 下的字段也完全一致。
2. what / why 译成通顺的英文。chain 是因果链条，**环与环之间的分隔符必须原样保留**
   —— 用 " → "（空格 + 箭头 + 空格）连接；并列的多条链之间用 "；" 分隔。
   只把每一环里的中文换成英文，环数、顺序、分隔符都不许变。
3. notes 是字符串数组：逐条翻译，条数与顺序不变。
4. 反引号包住的内容（如 `P = C/(1+y)`）是公式或代码，**原样抄下来** —— 不翻译、
   不加空格、不改标点。
5. 用标准金融英语（传导 chain → transmission；外汇储备 → FX reserves；
   折现率 → discount rate；贸易逆差 → trade deficit）。
6. 只输出 JSON，不要 markdown 代码围栏，不要任何说明文字。

输入：
{payload}
"""


def _flat(cards: dict[str, list[dict]]) -> tuple[dict[str, dict], dict[str, tuple[str, int]]]:
    """把各板块卡片的解读摊平成 {"markets-1": {...}}，并记住它来自哪张卡片。"""
    flat: dict[str, dict] = {}
    where: dict[str, tuple[str, int]] = {}
    for cat in CATEGORIES:
        for i, card in enumerate(cards.get(cat) or []):
            ins = card.get("insight") or {}
            if not ins:
                continue                      # 规则模式没有解读，没什么可译
            notes = ins.get("notes") or []
            if isinstance(notes, str):
                notes = [ln for ln in notes.splitlines() if ln.strip()]
            key = f"{cat}-{i + 1}"
            flat[key] = {
                "what": str(ins.get("what") or ""),
                "why": str(ins.get("why") or ""),
                "chain": str(ins.get("chain") or ""),
                "notes": [str(n) for n in notes],
            }
            where[key] = (cat, i)
    return flat, where


def _extract_json(text: str) -> dict:
    """从模型回复里取出 JSON 对象。容忍 ```json 围栏和前后废话。"""
    t = text.strip()
    t = re.sub(r"\A```[A-Za-z]*\s*", "", t)
    t = re.sub(r"\s*```\Z", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("回复里找不到 JSON 对象")
    return json.loads(t[start:end + 1])


def translate_insights(cards: dict[str, list[dict]]) -> int:
    """把 cards 里每条解读译成英文，写进对应卡片的 insight_en。返回译出的条数。

    失败不抛异常（除非调用方传了坏结构）：记 warning 后返回已译出的条数，
    让当天的主流程照常落盘。字段不全的条目**不写** insight_en —— 宁可整条回落
    中文，也不要渲染出一个半截的英文版。
    """
    flat, where = _flat(cards)
    if not flat:
        return 0

    client, why = make_client()
    if client is None:
        log.warning("英文翻译跳过：%s", why)
        return 0

    prompt = PROMPT.format(payload=json.dumps(flat, ensure_ascii=False, indent=1))
    try:
        create_kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS,
                             messages=[{"role": "user", "content": prompt}])
        if DISABLE_THINKING:
            create_kwargs["thinking"] = {"type": "disabled"}
        resp = client.messages.create(**create_kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        data = _extract_json(text)
    except Exception as exc:                  # noqa: BLE001 —— 翻译失败不能拖垮主流程
        log.warning("英文翻译失败（本期英文版回落显示中文）：%s: %s",
                    type(exc).__name__, exc)
        return 0

    if getattr(resp, "stop_reason", None) == "max_tokens":
        log.warning("翻译输出被 max_tokens=%d 截断，后面几条会缺字段", MAX_TOKENS)

    done = 0
    for key, (cat, i) in where.items():
        got = data.get(key)
        if not isinstance(got, dict):
            log.warning("第 %s 条没有译文，跳过", key)
            continue
        en: dict = {}
        for f in INSIGHT_TEXT_FIELDS:
            v = got.get(f)
            if isinstance(v, str) and v.strip():
                en[f] = v.strip()
        notes = got.get("notes")
        if isinstance(notes, str):
            notes = [ln for ln in notes.splitlines() if ln.strip()]
        if isinstance(notes, list):
            en["notes"] = [str(x) for x in notes if str(x).strip()]
        if all(en.get(f) for f in INSIGHT_TEXT_FIELDS) and en.get("notes"):
            cards[cat][i]["insight_en"] = en
            done += 1
        else:
            log.warning("第 %s 条译文不全（缺 what/why/chain/notes），跳过", key)
    return done
