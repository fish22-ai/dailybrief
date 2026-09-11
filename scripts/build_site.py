"""把 data/*.json 渲染成静态站点。纯标准库 + f-string 模板，不引前端框架。

布局：一条事件一张**独立抽卡卡片**（大圆角 + 阴影 + 悬停微抬）。所有卡片收进一个
**横向滑卡卡组**（.deck）：一次只看一张，桌面用 ← → 键或按钮翻，手机左右滑动翻。
卡内左侧是新闻本体（标题/来源/时间），右侧是 AI 解读四段（发生了什么 / 市场为什么在意 /
金融传导 / 解析）。解读才是主体 —— 宽屏左栏只占三分之一，右栏是阅读重心；窄屏叠成单栏。

**唯一的 JS 是滑卡导航**（约 40 行，放页面底部 <script>）：解析的展开/收起仍用原生
<details>/<summary>，零脚本、键盘可达、禁用 JS 也能展开。滑卡本身靠原生横向滚动 +
scroll-snap，仅按钮/方向键/计数器需要少量脚本；即便脚本没跑，页面仍能手动左右滑。

传导链按 " → " 拆成一格一环渲染（用 "；" 分隔的支线另起一行），比一长串文本好读。
解析里的公式是**反引号包住的纯文本**，长公式单独成等宽块 —— 页面刻意不引
KaTeX/MathJax，零依赖、离线也读得出来。形如 `资本充足率 = 合格资本 / RWA` 的
简单两段分式会用 CSS 摞成上下分子分母（`_split_fraction()`），不靠任何公式库。

2026-09-06 之前的归档是 what/why/chain/watch/term 五字段，没有 notes。
render_card() 对这种旧数据回落到旧标签渲染，重跑不会让历史页面掉内容。

三个后续扩展位已在代码里标注，搜 `[预留·` 可以直接找到：
皮肤主题（CSS 变量层）、左右滑动抽卡（.deck 轨道）、行测解读模式（_collapsible()）。

用法：
    python scripts/build_site.py            # 渲染全部日期
    python scripts/build_site.py --latest   # 只渲染最新一天 + 首页
"""
from __future__ import annotations

import argparse
import hashlib
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

# 图标/清单这几个文件跟日期无关，改一次就得让**已经装在桌面上的旧副本**失效：
# 缓存名只按构建日期走的话，重画图标而当天日期没变（很常见）就永远换不掉。
# 这里按文件内容算一枚指纹，挂到 manifest 的图标 URL 上（?v=…）—— Chrome 看到
# 图标 URL 变了才会重新下载并更新已安装 App 的图标；service worker 的缓存名
# 也带上它，否则离线壳还是从旧缓存里拿图标。
ICON_FILES = ("icon-192.png", "icon-512.png", "apple-touch-icon.png")
_rev_cache: dict[str, str] = {}


def asset_rev() -> str:
    if "v" not in _rev_cache:
        h = hashlib.sha1()
        for name in ICON_FILES:
            path = SITE_DIR / name
            h.update(path.read_bytes() if path.exists() else b"")
        _rev_cache["v"] = h.hexdigest()[:10]
    return _rev_cache["v"]

# 全部可在 config.toml 的 [site] 改
SITE_NAME = config.get_str("site", "name", "每日金融 sense")
# 顶部品牌名：配置里是「知势|Daily Brief」，展示时去竖线、空格拼成「知势 DailyBrief」
BRAND = SITE_NAME.split("|")[0].strip() + " DailyBrief"
TAGLINE = config.get_str(
    "site", "tagline",
    "左栏是发生了什么，右栏是市场为什么在意、钱怎么一环一环流动、背后是哪个公式在动")
FOOTER = config.get_str("site", "footer", "每周更新 · 市场货币金融 / 政策地缘大事件")
ARCHIVE_DAYS = config.get_int("site", "archive_days", 60, lo=1, hi=3650)

