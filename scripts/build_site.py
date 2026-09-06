"""把 data/*.json 渲染成静态站点。纯标准库 + f-string 模板，不引前端框架。

布局：左侧新闻本体（标题/来源/时间），右侧 AI 解读四段
（发生了什么 / 市场为什么在意 / 金融传导 / 解析）。解读才是主体 ——
左栏只占三分之一宽，右栏是阅读重心。

传导链按 " → " 拆成一格一环渲染（用 "；" 分隔的支线另起一行），比一长串文本好读。
解析里的公式是**反引号包住的纯文本**，长公式单独成等宽块 —— 页面刻意不引 KaTeX，
零依赖、离线也读得出来。

2026-09-06 之前的归档是 what/why/chain/watch/term 五字段，没有 notes。
render_card() 对这种旧数据回落到旧标签渲染，重跑不会让历史页面掉内容。

用法：
    python scripts/build_site.py            # 渲染全部日期
    python scripts/build_site.py --latest   # 只渲染最新一天 + 首页
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config                               # noqa: E402
from core.models import CATEGORIES, CATEGORY_LABELS   # noqa: E402

DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"

# 全部可在 config.toml 的 [site] 改
SITE_NAME = config.get_str("site", "name", "每日金融 sense")
TAGLINE = config.get_str(
    "site", "tagline",
    "左栏是发生了什么，右栏是市场为什么在意、钱怎么一环一环流动、背后是哪个公式在动")
FOOTER = config.get_str("site", "footer", "每周更新 · 市场货币金融 / 政策地缘大事件")
ARCHIVE_DAYS = config.get_int("site", "archive_days", 60, lo=1, hi=3650)

CSS = """*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#f5f6f8;--card:#fff;--line:#e3e7ec;--ink:#12171f;--dim:#5b6472;
 --faint:#8b96a5;--accent:#0b5fbd;--chain:#0a7b57;--chainbg:#eef7f2;
 --chainline:#d3e7dd;--code:#f2f5f9;--warn:#8a6100}
body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
 background:var(--bg);color:var(--ink);padding:26px 18px 56px}