CSS = """*{box-sizing:border-box;margin:0;padding:0}
/* ── 主题变量层 ──
   [预留·皮肤主题] 颜色、圆角、阴影全部收在这一处，没有一个写死在下面的规则里。
   将来加皮肤不用动 HTML、也不用动渲染代码，只追加一组覆盖即可：
     html[data-skin="ink"]{--bg:#0f1115;--card:#171a20;--accent:#c9a227;--card-r:8px}
     html[data-skin="paper"]{--bg:#f3efe6;--card:#fffdf7;--accent:#8a5a2b}
   然后在 <html> 上加 data-skin="ink"。若要做切换器，也只需一个按钮写
   localStorage + setAttribute，本文件的其余部分完全不受影响。 */
/* [2026-09-11 改版] 版式沿用简单四段（发生了什么/市场为什么在意/金融传导/解析），
   只换了配色与品牌区：颜色取自 dailybrief-ui 参考站的暖色调，
   顶部 brand 区是「勢」logo + 知势 DailyBrief。 */
:root{--bg:#faf8f3;--card:#fff;--line:#e8e5db;--ink:#191918;--dim:#6b6a62;
 --faint:#9a988f;--accent:#b44322;--chain:#1c4e4f;--chainbg:#eef4f1;
 --chainline:#d6e3da;--code:#f2f1eb;--warn:#8a6100;
 --pill:#f3f0e8;--pillline:#e2dcc9;--card-r:14px;
 --serif:Georgia,"Songti SC","Noto Serif CJK SC","SimSun",serif;
 --card-sd:0 1px 2px rgba(30,27,20,.06),0 10px 26px -12px rgba(30,27,20,.14);
 --card-sd-hi:0 2px 6px rgba(30,27,20,.08),0 18px 38px -16px rgba(30,27,20,.22)}
body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
 background:var(--bg);color:var(--ink);padding:26px 18px 56px}
.wrap{max-width:1240px;margin:0 auto}
/* 顶部品牌区：势 logo（深色圆角块）+ 知势 DailyBrief，照 dailybrief-ui 的 masthead。 */
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;
 padding-bottom:13px;border-bottom:1px solid var(--line)}
header .logo{width:38px;height:38px;border-radius:9px;background:var(--ink);color:var(--bg);
 display:inline-flex;align-items:center;justify-content:center;font-family:var(--serif);
 font-size:19px;font-weight:700;flex:none}
.hd{flex:1;min-width:0}
h1{font-family:var(--serif);font-weight:700;font-size:19px;letter-spacing:.2px;
 display:flex;align-items:baseline;gap:6px;line-height:1.2}
h1 .en{font-size:12px;font-weight:500;color:var(--faint);letter-spacing:0}
.date{color:var(--dim);font-size:14px}
.badge{font-size:12px;padding:2px 9px;border-radius:10px;background:#efece4;color:var(--dim)}
.badge.rules{background:#fff3cd;color:var(--warn)}
/* 副标题跟在 logo 那块品牌区里（h1 下面一行），跟 9.11 复杂版一致 */
header .sub{font-size:12px;color:var(--faint);margin-top:2px}
.note{font-size:13px;color:var(--warn);background:#fdf3df;border:1px solid #ecd9a8;
 border-radius:6px;padding:9px 13px;margin:14px 0}
h2{font-size:14px;margin:26px 0 12px;color:var(--dim);letter-spacing:1px;
 display:flex;align-items:center;gap:10px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
/* ── 抽卡卡片 ──
   一条事件 = 一张独立卡片：大圆角 + 双层阴影 + 悬停微抬，像一张能抽出来的卡。
   宽屏保持"左新闻 1/3、右解读 2/3"，窄屏叠成单栏（见下面的媒体查询）。
   [预留·左右滑动抽卡] .cards 容器内就是 .deck（横向滚动轨道），默认已经是
   滑动卡组。若只想要原来的竖排平铺，改一行：.deck-track{display:block}
   并把 .deck-slide{flex:0 0 100%} 删掉即可。两种形态共用同一份卡片样式。 */
.cards{display:block}
article{background:var(--card);border:1px solid var(--line);border-radius:var(--card-r);
 margin-bottom:16px;display:grid;grid-template-columns:minmax(240px,1fr) 2fr;
 overflow:hidden;box-shadow:var(--card-sd);
 transition:transform .18s ease,box-shadow .18s ease}
article:hover{transform:translateY(-2px);box-shadow:var(--card-sd-hi)}
.news{padding:16px 18px;border-right:1px solid var(--line);background:#faf9f4}
.news a{color:var(--ink);text-decoration:none;font-weight:600;font-size:15px;
 display:block;margin-bottom:8px}
.news a:hover{color:var(--accent);text-decoration:underline}
.src{color:var(--faint);font-size:12px}
.raw{color:var(--dim);font-size:12.5px;margin-top:9px;padding-top:9px;
 border-top:1px dashed var(--line)}
/* ── 英文原文折叠（.orig）──
   英文源的卡片手机端默认只显示中文解读：标题和原文摘要都收进这个 details，
   点「查看英文原文」才展开。桌面端不折（屏幕够宽，英文直接摊开更好读），
   靠 author CSS 覆盖浏览器给 details 的 display:none —— 作者样式优先于 UA 样式。 */
.orig{margin-bottom:8px}
.orig>summary{list-style:none;cursor:pointer;user-select:none;display:inline-flex;
 align-items:center;gap:6px;font-size:12.5px;color:var(--dim);background:var(--code);
 border:1px solid var(--line);border-radius:999px;padding:5px 13px}
.orig>summary::-webkit-details-marker{display:none}
.orig>summary::after{content:"\\25be";font-size:10px;opacity:.7;
 transition:transform .15s ease}
.orig[open]>summary::after{transform:rotate(180deg)}
.orig>summary:hover{color:var(--accent);border-color:var(--pillline)}
.orig>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.orig[open]>summary{margin-bottom:9px}
.orig>a{margin-bottom:8px}
.orig>.raw{margin-top:0;padding-top:0;border-top:0}
@media(min-width:761px){
 .orig{margin-bottom:0}
 .orig>summary{display:none}
 .orig>*:not(summary){display:block;content-visibility:visible}
}
/* 卡片顶端一条色带 + 板块标签。因为滑卡把所有板块并成一条，卡片本身要自报板块。 */
article{position:relative}
article[data-cat]{overflow:hidden}
article[data-cat]::before{content:"";position:absolute;top:0;left:0;right:0;height:3px}
article[data-cat="markets"]::before{background:var(--accent)}
article[data-cat="policy"]::before{background:#a8690f}
/* 科技板块：冷色系里挑了个沉一点的蓝灰，和赤陶(市场)/赭黄(政策)拉开，又不跳出暖调底子。 */
article[data-cat="tech"]::before{background:#42557d}
.catpill{display:inline-block;font-size:11px;letter-spacing:.4px;
 padding:2px 9px;border-radius:999px;margin-bottom:8px}
.catpill[data-cat="markets"]{background:#fbece7;color:#b44322}
.catpill[data-cat="policy"]{background:#f3ead3;color:#8a620f}
.catpill[data-cat="tech"]{background:#e7eaf2;color:#3f527a}
@media(prefers-color-scheme:dark){
 .catpill[data-cat="markets"]{background:#3a241c;color:#d98a63}
 .catpill[data-cat="policy"]{background:#2f2a16;color:#e0b872}
 .catpill[data-cat="tech"]{background:#232a3c;color:#93a5cf}
}

/* ── 滑卡卡组 ──
   一次只显示一张卡片，桌面用 ←  → 方向键 / 按钮翻，手机左右滑动翻。
   轨道本身可横向滚动（touch 天然支持），按钮和方向键也只是把 scrollLeft 挪一格；
   滚动结束时按 scroll-snap 对齐，所以键盘和手指落到同一套位置逻辑上。 */
.deck{position:relative}
/* align-items:flex-start 让每张 slide 保持自身高度 —— 卡片高低不齐时，
   脚本才能量出"当前这张"和"最高那张"的差，把翻页键上提到当前卡正下方。 */
.deck-track{display:flex;align-items:flex-start;overflow-x:auto;
 overscroll-behavior-x:contain;
 scroll-snap-type:x mandatory;gap:14px;padding:2px 2px 12px;
 scrollbar-width:none;-webkit-overflow-scrolling:touch}
.deck-track::-webkit-scrollbar{display:none}
.deck-slide{flex:0 0 100%;scroll-snap-align:center}
.deck-slide article{margin-bottom:0}
.deck-ctl{display:flex;align-items:center;justify-content:center;gap:14px;
 margin-top:4px;min-height:34px}
.deck-btn{appearance:none;border:1px solid var(--line);background:var(--card);
 color:var(--dim);border-radius:999px;width:34px;height:34px;cursor:pointer;
 font:16px/1 monospace;display:inline-flex;align-items:center;justify-content:center;
 box-shadow:var(--card-sd);transition:opacity .15s ease,color .15s ease,transform .15s ease}
.deck-btn:hover:not([disabled]){color:var(--accent);transform:translateY(-1px)}
.deck-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.deck-btn[disabled]{opacity:.35;cursor:default}
.deck-pos{color:var(--faint);font-size:13px;letter-spacing:.5px;min-width:52px;
 text-align:center}
.deck-hint{color:var(--faint);font-size:12px;text-align:center;margin:2px 0 0}
.deck-empty{color:var(--faint);font-size:13px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);padding:14px 17px;box-shadow:var(--card-sd)}
.read{padding:16px 19px}
.seg{margin-bottom:13px}
.seg:last-child{margin-bottom:0}
.lb{font-size:11.5px;color:var(--faint);letter-spacing:.8px;margin-bottom:5px}
.tx{font-size:13.5px;color:#33332f;line-height:1.72}
.seg.what .tx{font-size:15px;font-weight:600;color:var(--ink);line-height:1.55}
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:5px 6px;margin-bottom:5px}
.flow:last-child{margin-bottom:0}
.hop{background:var(--chainbg);border:1px solid var(--chainline);color:var(--chain);
 border-radius:6px;padding:3px 8px;font-size:12.5px;line-height:1.5}
.arw{color:var(--chain);opacity:.55;font-size:12px}
/* ── 解析折叠 ──
   用原生 <details>/<summary>，**不写一行 JS**：零脚本、键盘可达、禁用 JS 也能展开。
   按钮的两种文案都在 HTML 里，只用 CSS 切换显示 —— 不靠 content 伪元素塞文字，
   这样读屏器和"页面内查找"都能拿到真实文字。 */
.notes{border-top:1px dashed var(--line);padding-top:11px}
.notes>summary{list-style:none;cursor:pointer;user-select:none;
 display:inline-flex;align-items:center;gap:7px;font-size:12.5px;
 color:var(--accent);background:var(--pill);border:1px solid var(--pillline);
 border-radius:999px;padding:5px 13px;transition:background .15s ease}
.notes>summary::-webkit-details-marker{display:none}
.notes>summary::marker{content:""}
.notes>summary:hover{background:var(--pillline)}
.notes>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.notes>summary::after{content:"\\25be";font-size:10px;opacity:.75;
 transition:transform .15s ease}
.notes[open]>summary::after{transform:rotate(180deg)}
.cnt{color:var(--faint);font-size:11.5px}
.tg-close{display:none}
.notes[open] .tg-open{display:none}
.notes[open] .tg-close{display:inline}
.notes>ul{list-style:none;margin-top:12px}
.notes li{position:relative;padding-left:15px;margin-bottom:9px;font-size:13px;
 color:#33332f;line-height:1.72}
.notes li:last-child{margin-bottom:0}
.notes li::before{content:"*";position:absolute;left:2px;top:1px;color:var(--faint)}
.nk{color:var(--ink)}
.fml{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 background:var(--code);border-left:2px solid var(--accent);border-radius:4px;
 padding:7px 10px;margin:6px 0;font-size:12.5px;line-height:1.6;
 white-space:pre-wrap;overflow-x:auto}
/* 近似上下分式：不引 MathJax/KaTeX，纯 flex 把分子摞在分母上、中间一条横线。
   分子分母各自 stretch 撑满列宽，所以横线自动等于较宽那行的宽度。 */
.frac{display:flex;align-items:center;gap:.55em;flex-wrap:wrap;white-space:normal}
.fh{white-space:pre-wrap}
.fq{display:inline-flex;flex-direction:column;align-items:stretch;text-align:center}
.fn{padding:0 .55em 3px}
.fd{padding:3px .55em 0;border-top:1.5px solid currentColor}
code{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 background:var(--code);border-radius:3px;padding:1px 4px;font-size:12.5px}
.noread{padding:16px 19px;color:var(--faint);font-size:13px;display:flex;align-items:center}
.empty{color:var(--faint);font-size:13px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);padding:14px 17px;box-shadow:var(--card-sd)}
/* 归档与设置都用原生 <details>：一次点击展开，不上 9.11 那套「底栏 → 抽屉 → 遮罩」。 */
.archive{margin-top:30px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);padding:14px 18px;box-shadow:var(--card-sd)}
.archive>summary{list-style:none;cursor:pointer;user-select:none;
 display:flex;align-items:center;gap:8px;font-size:12px;color:var(--dim);
 letter-spacing:1px}
.archive>summary::-webkit-details-marker{display:none}
.archive>summary::after{content:"\\25be";font-size:10px;opacity:.7;margin-left:auto;
 transition:transform .15s ease}
.archive[open]>summary::after{transform:rotate(180deg)}
.archive[open]>summary{margin-bottom:10px}
.archive>summary:hover{color:var(--accent)}
.archive>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.archive a{display:inline-block;margin:0 8px 8px 0;padding:4px 10px;background:#efece4;
 border-radius:6px;color:#4a4740;text-decoration:none;font-size:13px}
.archive a:hover{background:#e6e2d6}
.archive a.cur{background:var(--ink);color:#fff}
/* 设置项：样式照抄 9.11 复杂版 */
.opt{display:flex;gap:10px;align-items:flex-start;padding:10px 0;cursor:pointer}
.opt input{margin-top:3px;width:16px;height:16px;accent-color:var(--accent)}
.opt span{font-size:13.5px}
.opt em{display:block;font-style:normal;color:var(--faint);font-size:12px;margin-top:3px;
 line-height:1.6}
/* 设置齿轮：顶栏最右，点开是一个右下角弹出的面板。
   原来是页脚上方一整块 <details>「设置」，占一行还容易看不见；改成图标后
   设置和归档不再混在一起，顶栏右上也终于有个正经用途。 */
.gear{position:relative;flex:none}
.gear>summary{list-style:none;cursor:pointer;user-select:none;
 width:32px;height:32px;border-radius:999px;border:1px solid var(--line);
 background:var(--card);color:var(--dim);font-size:15px;line-height:1;
 display:inline-flex;align-items:center;justify-content:center}
.gear>summary::-webkit-details-marker{display:none}
.gear>summary::marker{content:""}
.gear>summary:hover{color:var(--accent);border-color:var(--pillline)}
.gear>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.gear[open]>summary{color:var(--accent);border-color:var(--pillline)}
.gear-panel{position:absolute;right:0;top:calc(100% + 8px);z-index:20;
 width:min(330px,84vw);background:var(--card);border:1px solid var(--line);
 border-radius:12px;padding:4px 14px;box-shadow:var(--card-sd-hi);text-align:left}
footer{margin-top:24px;color:var(--faint);font-size:12px;text-align:center;line-height:1.8}
/* 手机：单栏。卡片仍是滑卡卡组里的一张（.deck-slide 保持 100% 宽），
   只是卡内两栏叠成一栏 —— 解读是阅读重心，字号要略微放大，
   传导链的环格要能整行排下，公式块允许横向滚动而不是硬换行把公式劈开，
   折叠按钮要有够大的触摸目标。 */
@media(max-width:760px){
 body{padding:16px 12px 40px}
 .wrap{max-width:100%}
 article{grid-template-columns:1fr;margin-bottom:0}
 .deck-track{gap:12px;padding:2px 0 10px}
 .news{border-right:0;border-bottom:1px solid var(--line);padding:14px 15px}
 .read{padding:14px 15px}
 .tx,.notes li{font-size:14.5px;line-height:1.78}
 .seg.what .tx{font-size:15.5px}
 .hop{font-size:13px;padding:4px 9px}
 /* 公式不换行会被劈开读不懂，让它自己横滚。分式是 flex 布局，不能锁 pre。 */
 .fml:not(.frac){font-size:12px;white-space:pre;overflow-x:auto;
  -webkit-overflow-scrolling:touch}
 .notes>summary{font-size:13.5px;padding:8px 15px}   /* 触摸目标别太小 */
 /* 手机端新闻栏的顶行：板块标签靠左浮、英文原文按钮靠右浮，并排放同一行。
    两者都按 .catpill 的尺寸做（11px 字 + 2px/9px 内边距）才一样高。
    （标签一浮起来就不占流，.orig 才会顶到内容区顶部，否则按钮要低一行。）
    代价是**浮动的标签会一直影响后面每一个行盒**：中文源的卡没有 .orig，
    标题就会挤在标签右边那条窄缝里（看着像从右上角起头）。所以标题/摘要/来源
    一律 clear 到标签这一行下面 —— 中文源的版式和改之前完全一致。 */
 .catpill{float:left;margin:0 9px 6px 0}
 .orig>summary{float:right;margin:0 0 6px 9px;padding:2px 9px;font-size:11px;gap:4px}
 .orig[open]>summary{margin-bottom:6px}
 .news a,.news .raw,.news .src{clear:both}
 .deck-btn{width:38px;height:38px}
 h1{font-size:19px}
 .archive a{font-size:13.5px;padding:6px 11px}
}
/* 手机窄屏也保持横向流式：.flow 默认 flex-wrap:wrap，节点从左往右排、
   放不下自动换行，箭头维持 "→"（不再转成上下竖排）。 */
/* 悬停微抬只是锦上添花，晕动症用户把动效关掉后不该还在动 */
@media(prefers-reduced-motion:reduce){
 article,.notes>summary,.notes>summary::after{transition:none}
 article:hover{transform:none}
}
@media(prefers-color-scheme:dark){
 :root{--bg:#171612;--card:#1e1d18;--line:#322f28;--ink:#e9e6dc;--dim:#b1ab9c;
  --faint:#8a857a;--accent:#d98a63;--chain:#7fb8a4;--chainbg:#1b2620;
  --chainline:#2e4238;--code:#292720;--warn:#e0b872;
  --pill:#2b2820;--pillline:#3f3a2d;
  --card-sd:0 1px 2px rgba(0,0,0,.4),0 12px 28px -12px rgba(0,0,0,.6);
  --card-sd-hi:0 2px 6px rgba(0,0,0,.5),0 20px 44px -14px rgba(0,0,0,.75)}
 .news{background:#211f1a}
 .archive a{background:#2a2821;color:#c7c2b4}
 .archive a.cur{background:var(--accent);color:#fff}
 .badge{background:#2a2821}
 .note{background:#2a2418;border-color:#5c4a1e;color:#e0b872}
 .badge.rules{background:#3a2f14;color:#e0b872}
 .tx,.notes li{color:#d8d4c9}
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

# ── 近似上下分式 ──
# 不引 MathJax/KaTeX（页面零依赖、离线可读），所以分式靠 CSS 把分子摞在分母上。
# 识别刻意收紧 —— **摞错的分式比不摞更难懂**，宁可回落成一行等宽文本：
#   · " / " 两侧必须带空格。`ΔP/P`、`C/(1+y)¹` 这种是整体符号，不该被劈成分式
#   · 顶层（括号深度 0）只能有一个 " / "，多于一个就说明是嵌套式，放弃
#   · 分子分母在深度 0 上不能出现加减号 —— `a = b / c + d` 切出来的分母是 `c + d`，
#     摞成 b/(c+d) 是错的；而 `(1 − (1+r)⁻ⁿ)` 整体带括号、深度 0 干净，可以摞
_OPEN, _CLOSE = "([{（【", ")]}）】"
_ADD_CHARS = "+＋−-"                       # 深度 0 上出现就说明分式边界不明确
EQ_SEPS = (" = ", " ≈ ", " ＝ ")           # 等号左边整段原样放在分式左侧


def _top_level_split(text: str, sep: str) -> list[str]:
    """只在括号深度 0 处按 sep 切分，括号里的同名符号不算分隔符。"""
    parts: list[str] = []
    buf: list[str] = []
    depth = i = 0
    while i < len(text):
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and text.startswith(sep, i):
            parts.append("".join(buf))
            buf = []
            i += len(sep)
            continue
        buf.append(ch)
        i += 1
    parts.append("".join(buf))
    return parts


def _has_top_level_add(text: str) -> bool:
    depth = 0
    for ch in text:
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(0, depth - 1)
        elif depth == 0 and ch in _ADD_CHARS:
            return True
    return False


def _strip_wrap(text: str) -> str:
    """整段被一对括号包住时去掉那对括号：`(r − g)` → `r − g`。

    只在首括号的配对就是末字符时才去 —— `(a) + (b)` 首尾也是括号，但不是一对。
    """
    if len(text) < 2 or text[0] not in _OPEN or text[-1] not in _CLOSE:
        return text
    depth = 0
    for i, ch in enumerate(text):
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth -= 1
            if depth == 0:
                return text[1:-1].strip() if i == len(text) - 1 else text
    return text


def _split_fraction(fml: str) -> tuple[str, str, str] | None:
    """把 `资本充足率 = 合格资本 / 风险加权资产` 拆成 (左边, 分子, 分母)。

    不是"简单两段分式"就返回 None，交回等宽块渲染。判定条件见上面注释。
    """
    head, body = "", fml
    for eq in EQ_SEPS:
        parts = _top_level_split(fml, eq)
        if len(parts) == 2 and parts[0].strip() and parts[1].strip():
            head, body = parts[0].strip() + eq.rstrip(), parts[1]
            break

    halves = _top_level_split(body, " / ")
    if len(halves) != 2:
        return None
    num, den = halves[0].strip(), halves[1].strip()
    if not num or not den:
        return None
    if _has_top_level_add(num) or _has_top_level_add(den):
        return None
    return head, _strip_wrap(num), _strip_wrap(den)


def render_formula(fml: str) -> str:
    """一条反引号公式 → HTML。能摞成分式就摞，否则一行等宽块。"""
    frac = _split_fraction(fml)
    if not frac:
        return f'<div class="fml">{esc(fml)}</div>'
    head, num, den = frac
    lead = f'<span class="fh">{esc(head)}</span>' if head else ""
    return (f'<div class="fml frac">{lead}<span class="fq">'
            f'<span class="fn">{esc(num)}</span>'
            f'<span class="fd">{esc(den)}</span></span></div>')


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
    块公式里形如 `A = B / C` 的简单分式会被摞成上下分子分母（render_formula）。
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
            out.append(render_formula(fml) if block
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

# 快讯类条目（华尔街见闻那种，标题即全部信息）的 what 常和标题逐字相同。
# 左栏已经原样展示了标题，右栏再抄一遍纯属重复 —— 命中就整段不渲染。
# 只在"几乎逐字重合"时命中：英文源的标题是英文、what 是中文，天然不会误伤；
# 中文标题被改写过（重合度低）也照常保留。
_TITLE_NOISE = re.compile(r"[\s，。、；：,.;:！？!?「」『』“”‘’\"'（）()]")


def _restates_title(card: dict, what: str) -> bool:
    title = _TITLE_NOISE.sub("", str(card.get("title") or ""))
    text = _TITLE_NOISE.sub("", str(what or ""))
    if not title or not text:
        return False
    if title == text:
        return True
    short, long = sorted((title, text), key=len)
    return len(short) >= 10 and short in long and len(short) / len(long) > 0.7


def _collapsible(open_label: str, close_label: str, body: str,
                 tail: str = "", cls: str = "notes") -> str:
    """默认收起的折叠块。原生 <details>/<summary>，**不需要一行 JS**。

    两种按钮文案都写进 HTML、只靠 CSS 切换显示，所以禁用 CSS、用读屏器、
    或者用浏览器的"页面内查找"时读到的都是真实文字，而不是空按钮。

    [预留·行测解读模式] 将来想在同一张卡上再挂一个视角（例如把这条新闻改写成
    一道因果推理题 + 答案），不用碰交互代码，直接复用这个函数：
        if ins.get("quiz"):
            segs.append(_collapsible(
                "展开行测解读", "收起行测解读",
                "".join(render_note(q) for q in ins["quiz"])))
    同一张卡上挂几个折叠块互不影响（<details> 各自独立），样式沿用 .notes；
    要让它们互斥展开（手风琴）就给每个 <details> 加同一个 name 属性。
    """
    return (f'<details class="seg {cls}"><summary>'
            f'<span class="tg tg-open">{esc(open_label)}</span>'
            f'<span class="tg tg-close">{esc(close_label)}</span>'
            f'{tail}</summary>{body}</details>')


def _is_english(*texts: str) -> bool:
    """英文字母明显多于汉字，就算英文源 —— 决定手机端要不要把原文收起来。

    阈值 12 个字母是为了防误判：「iPhone 17 发布会」这种中文标题里夹几个英文
    单词不该被当成英文源。
    """
    latin = cjk = 0
    for t in texts:
        for ch in str(t or ""):
            low = ch.lower()
            if "a" <= low <= "z":
                latin += 1
            elif "一" <= ch <= "鿿":   # CJK 统一汉字
                cjk += 1
    return latin >= 12 and latin > cjk


def render_card(card: dict, cat: str) -> str:
    ins = card.get("insight") or {}
    label = CATEGORY_LABELS.get(cat, "")
    chip = (f'<div class="catpill" data-cat="{esc(cat)}">{esc(label)}</div>'
            if cat else "")

    meta = " · ".join(x for x in (esc(card.get("source")),
                                  esc(card.get("published"))) if x)
    raw = card.get("raw_summary") or ""
    raw_html = f'<div class="raw">{esc(raw[:150])}</div>' if raw else ""
    link = (f'<a href="{esc(card.get("url"))}" target="_blank" '
            f'rel="noopener noreferrer">{esc(card.get("title"))}</a>')
    if _is_english(card.get("title"), raw):
        # 英文源：标题和摘要都收进这个 details，手机端默认只显示中文解读，
        # 点「查看英文原文」才展开；桌面端由 CSS 直接摊开（见 .orig 那段）。
        news_body = (f'<details class="orig"><summary>查看英文原文</summary>'
                     f'{link}{raw_html}</details><div class="src">{meta}</div>')
    else:
        news_body = f'{link}<div class="src">{meta}</div>{raw_html}'
    left = f'<div class="news">{chip}{news_body}</div>'

    if not ins:
        # 规则模式：明确说没有解读，不拿新闻摘要冒充
        return (f'<article data-cat="{esc(cat)}">{left}'
                f'<div class="noread">今日无 AI 解读（规则模式）</div></article>')

    # 发生了什么 / 市场为什么在意 / 金融传导 三段默认直接展示 —— 它们是"这条为什么
    # 重要"的主线，收起来就等于没解读。只有解析（概念摊开讲）默认收起。
    segs = []
    for key, label in TEXT_SEGMENTS:
        if not ins.get(key):
            continue
        if key == "what" and _restates_title(card, ins[key]):
            continue                      # 快讯：标题已经说了，别再抄一遍
        segs.append(f'<section class="seg {key}"><div class="lb">{label}</div>'
                    f'<div class="tx">{esc(ins.get(key))}</div></section>')

    if ins.get("chain"):
        segs.append(f'<section class="seg chain"><div class="lb">金融传导</div>'
                    f'{render_chain(str(ins["chain"]))}</section>')

    notes = ins.get("notes") or []
    if isinstance(notes, str):
        notes = [ln for ln in notes.splitlines() if ln.strip()]
    items = [str(n) for n in notes if str(n).strip()]
    if items:
        # 解析动辄六到八条、每条两三行，全铺开会把卡片撑得看不到下一条。
        # 默认收起、给个"展开解析"按钮，卡片就回到一眼能扫完的高度。
        lis = "".join(render_note(n) for n in items)
        segs.append(_collapsible("展开解析", "收起解析", f"<ul>{lis}</ul>",
                                 tail=f'<span class="cnt">{len(items)} 条</span>'))
    else:
        segs += [
            f'<section class="seg"><div class="lb">{label}</div>'
            f'<div class="tx">{esc(ins.get(key))}</div></section>'
            for key, label in LEGACY_SEGMENTS if ins.get(key)
        ]

    return f'<article data-cat="{esc(cat)}">{left}<div class="read">{"".join(segs)}</div></article>'



# 滑卡导航用的内嵌 JS。**刻意只做四件事**：翻上一张/下一张、方向键响应、计数器、
# 把当前卡的位置写进 hash（方便分享/刷新后停在同一张）。其余交互（解析折叠、外链、
# 深色模式）全在原生 HTML/CSS 里，不在这份脚本中。
# 脚本放在页面末尾 <script> 里；即便它没跑（比如被禁），用户仍能手动左右滑卡片。
DECK_JS = r"""<script>
(function () {
  function init() {
    var track = document.querySelector('.deck-track');
    if (!track || track.children.length === 0) return;
    var slides = track.children;
    var prev = document.querySelector('.deck-prev');
    var next = document.querySelector('.deck-next');
    var pos = document.querySelector('.deck-pos');
    var ctl = document.querySelector('.deck-ctl');
    var N = slides.length;
    var maxH = 0;
    // 卡片高低不齐，轨道却按最高的那张留白 —— 量出差额，把翻页键上提到当前卡
    // 正下方（负 margin 吃掉差额）。**不改轨道高度**：改了会把相邻卡裁掉或抖一下。
    function measure() {
      maxH = 0;
      for (var k = 0; k < N; k++) {
        var h = slides[k].offsetHeight;
        if (h > maxH) maxH = h;
      }
    }
    function update() {
      var w = track.clientWidth;
      var i = Math.round(track.scrollLeft / w);
      if (i < 0) i = 0;
      if (i > N - 1) i = N - 1;
      if (pos) pos.textContent = (i + 1) + '/' + N;
      if (prev) prev.disabled = (i <= 0);
      if (next) next.disabled = (i >= N - 1);
      // 只在手机端收：桌面屏幕高，翻页时下方归档卡片跟着上下跳反而碍眼。
      if (ctl && slides[i]) {
        var narrow = window.matchMedia('(max-width:760px)').matches;
        ctl.style.marginTop = narrow
          ? (4 - (maxH - slides[i].offsetHeight)) + 'px' : '';
      }
    }
    function go(i) {
      if (i < 0) i = 0;
      if (i > N - 1) i = N - 1;
      var w = track.clientWidth;
      track.scrollTo({ left: i * w, behavior: 'smooth' });
    }
    if (prev) prev.addEventListener('click', function () {
      go(Math.round(track.scrollLeft / track.clientWidth) - 1);
    });
    if (next) next.addEventListener('click', function () {
      go(Math.round(track.scrollLeft / track.clientWidth) + 1);
    });
    track.addEventListener('scroll', update, { passive: true });
    // 展开/收起解析会改变卡片高度（toggle 不冒泡，靠捕获阶段收到）
    track.addEventListener('toggle', function () { measure(); update(); }, true);
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { go(Math.round(track.scrollLeft / track.clientWidth) + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft')  { go(Math.round(track.scrollLeft / track.clientWidth) - 1); e.preventDefault(); }
    });
    measure();
    update();
    window.addEventListener('resize', function () { measure(); update(); }, { passive: true });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
</script>"""


# 注册 service worker。写在页面里而不是 sw.js 里 —— 注册动作必须在页面上下文执行。
# 失败就静默吞掉：没有 SW 只是不能离线，页面本身照常能看。
SW_REGISTER = """<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('./sw.js').catch(function () {});
  });
}
</script>"""

# 设置项的行为，照抄 9.11 复杂版（同一个 localStorage key：dailybrief.showOrig）。
# 桌面端强制摊开英文原文 —— 不赌浏览器对未 open 的 details 用 display:none 还是
# content-visibility 隐藏，后者光靠 CSS 覆盖压不住，英文原文会被吞成空白。
SETTINGS_JS = """<script>
(function () {
  var OPT_KEY = 'dailybrief.showOrig';
  function store(k, v) {
    try {
      if (v === undefined) return window.localStorage.getItem(k);
      window.localStorage.setItem(k, v);
    } catch (e) { /* 隐私模式或禁用存储：当作没设置过，别让页面挂掉 */ }
    return null;
  }
  function applyOrig() {
    var desktop = window.matchMedia('(min-width:761px)').matches;
    var on = desktop || store(OPT_KEY) === '1';
    var ds = document.querySelectorAll('.orig');
    for (var i = 0; i < ds.length; i++) ds[i].open = on;
    var cb = document.getElementById('opt-orig');
    if (cb) cb.checked = store(OPT_KEY) === '1';
  }
  var box = document.getElementById('opt-orig');
  if (box) box.addEventListener('change', function () {
    store(OPT_KEY, box.checked ? '1' : '0');
    applyOrig();
  });
  // 齿轮面板：原生 <details> 不会自己收，补上「点外面」和 Esc。
  // 点齿轮本身不用管 —— 那一下在 .gear 里，contains 命中就放过，交给 details 自己翻。
  var gear = document.querySelector('.gear');
  if (gear) {
    document.addEventListener('click', function (e) {
      if (gear.open && !gear.contains(e.target)) gear.open = false;
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') gear.open = false;
    });
  }
  applyOrig();
  window.addEventListener('resize', applyOrig);
})();
</script>"""

# sw.js 本体，由 build_site.py 写进 site/。__VERSION__ 会换成构建日期。
# 版本号一变，activate 里就把旧缓存整批删掉，不会出现「壳是新的、内容还是旧的」。
SERVICE_WORKER = """/* 离线缓存。由 build_site.py 生成，别手改 —— 下次渲染会覆盖。

   HTML 走 network-first：联网时永远拿当天最新，断网回落到缓存，
   连缓存都没有就退回首页。图标/manifest 走 cache-first，它们基本不变。
   缓存名带版本号（构建日期 + 图标指纹），页面一更新就整体换掉。 */
const CACHE = 'dailybrief-__VERSION__';
const SHELL = ['./', './index.html', './manifest.webmanifest',
               './icon-192.png?v=__REV__', './icon-512.png?v=__REV__',
               './apple-touch-icon.png?v=__REV__'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  const isHTML = req.mode === 'navigate'
    || (req.headers.get('accept') || '').indexOf('text/html') !== -1;

  if (isHTML) {
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((hit) => hit || caches.match('./index.html')))
    );
    return;
  }

  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(req, copy));
      return res;
    }))
  );
});
"""


def render_page(payload: dict, dates: list[str], current: str) -> str:
    mode = payload.get("mode", "llm")
    # 「AI 解读」这枚徽章撤了：每张卡本来就只有 AI 解读，页脚也写了「解读由 Claude
    # 生成」，挂在顶栏只是占地方 —— 位置让给设置齿轮。规则模式是例外：那天整页没有
    # 解读，这枚橙章是警示，留在原处和下面的黄色说明框互相呼应。
    badge = ('<span class="badge rules">规则模式</span>' if mode == "rules" else "")
    # 顶部品牌区：logo（勢字）+ 知势 DailyBrief，照 dailybrief-ui 的 masthead。
    cn, _, en = BRAND.partition(" ")
    note = ""
    if mode == "rules":
        reason = payload.get("fallback_reason") or "LLM 不可用"
        note = (f'<div class="note">今日为规则模式：只按来源权重与时间筛出了条目，'
                f'<b>没有 AI 解读</b>。原因：{esc(reason)}</div>')

    # 滑卡卡组：两个板块的所有卡片按顺序收进同一条横向轨道，一次显示一张。
    # 每张卡片自带"所属板块"标签（因为不再有板块标题当锚点），计数/筛选都靠卡片。
    slides: list[str] = []
    total = 0
    for cat in CATEGORIES:
        for card in payload.get(cat) or []:
            total += 1
            slides.append(f'<div class="deck-slide">{render_card(card, cat)}</div>')
        if not (payload.get(cat) or []):
            # 空板块不必再占一行卡片；但若全空，给一句提示。
            if not slides:
                slides.append(f'<div class="deck-slide"><div class="deck-empty">'
                              f'今日该板块无入选条目</div></div>')

    ctl = (f'<div class="deck-ctl" aria-label="卡片导航">'
           f'<button class="deck-btn deck-prev" type="button" '
           f'aria-label="上一张" disabled>‹</button>'
           f'<span class="deck-pos">1/{total}</span>'
           f'<button class="deck-btn deck-next" type="button" '
           f'aria-label="下一张">›</button></div>')
    body = (f'<div class="deck-track" tabindex="0">'
            f'{"".join(slides)}</div>'
            f'{ctl}'
            f'<p class="deck-hint">← → 或按钮切换 · 手机左右滑动</p>')

    links = "".join(
        f'<a href="{d}.html"{" class=\"cur\"" if d == current else ""}>{d}</a>'
        for d in dates[:ARCHIVE_DAYS]
    )
    # 归档：原生 <details>，一次点击展开 —— 9.11 那套要「底栏 → 抽屉」两步，太绕。
    archive = (f'<details class="archive"><summary>历史归档</summary>{links}</details>'
               if links else "")
    # 设置：顶栏右边的齿轮（原来在页脚前单独占一行）。行为照抄 9.11
    # （同样的 id / localStorage key）。︎ 是文本变体选择符 —— 不加的话
    # iOS/安卓会把 ⚙ 渲染成彩色 emoji，跟顶栏的素色图标格格不入。
    settings = (
        '<details class="gear"><summary aria-label="设置" title="设置">⚙︎</summary>'
        '<div class="gear-panel">'
        '<label class="opt"><input type="checkbox" id="opt-orig">'
        '<span>默认展开英文原文<em>关闭时，英文源的卡片在手机端仅显示中文解读，'
        '需点击「查看英文原文」展开；桌面端始终显示英文原文。</em></span></label>'
        '</div></details>')

    sub_html = f'<p class="sub">{esc(TAGLINE)}</p>' if TAGLINE else ""

    # 手机上的注意点，都踩过：
    # - charset 必须在 <head> 最前面（早于 title），否则中文有几率乱码
    # - viewport 不写 maximum-scale/user-scalable，锁缩放会让公式没法放大看
    # - theme-color 让 Safari/Chrome 的地址栏跟着页面配色，加到主屏后不突兀
    # - apple-mobile-web-app-* 让 iOS「添加到主屏幕」后像个独立 App
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{esc(BRAND)} · {esc(payload.get("date"))}</title>
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#faf8f3" media="(prefers-color-scheme:light)">
<meta name="theme-color" content="#171612" media="(prefers-color-scheme:dark)">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{esc(BRAND)}">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="format-detection" content="telephone=no">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png?v={asset_rev()}">
<style>{CSS}</style></head><body><div class="wrap">
<header><span class="logo" aria-hidden="true">勢</span>
<div class="hd"><h1>{esc(cn)} <span class="en">{esc(en or "DailyBrief")}</span></h1>
{sub_html}</div>
<span class="date">{esc(payload.get("date"))}</span>{badge}{settings}</header>
{note}
{body}
{archive}
<footer>{esc(FOOTER)}<br>
条目来自各源公开 RSS 与 API，解读由 Claude 生成，仅供学习参考，不构成投资建议</footer>
</div>
{DECK_JS}
{SETTINGS_JS}
{SW_REGISTER}
</body></html>"""