.wrap{max-width:1240px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:baseline;gap:12px}
h1{font-size:21px;letter-spacing:.3px}
.date{color:var(--dim);font-size:14px}
.badge{font-size:12px;padding:2px 9px;border-radius:10px;background:#e6ecf3;color:var(--dim)}
.badge.rules{background:#fff3cd;color:var(--warn)}
.tagline{color:var(--faint);font-size:13px;margin-top:6px}
.note{font-size:13px;color:var(--warn);background:#fff8e1;border:1px solid #ffe08a;
 border-radius:6px;padding:9px 13px;margin:14px 0}
h2{font-size:14px;margin:26px 0 12px;color:var(--dim);letter-spacing:1px;
 display:flex;align-items:center;gap:10px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
article{background:var(--card);border:1px solid var(--line);border-radius:10px;
 margin-bottom:12px;display:grid;grid-template-columns:minmax(240px,1fr) 2fr;overflow:hidden}
.news{padding:15px 17px;border-right:1px solid var(--line);background:#fafbfc}
.news a{color:var(--ink);text-decoration:none;font-weight:600;font-size:15px;
 display:block;margin-bottom:8px}
.news a:hover{color:var(--accent);text-decoration:underline}
.src{color:var(--faint);font-size:12px}
.raw{color:var(--dim);font-size:12.5px;margin-top:9px;padding-top:9px;
 border-top:1px dashed var(--line)}
.read{padding:15px 18px}
.seg{margin-bottom:13px}
.seg:last-child{margin-bottom:0}
.lb{font-size:11.5px;color:var(--faint);letter-spacing:.8px;margin-bottom:5px}
.tx{font-size:13.5px;color:#26303c;line-height:1.72}
.seg.what .tx{font-size:15px;font-weight:600;color:var(--ink);line-height:1.55}
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:5px 6px;margin-bottom:5px}
.flow:last-child{margin-bottom:0}
.hop{background:var(--chainbg);border:1px solid var(--chainline);color:var(--chain);
 border-radius:6px;padding:3px 8px;font-size:12.5px;line-height:1.5}
.arw{color:var(--chain);opacity:.55;font-size:12px}
.notes{border-top:1px dashed var(--line);padding-top:11px}
.notes ul{list-style:none}
.notes li{position:relative;padding-left:15px;margin-bottom:9px;font-size:13px;
 color:#26303c;line-height:1.72}
.notes li:last-child{margin-bottom:0}
.notes li::before{content:"*";position:absolute;left:2px;top:1px;color:var(--faint)}
.nk{color:var(--ink)}
.fml{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 background:var(--code);border-left:2px solid var(--accent);border-radius:4px;
 padding:7px 10px;margin:6px 0;font-size:12.5px;line-height:1.6;
 white-space:pre-wrap;overflow-x:auto}
code{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 background:var(--code);border-radius:3px;padding:1px 4px;font-size:12.5px}
.noread{padding:15px 18px;color:var(--faint);font-size:13px;display:flex;align-items:center}
.empty{color:var(--faint);font-size:13px;background:var(--card);border:1px solid var(--line);
 border-radius:10px;padding:14px 17px}
nav{margin-top:30px;background:var(--card);border:1px solid var(--line);
 border-radius:10px;padding:14px 18px}
nav h3{font-size:12px;color:var(--dim);margin-bottom:10px;letter-spacing:1px}
nav a{display:inline-block;margin:0 8px 8px 0;padding:4px 10px;background:#eef2f6;
 border-radius:6px;color:#3a4351;text-decoration:none;font-size:13px}
nav a:hover{background:#e0e7ef}
nav a.cur{background:var(--ink);color:#fff}
footer{margin-top:24px;color:var(--faint);font-size:12px;text-align:center;line-height:1.8}
/* 手机：单栏。这里不只是把两栏叠起来 —— 解读是阅读重心，字号要略微放大，
   传导链的环格要能整行排下，公式块允许横向滚动而不是硬换行把公式劈开。 */
@media(max-width:760px){
 body{padding:16px 12px 40px}
 .wrap{max-width:100%}
 article{grid-template-columns:1fr}
 .news{border-right:0;border-bottom:1px solid var(--line);padding:13px 14px}
 .read{padding:14px}
 .tx,.notes li{font-size:14.5px;line-height:1.78}
 .seg.what .tx{font-size:15.5px}
 .hop{font-size:13px;padding:4px 9px}
 /* 公式不换行会被劈开读不懂，让它自己横滚，并给个可滚提示 */
 .fml{font-size:12px;white-space:pre;overflow-x:auto;-webkit-overflow-scrolling:touch}
 h1{font-size:19px}
 nav a{font-size:13.5px;padding:6px 11px}   /* 触摸目标别太小 */
}
/* 窄屏（iPhone SE 一类）：传导链改成一环一行的竖排，横排挤成两三个字读不了 */
@media(max-width:430px){
 .flow{flex-direction:column;align-items:stretch;gap:0}
 .hop{border-radius:5px}
 .arw{display:block;text-align:center;line-height:1.1;padding:1px 0;
  transform:rotate(90deg)}     /* 竖排时箭头转成向下 */
}
@media(prefers-color-scheme:dark){
 :root{--bg:#12151a;--card:#1b1f26;--line:#2a3038;--ink:#e6e9ee;--dim:#a3adba;
  --faint:#7c8794;--accent:#5c9ded;--chain:#4ec49a;--chainbg:#1a2620;
  --chainline:#2c4239;--code:#232830;--warn:#e0b872}
 .news{background:#181c22}
 nav a{background:#252b34;color:#c3ccd6}
 nav a.cur{background:#4a8fd8;color:#fff}
 .badge{background:#252b34}
 .note{background:#2a2418;border-color:#5c4a1e;color:#e0b872}
 .badge.rules{background:#3a2f14;color:#e0b872}
 .tx,.notes li{color:#cfd6de}
}"""


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


# 传导链的环分隔符。校验层要求模型用 " → "，这里放宽到裸箭头，
# 少一个空格不该让整条链退化成一坨文字。
HOP_SEP = re.compile(r"\s*→\s*")
BRANCH_SEP = re.compile(r"[；;]\s*")
FORMULA_BLOCK_CHARS = 14     # 反引号里超过这么长就单独成等宽块，短的走行内 code
LEAD_MAX_CHARS = 26          # 概念名加粗只认开头这么长以内的 "："
# 公式独立成块后，原句的收尾标点会孤零零掉到下一行（"…公式 `P = …`。债券发行后…"），
# 块本身已经把句子断开了，直接吃掉这个标点。
ORPHAN_PUNCT = "。，、；：,.;: "


def render_chain(chain: str) -> str:
    """把 "A → B → C；D → E" 渲染成一环一格；"；" 后的支线另起一行。"""
    rows = []
    for branch in BRANCH_SEP.split(chain):
        hops = [h for h in (x.strip() for x in HOP_SEP.split(branch)) if h]
        if not hops:
            continue
        cells = f'<span class="arw">→</span>'.join(
            f'<span class="hop">{esc(h)}</span>' for h in hops)
        rows.append(f'<div class="flow">{cells}</div>')
    return "".join(rows) or f'<div class="tx">{esc(chain)}</div>'


def render_note(text: str) -> str:
    """渲染一条解析：反引号里的公式转等宽，开头的设问或概念名加粗。

    公式统一是纯文本（`P = C/(1+y)¹ + …`），长的单独成块、短的行内。
    加粗只是给眼睛一个抓手 —— 一条解析动辄两三行，没有抓手会糊成一片。
    """
    segs = text.split("`")
    if len(segs) % 2 == 0:      # 反引号没配对，最后一段按正文处理
        segs = segs[:-2] + ["`".join(segs[-2:])]

    out = []
    after_block = False
    for i, seg in enumerate(segs):
        if not seg.strip():
            continue
        if i % 2:               # 奇数段在反引号内 = 公式
            fml = seg.strip()
            block = len(fml) >= FORMULA_BLOCK_CHARS
            out.append(f'<div class="fml">{esc(fml)}</div>' if block
                       else f'<code>{esc(fml)}</code>')
            after_block = block
            continue
        if after_block:
            seg = seg.lstrip(ORPHAN_PUNCT)
            after_block = False
            if not seg:
                continue
        if not out:             # 第一段正文：把设问句或概念名加粗
            out.append(_bold_lead(seg))
        else:
            out.append(esc(seg))
    return f'<li>{"".join(out)}</li>'


def _bold_lead(text: str) -> str:
    """给一条解析的开头加粗：优先整句设问，其次 "概念名：" 前缀。"""
    stripped = text.lstrip()
    pad = esc(text[:len(text) - len(stripped)])

    if stripped[:3].lower() == "why":
        end = max(stripped.find("？"), stripped.find("?"))
        if 0 < end < 80:
            return (f'{pad}<b class="nk">{esc(stripped[:end + 1])}</b>'
                    f'{esc(stripped[end + 1:])}')

    colon = stripped.find("：")
    if 0 < colon <= LEAD_MAX_CHARS:
        return (f'{pad}<b class="nk">{esc(stripped[:colon + 1])}</b>'
                f'{esc(stripped[colon + 1:])}')
    return esc(text)


# 2026-09-06 起的字段。what 单独排在最上面（当小标题用），notes 单独排在最下面。
TEXT_SEGMENTS = (("what", "发生了什么"), ("why", "市场为什么在意"))
# 旧归档（五字段时代）没有 notes，回落渲染这两行，免得重跑丢内容
LEGACY_SEGMENTS = (("watch", "盯什么"), ("term", "概念"))


def render_card(card: dict) -> str:
    ins = card.get("insight") or {}

    meta = " · ".join(x for x in (esc(card.get("source")),
                                  esc(card.get("published"))) if x)
    raw = card.get("raw_summary") or ""
    raw_html = f'<div class="raw">{esc(raw[:150])}</div>' if raw else ""
    left = (f'<div class="news">'
            f'<a href="{esc(card.get("url"))}" target="_blank" '
            f'rel="noopener noreferrer">{esc(card.get("title"))}</a>'
            f'<div class="src">{meta}</div>{raw_html}</div>')

    if not ins:
        # 规则模式：明确说没有解读，不拿新闻摘要冒充
        return (f'<article>{left}'
                f'<div class="noread">今日无 AI 解读（规则模式）</div></article>')

    segs = [
        f'<section class="seg {key}"><div class="lb">{label}</div>'
        f'<div class="tx">{esc(ins.get(key))}</div></section>'
        for key, label in TEXT_SEGMENTS if ins.get(key)
    ]

    if ins.get("chain"):
        segs.append(f'<section class="seg chain"><div class="lb">金融传导</div>'
                    f'{render_chain(str(ins["chain"]))}</section>')

    notes = ins.get("notes") or []
    if isinstance(notes, str):
        notes = [ln for ln in notes.splitlines() if ln.strip()]
    if notes:
        lis = "".join(render_note(str(n)) for n in notes if str(n).strip())
        segs.append(f'<section class="seg notes"><div class="lb">解析</div>'
                    f'<ul>{lis}</ul></section>')
    else:
        segs += [
            f'<section class="seg"><div class="lb">{label}</div>'
            f'<div class="tx">{esc(ins.get(key))}</div></section>'
            for key, label in LEGACY_SEGMENTS if ins.get(key)
        ]

    return f'<article>{left}<div class="read">{"".join(segs)}</div></article>'



def render_page(payload: dict, dates: list[str], current: str) -> str:
    mode = payload.get("mode", "llm")
    badge = ('<span class="badge rules">规则模式</span>' if mode == "rules"
             else '<span class="badge">AI 解读</span>')
    note = ""
    if mode == "rules":
        reason = payload.get("fallback_reason") or "LLM 不可用"
        note = (f'<div class="note">今日为规则模式：只按来源权重与时间筛出了条目，'
                f'<b>没有 AI 解读</b>。原因：{esc(reason)}</div>')

    blocks = []
    for cat in CATEGORIES:
        cards = payload.get(cat) or []
        body = ("".join(render_card(c) for c in cards) if cards
                else '<div class="empty">今日该板块无入选条目</div>')
        blocks.append(f'<h2>{CATEGORY_LABELS[cat]}</h2>{body}')

    links = "".join(
        f'<a href="{d}.html"{" class=\"cur\"" if d == current else ""}>{d}</a>'
        for d in dates[:ARCHIVE_DAYS]
    )
    nav = f'<nav><h3>历史归档</h3>{links}</nav>' if links else ""

    tagline = f'<div class="tagline">{esc(TAGLINE)}</div>' if TAGLINE else ""

    # 手机上的注意点，都踩过：
    # - charset 必须在 <head> 最前面（早于 title），否则中文有几率乱码
    # - viewport 不写 maximum-scale/user-scalable，锁缩放会让公式没法放大看
    # - theme-color 让 Safari/Chrome 的地址栏跟着页面配色，加到主屏后不突兀
    # - apple-mobile-web-app-* 让 iOS「添加到主屏幕」后像个独立 App
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(SITE_NAME)} · {esc(payload.get("date"))}</title>
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f5f6f8" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#12151a" media="(prefers-color-scheme:dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{esc(SITE_NAME)}">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="manifest.webmanifest">
<style>{CSS}</style></head><body><div class="wrap">
<header><h1>{esc(SITE_NAME)}</h1>
<span class="date">{esc(payload.get("date"))}</span>{badge}</header>
{tagline}
{note}
{"".join(blocks)}
{nav}
<footer>{esc(FOOTER)}<br>
条目来自各源公开 RSS 与 API，解读由 Claude 生成，仅供学习参考，不构成投资建议</footer>
</div></body></html>"""


def manifest() -> str:
    """PWA manifest：iOS/Android「添加到主屏幕」后有名字和图标底色。

    刻意不引图标文件 —— 没有 icons 字段时两家系统都会自动截图当图标，
    省掉维护一堆尺寸 png。
    """
    return json.dumps({
        "name": SITE_NAME,
        "short_name": SITE_NAME[:12],
        "start_url": "./index.html",
        "display": "standalone",
        "background_color": "#f5f6f8",
        "theme_color": "#f5f6f8",
        "lang": "zh-CN",
    }, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", action="store_true", help="只渲染最新一天")
    args = ap.parse_args()

    files = sorted(
        (p for p in DATA_DIR.glob("20*.json") if p.name != "index.json"),
        key=lambda p: p.stem, reverse=True,
    )
    if not files:
        print("data/ 下没有日期 JSON，先跑 run_daily.py", file=sys.stderr)
        return 1

    dates = [p.stem for p in files]
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    for path in (files[:1] if args.latest else files):
        payload = json.loads(path.read_text(encoding="utf-8"))
        out = SITE_DIR / f"{path.stem}.html"
        out.write_text(render_page(payload, dates, path.stem), encoding="utf-8")
        print(f"渲染 {out.relative_to(ROOT)}")

    shutil.copyfile(SITE_DIR / f"{dates[0]}.html", SITE_DIR / "index.html")
    (SITE_DIR / "manifest.webmanifest").write_text(manifest(), encoding="utf-8")
    # GitHub Pages 默认走 Jekyll，会忽略下划线开头的文件并偶尔改写内容。
    # 我们只发静态 HTML，直接关掉。
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    print(f"首页指向 {dates[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