def manifest() -> str:
    """PWA manifest：iOS/Android「添加到主屏幕」后有名字、图标和独立窗口。

    2026-09-11 起带 icons + service worker —— Chrome 必须同时看到这两样才给
    真正的「安装应用」（独立窗口），否则只能加到主屏幕当书签。
    purpose 用 "any maskable"：图标本身留了足够边距，切圆形/方形都不会切到字。
    三张 PNG 是 site/ 下的现成文件，这里只引用，不生成图片。
    图标 src 挂 ?v=<指纹>：URL 不变的话，装在桌面上的 App 会一直用安装时那份图标。
    """
    rev = asset_rev()
    return json.dumps({
        "name": SITE_NAME,
        "short_name": SITE_NAME.split("|")[0].strip() or BRAND,
        "start_url": "./index.html",
        "scope": "./",
        "display": "standalone",
        "background_color": "#faf8f3",
        "theme_color": "#faf8f3",
        "lang": "zh-CN",
        "icons": [
            {"src": f"./icon-192.png?v={rev}", "sizes": "192x192",
             "type": "image/png", "purpose": "any maskable"},
            {"src": f"./icon-512.png?v={rev}", "sizes": "512x512",
             "type": "image/png", "purpose": "any maskable"},
        ],
    }, ensure_ascii=False, indent=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--latest", action="store_true", help="只渲染最新一天")
    ap.add_argument("--date", default="",
                    help="只渲染指定日期（如 2026-09-10），不动 index/manifest")
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

    todo = files[:1] if args.latest else files
    if args.date:
        todo = [p for p in todo if p.stem == args.date]
        if not todo:
            print(f"data/ 下没有 {args.date}.json", file=sys.stderr)
            return 1

    for path in todo:
        payload = json.loads(path.read_text(encoding="utf-8"))
        out = SITE_DIR / f"{path.stem}.html"
        out.write_text(render_page(payload, dates, path.stem), encoding="utf-8")
        print(f"渲染 {out.relative_to(ROOT)}")

    # service worker 跟着一起写。版本号取本次渲染的日期，页面一更新，
    # 浏览器发现 sw.js 变了就重装，旧缓存整批清掉。
    # manifest 与 sw.js 跟日期无关，--date 单渲一天时也要写，否则装到手机上的
    # 图标/离线壳会停在旧版本。
    sw_path = SITE_DIR / "sw.js"
    sw_path.write_text(
        SERVICE_WORKER.replace("__VERSION__", f"{todo[0].stem}-{asset_rev()}")
                      .replace("__REV__", asset_rev()),
        encoding="utf-8")
    print(f"渲染 {sw_path.relative_to(ROOT)}")
    (SITE_DIR / "manifest.webmanifest").write_text(manifest(), encoding="utf-8")

    if args.date:
        return 0

    shutil.copyfile(SITE_DIR / f"{dates[0]}.html", SITE_DIR / "index.html")
    # GitHub Pages 默认走 Jekyll，会忽略下划线开头的文件并偶尔改写内容。
    # 我们只发静态 HTML，直接关掉。
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    print(f"首页指向 {dates[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
