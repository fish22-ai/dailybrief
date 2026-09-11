"""把 data/*.json 渲染成静态站点。纯标准库 + f-string 模板，不引前端框架。

布局：一条事件一张**独立抽卡卡片**（大圆角 + 阴影 + 悬停微抬）。所有卡片收进一个
**横向滑卡卡组**（.deck）：一次只看一张，桌面用 ← → 键或按钮翻，手机左右滑动翻。
卡内左侧是新闻本体（标题/来源/时间），右侧是 AI 解读**四张研读卡**：
30秒事实内核 / 金融传导全景脉络 / 深入研读与博弈透视 / 行动启示与认知内化。
解读才是主体 —— 宽屏左栏只占三分之一，右栏是阅读重心；窄屏叠成单栏。

**卡组是跨天连续的**：轨道里依次是[前一天][当天][后一天]的卡片，打开时停在当天的
第一张。滑到当天最后一张继续往前就接上后一天、往回滑就接上前一天，不会卡在当天两端；
别的日期的卡片多一个日期标签，免得不知道自己滑到了哪天。计数器是**当天**的序号
（3/7），放在卡片下面。

**手机是一屏一张卡，但页面照常整页滚动**：卡片做得够短 —— 标题/来源/发生了什么/
为什么在意/金融传导都塞进一屏（字数多了由页尾脚本把卡内字号按 `--fit` 往下缩，
下限 0.65），横向翻卡时不用上下找内容；但**不做卡内局部滚动**（那样读起来憋屈），
真超了就让它超、整页顺滑上下滚，跟普通网页一致。「解析」是折叠块，本来就不在这一屏里。

**JS 只做三件事**（约 70 行，放页面底部 <script>）：翻卡、计数器、按需缩放字号。
解析的展开/收起仍用原生 <details>/<summary>，零脚本、键盘可达、禁用 JS 也能展开。
滑卡本身靠原生横向滚动 + scroll-snap；即便脚本没跑，页面仍能手动左右滑
（只是会停在轨道最左边那张，也就是前一天的卡）。

传导链按 " → " 拆成一格一环渲染（用 "；" 分隔的支线另起一行），比一长串文本好读。
解析里的公式是**反引号包住的纯文本**，长公式单独成等宽块 —— 页面刻意不引
KaTeX/MathJax，零依赖、离线也读得出来。形如 `资本充足率 = 合格资本 / RWA` 的
简单两段分式会用 CSS 摞成上下分子分母（`_split_fraction()`），不靠任何公式库。

2026-09-11 起 AI 直接产出结构化解读：transmissionChain 是 4 个带
trigger/mechanism/assetImpacts/timeHorizon/keySensitivity 的对象，页面**照着渲染**，
不再把一根平文本按位置硬切成 4 段（那次的问题：切的三个字段同源，渲染出来是同一句话）。
配套还有 summary30s / historicalAnalogy / gameTheoryStakeholders / forwardIndicators /
takeaways / notes。参考站的 surfaceVsCore（表面直觉 vs 机构内核）、reflectionQuestion
（内化自测思考题）与 AskAI 追问用户明确不要，这里既不产出也不渲染。

**旧归档必须继续能看**：data/ 里 2026-09-11 之前的数据还是 what/why/chain/watch/term
或四段老格式（chain 是平文本）。render_card() 检测不到 transmissionChain 就回落到
_chain_stages() 那套启发式切分 + 旧标签，重跑不会让历史页面掉内容。

三个后续扩展位已在代码里标注，搜 `[预留·` 可以直接找到：
皮肤主题（CSS 变量层）、左右滑动抽卡（.deck 轨道）、行测解读模式（_collapsible()）。

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
SITE_NAME = config.get_str("site", "name", "知势|Daily Brief")
# 顶部品牌名：配置里是「知势|Daily Brief」，展示时去竖线、空格拼成「知势 DailyBrief」
BRAND = SITE_NAME.split("|")[0].strip() + " DailyBrief"
TAGLINE = config.get_str("site", "tagline", "全球政经大事 · 趋势与影响")
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
:root{--bg:#faf8f3;--card:#fff;--line:#e8e5db;--ink:#191918;--dim:#6b6a62;
 --faint:#9a988f;--accent:#b44322;--accent2:#1c4e4f;--chain:#1c4e4f;--chainbg:#eef4f1;
 --chainline:#d6e3da;--code:#f2f1eb;--warn:#8a6100;
 --pill:#f3f0e8;--pillline:#e2dcc9;--card-r:14px;
 --serif:Georgia,"Songti SC","Noto Serif CJK SC","SimSun",serif;
 --card-sd:0 1px 2px rgba(30,27,20,.06),0 10px 26px -12px rgba(30,27,20,.14);
 --card-sd-hi:0 2px 6px rgba(30,27,20,.08),0 18px 38px -16px rgba(30,27,20,.22)}
body{font:15px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
 background:var(--bg);color:var(--ink);padding:26px 18px 96px}
.wrap{max-width:1240px;margin:0 auto}
header{display:flex;flex-wrap:wrap;align-items:center;gap:10px 14px;
 padding-bottom:13px;border-bottom:1px solid var(--line)}
header .logo{width:38px;height:38px;border-radius:9px;background:var(--ink);color:var(--bg);
 display:inline-flex;align-items:center;justify-content:center;font-family:var(--serif);
 font-size:19px;font-weight:700;flex:none}
.hd{flex:1;min-width:0}
h1{font-family:var(--serif);font-weight:700;font-size:19px;letter-spacing:.2px;
 display:flex;align-items:baseline;gap:6px;line-height:1.2}
h1 .en{font-size:12px;font-weight:500;color:var(--faint);letter-spacing:0}
header .sub{font-size:12px;color:var(--faint);margin-top:2px}
header .date{font-size:12px;color:var(--dim)}
.date{color:var(--dim);font-size:14px}
.badge{font-size:12px;padding:2px 9px;border-radius:10px;background:#efece4;color:var(--dim)}
.badge.rules{background:#fff3cd;color:var(--warn)}
.note{font-size:13px;color:var(--warn);background:#fdf3df;border:1px solid #ecd9a8;
 border-radius:6px;padding:9px 13px;margin:14px 0}
h2{font-size:14px;margin:26px 0 12px;color:var(--dim);letter-spacing:1px;
 display:flex;align-items:center;gap:10px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
/* ── 抽卡卡片 ──
   一条事件 = 一张独立卡片：大圆角 + 双层阴影 + 悬停微抬，像一张能抽出来的卡。
   宽屏保持"左新闻 1/3、右解读 2/3"，窄屏叠成单栏（见下面的媒体查询）。
   两栏的栅格挂在 .card-body 上而不是 article 上：手机要靠 .card-body 当纵向滚动
   容器（卡片撑满一屏、放不下才内部滚），栅格留在 article 上就没法让它当一个盒子。
   [预留·左右滑动抽卡] 轨道里的卡片就是 .deck-slide，默认已经是滑动卡组。
   若只想要原来的竖排平铺，改一行：.deck-track{display:block} 并把
   .deck-slide{flex:0 0 100%} 删掉即可。两种形态共用同一份卡片样式。 */
.cards{display:block}
article{background:transparent;border:0;border-radius:0;margin-bottom:16px;overflow:hidden}
.card-body{display:grid;grid-template-columns:minmax(240px,1fr) 2fr;gap:14px}
.news{padding:15px 17px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);box-shadow:var(--card-sd)}
.news a{color:var(--ink);text-decoration:none;font-family:var(--serif);font-weight:600;
 font-size:15px;display:block;margin-bottom:8px;line-height:1.45}
.news a:hover{color:var(--accent);text-decoration:underline}
.src{color:var(--faint);font-size:12px}
.raw{color:var(--dim);font-size:12.5px;margin-top:9px;padding-top:9px;
 border-top:1px dashed var(--line)}
/* 卡片顶端一条色带 + 板块标签。因为滑卡把所有板块、甚至前后几天的条目都并成一条，
   卡片本身要自报板块；跨天滑过来的卡片还要自报"这是哪天"（.daychip）。 */
article{position:relative}
article[data-cat]{overflow:hidden}
article[data-cat]::before{content:"";position:absolute;top:0;left:0;right:0;height:3px}
article[data-cat="markets"]::before{background:var(--accent)}
article[data-cat="policy"]::before{background:#a8690f}
.chips{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:8px;
 padding-right:30px}   /* 右上角留给收藏星标 */
.catpill{display:inline-block;font-size:11px;letter-spacing:.4px;
 padding:2px 9px;border-radius:999px}
.catpill[data-cat="markets"]{background:#fbece7;color:#b44322}
.catpill[data-cat="policy"]{background:#f3ead3;color:#8a620f}
.daychip{font-size:11px;letter-spacing:.3px;color:var(--faint);
 border:1px solid var(--line);border-radius:999px;padding:1px 8px}
@media(prefers-color-scheme:dark){
 .catpill[data-cat="markets"]{background:#3a241c;color:#d98a63}
 .catpill[data-cat="policy"]{background:#2f2a16;color:#e0b872}
}

/* ── 滑卡卡组 ──
   一次只显示一张卡片，桌面用 ←  → 方向键 / 按钮翻，手机左右滑动翻。
   轨道本身可横向滚动（touch 天然支持），按钮和方向键也只是把 scrollLeft 挪一格；
   滚动结束时按 scroll-snap 对齐，所以键盘和手指落到同一套位置逻辑上。
   轨道里装的是**连续几天**的卡片，所以"上一张/下一张"跨过当天边界就自然接到
   前一天 / 后一天，没有特判。计数器和按钮放在卡片下面（手机上一屏一张，
   卡片占满剩下的高度，控件只能往下排）。 */
.deck{position:relative}
.deck-track{display:flex;overflow-x:auto;overscroll-behavior-x:contain;
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
.deck-empty{color:var(--faint);font-size:13px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);padding:14px 17px;box-shadow:var(--card-sd)}
.read{padding:0}
.seg{margin-bottom:13px}
.seg:last-child{margin-bottom:0}
/* ── 研读卡：每一节是一张独立白卡，卡头是「深色图标块 + 衬线标题（+副题）」，
   样式参照 fish22-ai/dailybrief-ui 的头卡：发生了什么=核、为什么在意=因、
   金融传导=链、深入研读=研、行动启示=动。 */
.seg{background:var(--card);border:1px solid var(--line);border-radius:var(--card-r);
 padding:11px 13px;box-shadow:var(--card-sd);margin-bottom:10px}
.seg:last-child{margin-bottom:0}
.sec-hd{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.sec-hd .dot{width:8px;height:8px;border-radius:50%;background:var(--accent);flex:none}
.seg.why .sec-hd .dot{background:var(--accent2)}
.seg.chain .sec-hd .dot{background:var(--chain)}
/* 研读卡的小标题要**明显大于正文**（正文 13.5px）：早先它是 14px，跟正文几乎一样，
   手机端正文还乘了 14.5px 的基准，结果标题反而是全卡最小的字，层级完全反了。 */
.sec-hd h4{font-family:var(--serif);font-weight:700;font-size:16px;letter-spacing:.3px}
.sec-hd .sub{color:var(--faint);font-size:11px;margin-top:1px;letter-spacing:.2px}
.tx{font-size:13.5px;color:#33332f;line-height:1.72}
.seg.what .tx{font-size:15px;font-weight:600;color:var(--ink);line-height:1.55}
/* ── 30秒事实内核：三条客观事实，勾号 + 文本 ──
   类名刻意用 .fact-list 而不是 .facts —— 外层的研读卡也带着 facts 这个 cls，
   两者同名的话栅格/列表样式会互相串（行动启示那边就踩过一次，见 .take-list）。 */
.fact-list{list-style:none}
.fact-list li{position:relative;padding-left:19px;margin-bottom:7px;font-size:13.5px;
 color:#33332f;line-height:1.7}
.fact-list li:last-child{margin-bottom:0}
.fact-list li::before{content:"\\2713";position:absolute;left:1px;top:0;font-size:12px;
 font-weight:700;color:var(--accent2)}
/* ── 深入研读与博弈透视：历史参照 / 博弈各方 / 前瞻指标 三小块 ──
   小节标题是「色点 + 小字标签」，比主卡头低一级，读起来不跟四张研读卡抢。 */
.deep-sub{margin-bottom:12px}
.deep-sub:last-child{margin-bottom:0}
.deep-lb{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:600;
 color:var(--faint);letter-spacing:.6px;margin-bottom:7px}
.deep-lb::before{content:"";width:6px;height:6px;border-radius:50%;
 background:var(--accent2);flex:none}
.analogy{background:var(--bg);border:1px solid var(--line);border-radius:9px;
 padding:10px 12px}
.analogy .ay{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:5px}
.analogy .ae{font-family:var(--serif);font-weight:700;font-size:13.5px;color:var(--ink)}
.analogy .ayr{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 font-size:11px;color:var(--faint)}
.analogy p{font-size:12.5px;color:#33332f;line-height:1.7}
.parties{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}
.party{background:var(--card);border:1px solid var(--line);border-radius:9px;
 padding:9px 11px}
.party .pn{display:flex;justify-content:space-between;gap:6px;font-weight:700;
 font-size:13px;color:var(--ink);padding-bottom:6px;margin-bottom:6px;
 border-bottom:1px solid var(--line)}
.party .pn i{font-style:normal;font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",
 Consolas,monospace;font-size:10px;color:var(--faint)}
.party .pl{display:block;font-size:10px;color:var(--faint);margin-bottom:2px}
.party .pv{font-size:12px;color:#4f4f48;line-height:1.6}
.party .pb{display:block;font-size:10px;font-weight:600;color:var(--accent);margin:7px 0 2px}
.party .pvb{font-size:12px;font-weight:500;color:var(--ink);line-height:1.6}
.inds{display:flex;flex-direction:column;gap:6px}
.ind{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:8px 11px}
.ind .ih{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;margin-bottom:4px}
.ind .in{font-size:13px;font-weight:600;color:var(--ink)}
.ind .it{font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",Consolas,monospace;
 font-size:11px;padding:1px 7px;border-radius:4px;background:#ede6dc;color:#705030}
.ind .is{font-size:12px;color:var(--dim);line-height:1.65}
/* ── 行动启示与认知内化：投资者 / 实体与从业者 / 个人与家庭 三栏 ──
   栅格挂在 .take-list 这个**内层** div 上，绝不能挂在研读卡本身（它的 cls 也叫
   takes）—— 同名时卡头和内容会一起被当成栅格子项：卡头挤进第一列、内容进第二列、
   第三列空着，而且手机端因为栅格已叠成单栏反而看不出来。踩过一次，别再合并。 */
.take-list{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.take{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:10px 12px}
.take .tl{font-size:13px;font-weight:700;color:var(--ink);margin-bottom:5px}
.take p{font-size:12.5px;color:#4f4f48;line-height:1.7}
/* 窄屏：三栏并排会挤成豆腐块，直接叠成单栏 */
@media(max-width:760px){
 .parties,.take-list{grid-template-columns:1fr}
}
/* ── 经济传导脉络：4 阶段步骤条 + 统一详情面板（照 dailybrief-ui）──
   链条的环被均分成 4 个阶段（不烧 LLM，只重组原文）。顶部 4 个 Step 标签横排
   （mono「STEP 0n」徽章 + 标题，标题允许完整换行、不省略）；点击任意 Step，
   详情都在整个模块**下方**的同一块面板里切换，不跟着某个 Step 列走。
   选 Step 用原生 radio（每张卡独立 radio 组），面板显隐靠 CSS `:has()` 联动，
   **零 JS**，键盘方向键也能切。 */
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:5px}
.step{position:relative;cursor:pointer;user-select:none;border:1px solid var(--line);
 border-radius:9px;background:var(--card);padding:7px 9px 8px;display:block;overflow:hidden}
.step input{position:absolute;inset:0;opacity:0;cursor:pointer}
.step:focus-within{outline:2px solid var(--accent);outline-offset:2px}
.stepn{display:inline-block;font-family:ui-monospace,SFMono-Regular,"Cascadia Mono",
 Consolas,monospace;font-size:9.5px;font-weight:700;letter-spacing:.6px;
 padding:1px 6px;border-radius:4px;background:var(--code);color:var(--faint);
 white-space:nowrap}   /* STEP 0n 徽章别在窄卡里折成两行 */
.step:has(input:checked) .stepn{background:var(--ink);color:var(--bg)}
.stepl{display:block;margin-top:6px;font-size:12px;line-height:1.45;font-weight:500;
 color:var(--dim);white-space:normal}   /* 标题完整展开，不省略 */
.step:hover .stepl{color:var(--ink)}
.step:has(input:checked) .stepl{color:var(--ink);font-weight:700}
.step:has(input:checked){border-color:var(--ink)}
/* 选中态底部一根砖红细条，就是参考 UI 的 active 指示条 */
.step:has(input:checked)::after{content:"";position:absolute;bottom:0;left:0;right:0;
 height:2px;background:var(--accent)}
/* 统一详情面板：只显示当前选中的那个（radio + :has，零 JS） */
.step-panels{margin-top:8px}
.step-panel{display:none;background:var(--bg);border:1px solid var(--line);
 border-radius:10px;padding:9px 10px}
.steps:has(input[value="1"]:checked) ~ .step-panels .panel-1,
.steps:has(input[value="2"]:checked) ~ .step-panels .panel-2,
.steps:has(input[value="3"]:checked) ~ .step-panels .panel-3,
.steps:has(input[value="4"]:checked) ~ .step-panels .panel-4{display:block}
.step-hd{display:flex;align-items:center;gap:6px;padding-bottom:6px;margin-bottom:7px;
 border-bottom:1px dashed var(--line)}
.step-hd b{width:17px;height:17px;border-radius:50%;background:var(--ink);color:var(--bg);
 font-size:10.5px;font-weight:700;display:inline-flex;align-items:center;
 justify-content:center;flex:none}
.step-hd span{font-family:var(--serif);font-weight:700;font-size:13.5px;color:var(--ink)}
.step-hd .step-t{margin-left:auto;font-style:normal;font-family:ui-monospace,
 SFMono-Regular,"Cascadia Mono",Consolas,monospace;font-size:10.5px;color:var(--faint);
 white-space:nowrap;flex:none}
.step-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
.sf{background:var(--card);border:1px solid var(--line);border-radius:8px;
 padding:calc(9px*var(--fit,1)) calc(11px*var(--fit,1))}
.sf-lb{display:flex;align-items:center;gap:5px;font-size:10.5px;font-weight:600;
 color:var(--faint);letter-spacing:.8px;margin-bottom:5px}
.sf-lb::before{content:"";width:6px;height:6px;border-radius:50%;
 background:var(--accent);flex:none}
.sf-logic .sf-lb::before{background:var(--accent2)}
.sf-v{font-size:12px;line-height:1.6;color:#33332f}
/* 资产影响：绿涨/红跌/琥珀震荡 的色块行，照 reference 的 Impacted Asset Classes */
.impacts{margin-top:calc(8px*var(--fit,1));padding-top:calc(7px*var(--fit,1));border-top:1px dashed var(--line)}
.imp-t{font-size:10.5px;font-weight:600;color:var(--dim);letter-spacing:.5px;
 margin-bottom:7px}
.imp-list{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}
.imp{display:flex;align-items:flex-start;gap:calc(8px*var(--fit,1));background:var(--card);
 border:1px solid var(--line);border-radius:8px;
 padding:calc(7px*var(--fit,1)) calc(9px*var(--fit,1))}
.dir{width:calc(24px*var(--fit,1));height:calc(24px*var(--fit,1));border-radius:6px;
 display:inline-flex;align-items:center;justify-content:center;
 font-size:calc(12px*var(--fit,1));font-weight:700;flex:none}
.dir.up{background:#eaf6ee;color:#1e7e34}
.dir.down{background:#fdeee9;color:#c82333}
.dir.vol{background:#fff6e3;color:#b78103}
.dir.neu{background:var(--code);color:var(--dim)}
.imp-txt{min-width:0}
.imp-txt b{display:inline-block;font-size:12px;font-weight:600;color:var(--ink);
 line-height:1.5}
.imp-note{font-size:11px;color:var(--dim);line-height:1.55;margin-top:3px}
.imp-tag{display:inline-block;margin-left:7px;font-family:ui-monospace,SFMono-Regular,
 "Cascadia Mono",Consolas,monospace;font-size:10px;font-weight:600;padding:1px 6px;
 border-radius:4px}
.imp-tag.up{background:#eaf6ee;color:#1e7e34}
.imp-tag.down{background:#fdeee9;color:#c82333}
.imp-tag.vol{background:#fff6e3;color:#b78103}
.imp-tag.neu{background:var(--code);color:var(--dim)}
/* 监测指标：底部砖红警示行，照 reference 的 Key Sensitivity Indicator */
.sens{display:flex;gap:7px;align-items:flex-start;margin-top:calc(8px*var(--fit,1));padding-top:calc(7px*var(--fit,1));
 border-top:1px dashed var(--line);font-size:11.5px;color:var(--dim);line-height:1.6}
.sens-ic{width:17px;height:17px;border-radius:50%;background:var(--accent);color:#fff;
 font-size:10.5px;font-weight:700;display:inline-flex;align-items:center;
 justify-content:center;flex:none;margin-top:1px}
.sens b{color:var(--ink);font-weight:600}
/* 传导链：一环一格，每格带编号圆点，读起来像一条编号的传导管线 */
.flow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin-bottom:5px}
.flow:last-child{margin-bottom:0}
.hop{display:inline-flex;align-items:center;gap:6px;background:var(--chainbg);
 border:1px solid var(--chainline);color:var(--chain);border-radius:8px;
 padding:4px 10px 4px 5px;font-size:12.5px;line-height:1.45}
.hn{width:17px;height:17px;border-radius:50%;background:var(--chain);color:#fff;
 display:inline-flex;align-items:center;justify-content:center;font-size:10.5px;
 font-weight:700;flex:none}
.arw{color:var(--chain);opacity:.5;font-size:11px}
/* ── 深入研读的折叠 ──
   用原生 <details>/<summary>，**不写一行 JS**：零脚本、键盘可达、禁用 JS 也能展开。
   按钮的两种文案都在 HTML 里，只用 CSS 切换显示 —— 不靠 content 伪元素塞文字，
   这样读屏器和"页面内查找"都能拿到真实文字。 */
.fold{border-top:1px dashed var(--line);padding-top:11px}
.fold>summary{list-style:none;cursor:pointer;user-select:none;
 display:inline-flex;align-items:center;gap:7px;font-size:12.5px;
 color:var(--accent);background:var(--pill);border:1px solid var(--pillline);
 border-radius:999px;padding:5px 13px;transition:background .15s ease}
.fold>summary::-webkit-details-marker{display:none}
.fold>summary::marker{content:""}
.fold>summary:hover{background:var(--pillline)}
.fold>summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.fold>summary::after{content:"\\25be";font-size:10px;opacity:.75;
 transition:transform .15s ease}
.fold[open]>summary::after{transform:rotate(180deg)}
.cnt{color:var(--faint);font-size:11.5px}
.tg-close{display:none}
.fold[open] .tg-open{display:none}
.fold[open] .tg-close{display:inline}
.fold>ul{list-style:none;margin-top:12px}
.fold li{position:relative;padding-left:15px;margin-bottom:9px;font-size:13px;
 color:#33332f;line-height:1.72}
.fold li:last-child{margin-bottom:0}
.fold li::before{content:"*";position:absolute;left:2px;top:1px;color:var(--faint)}
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
.noread{padding:14px 16px;color:var(--faint);font-size:13px;display:flex;align-items:center;
 background:var(--card);border:1px solid var(--line);border-radius:var(--card-r);
 box-shadow:var(--card-sd)}
.empty{color:var(--faint);font-size:13px;background:var(--card);border:1px solid var(--line);
 border-radius:var(--card-r);padding:14px 17px;box-shadow:var(--card-sd)}
/* ── 面板里的历史归档（事件归档）：年 → 月 → 日期，三级都是原生 <details> ──
   日期平铺 60 天会很长，所以年和月都默认收起，只留几个标题行；展开某个月才列出
   那几天。零 JS（原生 details/summary），键盘也走得通。 */
.archive details{margin-bottom:4px}
.archive details:last-child{margin-bottom:0}
.archive summary{list-style:none;cursor:pointer;user-select:none;font-size:13.5px;
 color:var(--dim);display:flex;align-items:baseline;gap:8px;padding:5px 0}
.archive summary::-webkit-details-marker{display:none}
.archive summary::before{content:"\\25b8";font-size:10px;color:var(--faint);
 display:inline-block;transition:transform .15s ease}
.archive details[open]>summary::before{transform:rotate(90deg)}
.archive summary:hover{color:var(--accent)}
.archive summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.archive .arc-m summary{padding-left:16px;font-size:13px}
.archive summary .cnt{color:var(--faint);font-size:11.5px}
.archive .days{display:flex;flex-wrap:wrap;gap:6px;padding:6px 0 10px 26px}
.archive a{display:inline-block;min-width:32px;text-align:center;padding:4px 8px;
 background:#f1efe8;border-radius:6px;color:#4a4840;text-decoration:none;font-size:13px}
.archive a:hover{background:#e3dfd2}
.archive a.cur{background:var(--ink);color:#fff}
footer{margin-top:24px;color:var(--faint);font-size:12px;text-align:center;line-height:1.8}

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
@media(min-width:761px){
 .orig{margin-bottom:0}
 .orig>summary{display:none}
 .orig>*:not(summary){display:block;content-visibility:visible}
}

/* ── 底部固定功能栏 + 三个面板 ──
   三个入口：事件归档 / 我的收藏 / 设置。面板是固定在底部往上抽的抽屉，
   同一时间只开一个；点遮罩、点 ✕、按 Esc 都关。没有 JS 时靠 <noscript>
   里那段样式把三块直接摊在页面底部（归档照样能用）。
   注意：这里滚动的是**抽屉自己**（max-height + overflow-y），卡片里依旧没有内嵌滚动。 */
.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:40;display:flex;
 background:var(--card);border-top:1px solid var(--line);
 padding-bottom:env(safe-area-inset-bottom);
 box-shadow:0 -4px 18px -10px rgba(16,24,40,.35)}
.tabbar button{flex:1;appearance:none;background:none;border:0;cursor:pointer;
 font:inherit;color:var(--dim);padding:8px 4px 7px;
 display:flex;flex-direction:column;align-items:center;gap:3px;font-size:11.5px}
.tabbar .ic{font-size:17px;line-height:1}
.tabbar button:hover{color:var(--accent)}
.tabbar button[aria-expanded="true"]{color:var(--accent)}
.tabbar button:focus-visible{outline:2px solid var(--accent);outline-offset:-3px}
.scrim{position:fixed;inset:0;z-index:50;background:rgba(12,16,22,.45);
 opacity:0;pointer-events:none;transition:opacity .2s ease}
.scrim.on{opacity:1;pointer-events:auto}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:60;background:var(--card);
 border-radius:18px 18px 0 0;box-shadow:0 -12px 44px -14px rgba(16,24,40,.45);
 max-height:78vh;overflow-y:auto;overscroll-behavior:contain;
 padding:12px 16px calc(22px + env(safe-area-inset-bottom));
 transform:translateY(103%);transition:transform .24s ease;visibility:hidden}
.sheet.on{transform:none;visibility:visible}
.sheet-hd{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.sheet-hd h2{font-size:14px;letter-spacing:.5px;color:var(--dim);margin:0;flex:1}
.sheet-hd h2::after{content:none}
.sheet-x{appearance:none;background:none;border:0;cursor:pointer;color:var(--faint);
 font-size:15px;line-height:1;padding:4px 2px}
.sheet-x:hover{color:var(--accent)}
.favs{list-style:none;margin:0}
.favs li{display:flex;align-items:baseline;gap:8px;padding:9px 0;
 border-bottom:1px dashed var(--line)}
.favs li:last-child{border-bottom:0}
.favs a{flex:1;color:var(--ink);text-decoration:none;font-size:13.5px;font-weight:600;
 line-height:1.5}
.favs a:hover{color:var(--accent);text-decoration:underline}
.fav-meta{color:var(--faint);font-size:12px;white-space:nowrap}
.fav-del{appearance:none;background:none;border:0;cursor:pointer;color:var(--faint);
 font-size:13px;padding:2px 4px}
.fav-del:hover{color:#c0392b}
.favs-empty,.favs-note{color:var(--faint);font-size:12.5px;line-height:1.7}
.favs-empty{padding:4px 0 2px}
.favs-note{margin-top:10px;font-size:11.5px}
.opt{display:flex;gap:10px;align-items:flex-start;padding:10px 0;cursor:pointer}
.opt input{margin-top:3px;width:16px;height:16px;accent-color:var(--accent)}
.opt span{font-size:13.5px}
.opt em{display:block;font-style:normal;color:var(--faint);font-size:12px;margin-top:3px;
 line-height:1.6}
/* 收藏星标：贴在卡片右上角，不参与正文流 */
.fav{position:absolute;top:9px;right:11px;z-index:2;appearance:none;background:none;
 border:0;cursor:pointer;font-size:17px;line-height:1;color:var(--faint);padding:3px 4px;
 transition:color .15s ease,transform .15s ease}
.fav:hover{color:#c9805d;transform:scale(1.12)}
.fav.on{color:#b44322}
.fav:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
/* 桌面端：功能栏和抽屉都居中收窄，别横跨整个 1240px */
@media(min-width:761px){
 .tabbar{left:50%;right:auto;width:520px;margin-left:-260px;
  border:1px solid var(--line);border-bottom:0;border-radius:14px 14px 0 0}
 .sheet{left:50%;right:auto;width:720px;margin-left:-360px;border-radius:18px}
}
/* ── 手机：一屏一张卡，但页面照常整页上下滚 ──
   卡片本身做得够短（除"解析"外都在一屏里），所以横向翻卡时不用上下找内容；
   但页面**不锁高、不做卡内局部滚动** —— 卡内滚动读起来憋屈，长一点就让它长，
   整页顺滑上下滚，和普通网页一致。字数多的卡片靠页尾脚本把 --fit 从 1 往下调到
   0.65（下面所有字号都乘它），目标是"整张卡不超过一屏"；缩到底还超，就让它超，
   页面继续滚。 */
@media(max-width:760px){
 body{padding:12px 11px calc(74px + env(safe-area-inset-bottom))}
 .wrap{max-width:none}
 header{gap:8px}
 h1{font-size:18px}
 .date{font-size:12.5px}
 .badge{font-size:11px;padding:1px 8px}
 .note{font-size:12px;padding:7px 11px;margin:12px 0}
 .deck-track{gap:10px;padding:2px 0 6px}
 .card-body{grid-template-columns:1fr}          /* 单栏，自然高度，不截不滚 */
 .news{padding:12px 14px}
 .read{padding:0}
 /* 除深入研读/解析之外的一切字号都乘 --fit：整张卡超过一屏就整体缩，不做截断 */
 .seg{margin-bottom:calc(10px*var(--fit,1))}
 .sec-hd h4{font-size:calc(16px*var(--fit,1))}
 .sec-hd .sub{font-size:calc(11.5px*var(--fit,1))}
 .step{padding:calc(7px*var(--fit,1)) calc(9px*var(--fit,1)) calc(8px*var(--fit,1))}
 .stepn{font-size:calc(9.5px*var(--fit,1))}
 .stepl{font-size:calc(12px*var(--fit,1))}
  .step-panel{padding:calc(8px*var(--fit,1)) calc(9px*var(--fit,1))}
 .sf-lb{font-size:calc(10.5px*var(--fit,1))}
 .sf-v{font-size:calc(12px*var(--fit,1))}
 .imp-txt b{font-size:calc(12px*var(--fit,1))}
 .sens{font-size:calc(11.5px*var(--fit,1))}
 .tx,.fold li,.fact-list li{font-size:calc(14.5px*var(--fit,1));line-height:1.75}
 .seg.what .tx{font-size:calc(15.5px*var(--fit,1))}
 /* 深入研读 / 行动启示里的正文也要乘 --fit，不然卡片缩字号时这几块不跟着缩，
    fit 脚本就白跑了（手机一屏一张卡的预算会被这几张卡撑破）。 */
 .analogy p,.party .pv,.party .pvb,.take p{font-size:calc(12px*var(--fit,1))}
 .party .pn,.ind .in{font-size:calc(13px*var(--fit,1))}
 .party .pl,.party .pb,.ind .it{font-size:calc(10.5px*var(--fit,1))}
 .ind .is{font-size:calc(12px*var(--fit,1))}
 .deep-lb{font-size:calc(12px*var(--fit,1))}
 .take .tl{font-size:calc(13px*var(--fit,1))}
 .analogy .ae{font-size:calc(13.5px*var(--fit,1))}
 .step-hd span{font-size:calc(13.5px*var(--fit,1))}
 .imp-note,.step-hd .step-t{font-size:calc(11px*var(--fit,1))}
 .news a{font-size:calc(15px*var(--fit,1))}
 .src{font-size:calc(12px*var(--fit,1))}
 .raw{font-size:calc(12.5px*var(--fit,1));margin-top:calc(9px*var(--fit,1));
  padding-top:calc(9px*var(--fit,1))}
 .hop{font-size:calc(13px*var(--fit,1));gap:calc(6px*var(--fit,1));
  padding:calc(4px*var(--fit,1)) calc(9px*var(--fit,1)) calc(4px*var(--fit,1)) calc(5px*var(--fit,1))}
 .hn{width:calc(17px*var(--fit,1));height:calc(17px*var(--fit,1));
  font-size:calc(10.5px*var(--fit,1))}
 .fold>summary{font-size:calc(13.5px*var(--fit,1));
  padding:calc(8px*var(--fit,1)) calc(15px*var(--fit,1))}   /* 触摸目标别太小 */
 /* 公式不换行会被劈开读不懂，让它自己横滚。分式是 flex 布局，不能锁 pre。
    行内 code 也必须乘 --fit —— 不然整卡缩字号时，公式块停在 12.5px 会显得特大。 */
 .fml{font-size:calc(12.5px*var(--fit,1))}
 code{font-size:calc(12.5px*var(--fit,1))}
 .fml:not(.frac){white-space:pre;overflow-x:auto;-webkit-overflow-scrolling:touch}
 .deck-ctl{margin-top:6px;min-height:32px;gap:12px}
 .deck-btn{width:36px;height:36px}
 /* 归档：抽屉里再压一档，手机上大多直接点日期，标题行不用太大 */
 .archive summary{font-size:12.5px;padding:4px 0}
 .archive .arc-m summary{font-size:12px}
 .archive .days{padding:4px 0 6px 22px;gap:5px}
 .archive a{font-size:12px;padding:3px 7px;min-width:28px}
 .sheet{padding:10px 14px calc(20px + env(safe-area-inset-bottom))}
 .tabbar button{font-size:11px;padding:7px 4px 6px}
 footer{display:none}
}
/* 传导链在窄屏也保持横向流式：.flow 默认 flex-wrap:wrap，节点从左往右排、
   放不下自动换行，箭头维持 "→"（不再转成上下竖排）。 */
/* 晕动症用户把动效关掉后不该还在动 */
@media(prefers-reduced-motion:reduce){
 .fold>summary,.fold>summary::after,.fav{transition:none}
}
@media(prefers-color-scheme:dark){
 :root{--bg:#171612;--card:#1e1d18;--line:#322f28;--ink:#e9e6dc;--dim:#b1ab9c;
  --faint:#8a857a;--accent:#d98a63;--accent2:#7fb8a4;--chain:#7fb8a4;--chainbg:#1b2620;
  --chainline:#2e4238;--code:#292720;--warn:#e0b872;
  --pill:#2b2820;--pillline:#3f3a2d;
  --card-sd:0 1px 2px rgba(0,0,0,.4),0 12px 28px -12px rgba(0,0,0,.6);
  --card-sd-hi:0 2px 6px rgba(0,0,0,.5),0 20px 44px -14px rgba(0,0,0,.75)}
 .news{background:#211f1a}
 .archive a{background:#2a2821;color:#c7c2b4}
 .archive a:hover{background:#35322a}
 .badge{background:#2a2821}
 .note{background:#2a2418;border-color:#5c4a1e;color:#e0b872}
 .badge.rules{background:#3a2f14;color:#e0b872}
 .tx,.fold li,.fact-list li,.analogy p{color:#d8d4c9}
 .party .pv,.take p{color:#c4bfb2}
 .ind .it{background:#332c1e;color:#e0b872}
}"""


def esc(s: object) -> str:
    return html.escape(str(s or ""), quote=True)


# 传导链的环分隔符。校验层要求模型用 " → "，这里放宽到裸箭头，
# 少一个空格不该让整条链退化成一坨文字。
HOP_SEP = re.compile(r"\s*→\s*")
BRANCH_SEP = re.compile(r"[；;]\s*")
FORMULA_BLOCK_CHARS = 14     # 反引号里超过这么长就单独成等宽块，短的走行内 code
LEAD_MAX_CHARS = 26          # 概念名加粗只认开头这么长以内的 "："
# 经济传导脉络的 4 阶段拆法，是纯启发式（不烧 LLM，只是重组原文环）：
# 带涨跌/资产词的环算"资产影响"，从这类环里抠出"监测指标"。
ASSET_WORDS = ("收益率", "利率", "价格", "指数", "美元", "汇率", "股", "债", "汇",
               "油价", "金价", "黄金", "大宗", "涨", "跌", "升", "降", "↑", "↓",
               "上行", "走强", "走弱", "承压", "反弹", "回落", "新高", "新低",
               "贬值", "升值", "资产", "估值", "息差", "利差")
_CHANGE_RE = re.compile(r"[↑↓↗↘]+$|[上行走强走弱承压反弹回落回升新高新低涨跌升降]+$")
# 资产影响的涨跌方向，按关键字判断 —— 用于参考 UI 那种 绿涨/红跌/琥珀震荡 的色块。
# 顺序重要：两字词在前，避免"上行"先撞上"承压下行"里的"下行"。
_IMPACT_DIRS = (
    ("上行", "up"), ("走强", "up"), ("升值", "up"), ("反弹", "up"), ("新高", "up"),
    ("受益", "up"), ("↑", "up"), ("涨", "up"),
    ("下行", "down"), ("走弱", "down"), ("承压", "down"), ("贬值", "down"),
    ("新低", "down"), ("回落", "down"), ("↓", "down"), ("跌", "down"),
)
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
    """把 "A → B → C；D → E" 渲染成一环一格、编号管线的样子；"；" 后的支线另起一行。"""
    rows = []
    for branch in BRANCH_SEP.split(chain):
        hops = [h for h in (x.strip() for x in HOP_SEP.split(branch)) if h]
        if not hops:
            continue
        cells = f'<span class="arw">→</span>'.join(
            f'<span class="hop"><i class="hn">{i}</i>{esc(h)}</span>'
            for i, h in enumerate(hops, 1))
        rows.append(f'<div class="flow">{cells}</div>')
    return "".join(rows) or f'<div class="tx">{esc(chain)}</div>'


# 经济传导脉络固定拆 4 个阶段（横向 Step 步骤条）。
CHAIN_STAGES = 4


def _step_monitor(h: str) -> str:
    """从 "2Y美债收益率↑" 这类环里抠出要盯的指标名：去掉末尾涨跌符和顿号。"""
    m = _CHANGE_RE.sub("", h).strip("，。；,.;:： ")
    return m or h


def _impact_dir(h: str) -> str:
    for k, d in _IMPACT_DIRS:
        if k in h:
            return d
    return "vol"


def _chain_stages(chain: str) -> list[dict]:
    """把主链的环均分成 4 段，每段给出 触发源/传导逻辑/资产影响/监测指标。

    字段内容全部来自原链上的真实文字，只是按位置重组 —— **不烧 LLM**：
      - 触发源    = 段首环
      - 传导逻辑  = 整段环，用 " → " 连回去
      - 资产影响  = 段里带涨跌/资产词的环（没有就取末环）
      - 监测指标  = 从最后一个涨跌环里抠出的指标名
    环数不足 4 就返回空列表，调用方退回原来的横向流式渲染。
    """
    branches = [b for b in BRANCH_SEP.split(chain) if b.strip()]
    if not branches:
        return []
    hops = [h for h in (x.strip() for x in HOP_SEP.split(branches[0])) if h]
    n = len(hops)
    if n < CHAIN_STAGES:
        return []
    out: list[dict] = []
    base, rem = n // CHAIN_STAGES, n % CHAIN_STAGES
    pos = 0
    for i in range(CHAIN_STAGES):
        size = base + (1 if i < rem else 0)
        grp = hops[pos:pos + size]
        pos += size
        impact = [h for h in grp if any(w in h for w in ASSET_WORDS)] or [grp[-1]]
        out.append({
            # 标题要完整，不截断；长了靠 CSS 换到第二行
            "label": grp[0],
            "trigger": grp[0],
            "logic": " → ".join(grp),
            "impacts": [{"name": _step_monitor(h), "dir": _impact_dir(h)}
                        for h in impact],
            "monitor": _step_monitor(impact[-1]),
        })
    return out


def render_stages(chain: str, uid: str = "") -> str:
    """经济传导脉络的主干：顶部 4 个 Step 标签横排，详情面板固定在整个模块**下方**。

    选哪个 Step 用原生 radio（每个卡片一个独立 name），面板显隐靠 CSS `:has()`
    联动 —— 零 JS、键盘可用（方向键切换 Step）。点任意 Step，详情都在模块下方
    同一个区域切换，不跟着某个 Step 列走（照 dailybrief-ui 的 inspector 面板）。
    "；" 分隔的并行支线不进步骤条，原样排在下面，别丢内容；
    环数拆不出 4 段时整个退回 render_chain 的横向流式。
    """
    stages = _chain_stages(chain)
    if not stages:
        return render_chain(chain)
    grp = f"chain-{uid}" if uid else "chain"   # 每张卡一个 radio 组，跨卡不互斥
    steps = "".join(
        f'<label class="step"><input type="radio" name="{grp}" value="{i}"'
        f'{" checked" if i == 1 else ""}>'
        f'<span class="stepn">STEP 0{i}</span>'
        f'<span class="stepl">{esc(s["label"])}</span></label>'
        for i, s in enumerate(stages, 1))

    def _tag(d: str) -> str:
        return {"up": "看多 / 上行", "down": "看空 / 承压", "vol": "剧烈博弈震荡"}[d]

    def _panel(i: int, s: dict) -> str:
        imp_rows = "".join(
            f'<div class="imp"><span class="dir {im["dir"]}">'
            f'{"↗" if im["dir"] == "up" else "↘" if im["dir"] == "down" else "~"}</span>'
            f'<div class="imp-txt"><b>{esc(im["name"])}</b>'
            f'<span class="imp-tag {im["dir"]}">{_tag(im["dir"])}</span></div></div>'
            for im in s["impacts"])
        return (f'<section class="step-panel panel-{i}">'
                f'<div class="step-hd"><b>{i}</b><span>{esc(s["label"])}</span></div>'
                f'<div class="step-grid">'
                f'<div class="sf sf-trigger"><div class="sf-lb">输入触发源</div>'
                f'<div class="sf-v">{esc(s["trigger"])}</div></div>'
                f'<div class="sf sf-logic"><div class="sf-lb">底层传导机理</div>'
                f'<div class="sf-v">{esc(s["logic"])}</div></div></div>'
                f'<div class="impacts"><div class="imp-t">'
                f'关键受波及大类资产定价</div>'
                f'<div class="imp-list">{imp_rows}</div></div>'
                f'<div class="sens"><span class="sens-ic">!</span><div>'
                f'<b>关键敏感监测变量：</b><span>{esc(s["monitor"])}</span></div></div>'
                f'</section>')

    panels = "".join(_panel(i, s) for i, s in enumerate(stages, 1))
    extra = [b for b in BRANCH_SEP.split(chain) if b.strip()][1:]
    extra_html = render_chain("；".join(extra)) if extra else ""
    return (f'<div class="steps">{steps}</div>'
            f'<div class="step-panels">{panels}</div>{extra_html}')


# 资产影响方向的配色与文案。键是 AI 输出里的 direction，值分别是 CSS 后缀、
# 方向符号、中文标签 —— 三者必须同步改，页面靠它们决定绿涨/红跌/琥珀震荡。
DIRECTION_STYLE = {
    "up":       ("up",  "↗", "看多 / 上行"),
    "down":     ("down", "↘", "看空 / 承压"),
    "volatile": ("vol", "~", "剧烈博弈震荡"),
    "neutral":  ("neu", "—", "中性"),
}


def render_structured_stages(nodes: list[dict], uid: str = "") -> str:
    """金融传导全景脉络（结构化）：直接读 AI 给的 4 段，不再从平文本里猜。

    这是 2026-09-11 的主路径。与 render_stages() 的区别很关键：那个是把一根平文本
    **按位置硬切成 4 段**（旧归档的回退路径），切出来的「触发源 / 传导机理 / 资产影响」
    同源，渲染出来是同一句话。这里每个字段都是模型分别写的，所以三者各不相同。

    选 Step 仍用原生 radio（每张卡一个独立 name），详情面板留在整个模块下方统一切换，
    零 JS、键盘方向键可用 —— 与回退路径共用 .steps/.step-panel/.sf/.imp/.sens 一套样式。
    """
    grp = f"chain-{uid}" if uid else "chain"   # 每张卡一个 radio 组，跨卡不互斥
    steps = "".join(
        f'<label class="step"><input type="radio" name="{grp}"'
        f' value="{n["step"]}"{" checked" if i == 0 else ""}>'
        f'<span class="stepn">STEP {n["step"]:02d}</span>'
        f'<span class="stepl">{esc(n["name"])}</span></label>'
        for i, n in enumerate(nodes))

    def _panel(n: dict) -> str:
        imp_rows = "".join(
            f'<div class="imp"><span class="dir {DIRECTION_STYLE[d][0]}">'
            f'{DIRECTION_STYLE[d][1]}</span>'
            f'<div class="imp-txt"><b>{esc(im["asset"])}</b>'
            f'<span class="imp-tag {DIRECTION_STYLE[d][0]}">'
            f'{DIRECTION_STYLE[d][2]}</span>'
            f'<p class="imp-note">{esc(im["note"])}</p></div></div>'
            for im in n["assetImpacts"]
            for d in (im["direction"],))
        # 时滞放在面板头卡右侧，跟参考站的 inspector 面板一致
        return (f'<section class="step-panel panel-{n["step"]}">'
                f'<div class="step-hd"><b>{n["step"]}</b>'
                f'<span>{esc(n["name"])}</span>'
                f'<i class="step-t">时滞 {esc(n["timeHorizon"])}</i></div>'
                f'<div class="step-grid">'
                f'<div class="sf sf-trigger"><div class="sf-lb">输入触发源</div>'
                f'<div class="sf-v">{esc(n["trigger"])}</div></div>'
                f'<div class="sf sf-logic"><div class="sf-lb">底层传导机理</div>'
                f'<div class="sf-v">{esc(n["mechanism"])}</div></div></div>'
                f'<div class="impacts"><div class="imp-t">'
                f'关键受波及大类资产定价</div>'
                f'<div class="imp-list">{imp_rows}</div></div>'
                f'<div class="sens"><span class="sens-ic">!</span><div>'
                f'<b>关键敏感监测变量：</b><span>{esc(n["keySensitivity"])}</span>'
                f'</div></div></section>')

    panels = "".join(_panel(n) for n in nodes)
    return f'<div class="steps">{steps}</div><div class="step-panels">{panels}</div>'


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


# 旧归档（五字段时代）没有 notes，render_card 里回落渲染 盯什么/概念 两行，
# 免得重跑丢内容 —— 字段标签直接写在 render_card 里了。


def _collapsible(open_label: str, close_label: str, body: str,
                 tail: str = "", cls: str = "fold") -> str:
    """默认收起的折叠块。原生 <details>/<summary>，**不需要一行 JS**。

    两种按钮文案都写进 HTML、只靠 CSS 切换显示，所以禁用 CSS、用读屏器、
    或者用浏览器的"页面内查找"时读到的都是真实文字，而不是空按钮。

    [预留·行测解读模式] 将来想在同一张卡上再挂一个视角（例如把这条新闻改写成
    一道因果推理题 + 答案），不用碰交互代码，直接复用这个函数：
        if ins.get("quiz"):
            segs.append(_collapsible(
                "展开行测解读", "收起行测解读",
                "".join(render_note(q) for q in ins["quiz"])))
    同一张卡上挂几个折叠块互不影响（<details> 各自独立），样式沿用 .fold；
    要让它们互斥展开（手风琴）就给每个 <details> 加同一个 name 属性。
    """
    return (f'<details class="{cls}"><summary>'
            f'<span class="tg tg-open">{esc(open_label)}</span>'
            f'<span class="tg tg-close">{esc(close_label)}</span>'
            f'{tail}</summary>{body}</details>')


def _day_label(iso: str) -> str:
    """'2026-09-09' → '9月9日'。解析不了就原样返回，渲染不该因为没有日期而崩。"""
    try:
        _, m, d = iso.split("-")
        return f"{int(m)}月{int(d)}日"
    except (ValueError, AttributeError):
        return str(iso or "")


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
            elif "一" <= ch <= "鿿":       # CJK 统一汉字
                cjk += 1
    return latin >= 12 and latin > cjk


def _sec(title: str, sub: str, body: str, cls: str) -> str:
    """一张研读卡：色点 + 衬线标题 +（可选）副题 + 正文。照 dailybrief-ui 的头卡。"""
    sub_html = f'<p class="sub">{sub}</p>' if sub else ""
    return (f'<section class="seg {cls}"><div class="sec-hd">'
            f'<span class="dot" aria-hidden="true"></span>'
            f'<div><h4>{title}</h4>{sub_html}</div></div>{body}</section>')


def _notes_fold(notes: object) -> str:
    """解析折叠块。解析动辄五到八条、每条两三行，全铺开会把卡片撑得看不到下一条，
    所以默认收起 —— 卡片回到一眼能扫完的高度，想看再点开。"""
    if isinstance(notes, str):
        notes = [ln for ln in notes.splitlines() if ln.strip()]
    items = [str(n) for n in (notes or []) if str(n).strip()]
    if not items:
        return ""
    lis = "".join(render_note(n) for n in items)
    return _collapsible("展开解析", "收起解析", f"<ul>{lis}</ul>",
                        tail=f'<span class="cnt">{len(items)} 条</span>')


def _deep_blocks(ins: dict) -> str:
    """深入研读与博弈透视的三小块：历史参照 / 博弈各方 / 前瞻红线指标。

    参考站还有「表面直觉 vs 机构内核」，用户明确不要，这里不渲染。
    """
    blocks: list[str] = []

    hist = ins.get("historicalAnalogy") or {}
    if isinstance(hist, dict) and (hist.get("event") or hist.get("comparison")):
        year = (f'<span class="ayr">[{esc(hist.get("year"))}]</span>'
                if hist.get("year") else "")
        blocks.append(
            '<div class="deep-sub"><div class="deep-lb">历史参照系与经验校准</div>'
            f'<div class="analogy"><div class="ay">'
            f'<span class="ae">{esc(hist.get("event"))}</span>{year}</div>'
            f'<p>{esc(hist.get("comparison"))}</p></div></div>')

    parties = [p for p in (ins.get("gameTheoryStakeholders") or [])
               if isinstance(p, dict)]
    if parties:
        cards = "".join(
            f'<div class="party"><div class="pn">'
            f'<span>{esc(p.get("party"))}</span><i>0{i}</i></div>'
            f'<span class="pl">台前表态立场</span>'
            f'<p class="pv">{esc(p.get("stance"))}</p>'
            f'<span class="pb">不妥协的真实底牌</span>'
            f'<p class="pvb">{esc(p.get("bottomLine"))}</p></div>'
            for i, p in enumerate(parties, 1))
        blocks.append('<div class="deep-sub">'
                      '<div class="deep-lb">博弈各方的台前立场与真实底牌</div>'
                      f'<div class="parties">{cards}</div></div>')

    inds = [x for x in (ins.get("forwardIndicators") or []) if isinstance(x, dict)]
    if inds:
        rows = "".join(
            f'<div class="ind"><div class="ih">'
            f'<span class="in">{esc(x.get("indicator"))}</span>'
            f'<span class="it">红线 {esc(x.get("threshold"))}</span></div>'
            f'<div class="is">{esc(x.get("significance"))}</div></div>'
            for x in inds)
        blocks.append('<div class="deep-sub">'
                      '<div class="deep-lb">需持续追踪的前瞻红线指标</div>'
                      f'<div class="inds">{rows}</div></div>')

    return "".join(blocks)


# 行动启示的三个视角。键是 AI 输出里的 takeaways 字段名。
TAKE_VIEWS = (("investor", "投资者视角"),
              ("industry", "实体与从业者"),
              ("personal", "个人与家庭生活"))


def _takes_block(ins: dict) -> str:
    """行动启示与认知内化：投资者 / 实体与从业者 / 个人与家庭 三栏。

    内层栅格类名是 take-list，**不能**跟研读卡的 cls（takes）同名 ——
    同名会让卡头也被当成栅格子项，见 CSS 里那段注释。
    """
    take = ins.get("takeaways") or {}
    if not isinstance(take, dict):
        return ""
    cells = "".join(
        f'<div class="take"><div class="tl">{label}</div>'
        f'<p>{esc(take.get(key))}</p></div>'
        for key, label in TAKE_VIEWS if str(take.get(key) or "").strip())
    return f'<div class="take-list">{cells}</div>' if cells else ""


def _legacy_segs(ins: dict, cid: str) -> list[str]:
    """旧归档（2026-09-11 之前）的回退渲染：what / why / chain(平文本) / notes / watch / term。

    chain 是一根平文本，只能用 _chain_stages() 按位置硬切成 4 段假装结构化 ——
    这正是这次改版要修的毛病，但**历史页面得照原样显示**，重跑不能让它们掉内容。
    """
    segs: list[str] = []
    if ins.get("what"):
        segs.append(_sec("发生了什么", "",
                         f'<div class="tx">{esc(ins["what"])}</div>', "what"))
    if ins.get("why"):
        segs.append(_sec("市场为什么在意", "",
                         f'<div class="tx">{esc(ins["why"])}</div>', "why"))

    chain = str(ins.get("chain") or "")
    fold = _notes_fold(ins.get("notes"))
    if chain:
        segs.append(_sec("经济传导脉络", "", render_stages(chain, cid) + fold, "chain"))
    elif fold:
        segs.append(_sec("解析", "", fold, "notes"))
    # 更早的五字段时代还可能有 watch（盯什么）/ term（概念），原样补两张卡免得掉内容
    segs += [
        _sec(label, "", f'<div class="tx">{esc(ins.get(key))}</div>', "")
        for key, label in (("watch", "盯什么"), ("term", "概念"))
        if ins.get(key)
    ]
    return segs


def render_card(card: dict, cat: str, day_label: str = "", cid: str = "") -> str:
    """一张卡片。

    day_label 只给"跨天滑过来"的卡片（前后一天）—— 卡组把几天的卡片接成了一条，
    卡片得自报这是哪天。cid 是这张卡当天的稳定编号（`<article id="c-<cid>">`），
    收藏列表就是靠这个锚点跳回具体某一张卡的。
    """
    ins = card.get("insight") or {}
    label = CATEGORY_LABELS.get(cat, "")
    chips = [f'<div class="catpill" data-cat="{esc(cat)}">{esc(label)}</div>'
             if cat else ""]
    if day_label:
        chips.append(f'<span class="daychip">{esc(day_label)}</span>')
    chip = f'<div class="chips">{"".join(chips)}</div>' if any(chips) else ""

    meta = " · ".join(x for x in (esc(card.get("source")),
                                  esc(card.get("published"))) if x)
    url = esc(card.get("url"))
    link = (f'<a class="t" href="{url}" target="_blank" '
            f'rel="noopener noreferrer">{esc(card.get("title"))}</a>')
    raw = str(card.get("raw_summary") or "")
    raw_html = f'<div class="raw">{esc(raw[:150])}</div>' if raw else ""
    if _is_english(card.get("title"), raw):
        # 英文源：标题和摘要都收进这个 details，手机端默认只显示中文解读，
        # 点「查看英文原文」才展开；桌面端由 CSS 直接摊开（见 .orig 那段）。
        news_body = (f'<details class="orig"><summary>查看英文原文</summary>'
                     f'{link}{raw_html}</details><div class="src">{meta}</div>')
    else:
        news_body = f'{link}<div class="src">{meta}</div>{raw_html}'

    star = (f'<button class="fav" type="button" data-cid="{esc(cid)}" data-id="{url}"'
            f' aria-pressed="false" aria-label="收藏这条" title="收藏这条">☆</button>')
    left = f'<div class="news">{chip}{star}{news_body}</div>'
    id_attr = f' id="c-{esc(cid)}"' if cid else ""

    if not ins:
        # 规则模式：明确说没有解读，不拿新闻摘要冒充
        return (f'<article{id_attr} data-cat="{esc(cat)}"><div class="card-body">{left}'
                f'<div class="noread">今日无 AI 解读（规则模式）</div></div></article>')

    # 四张研读卡，头卡样式参照 dailybrief-ui：色点 + 衬线标题 +（可选）副题。
    nodes = ins.get("transmissionChain") or []
    if not nodes:
        # 旧归档回退：2026-09-11 之前的数据没有结构化传导链，走 what/why/chain/notes
        segs = _legacy_segs(ins, cid)
    else:
        segs = []

        # 1. 30秒事实内核
        facts = [str(f) for f in (ins.get("summary30s") or []) if str(f).strip()]
        if facts:
            lis = "".join(f'<li>{esc(f)}</li>' for f in facts)
            segs.append(_sec("30秒事实内核", "",
                             f'<ul class="fact-list">{lis}</ul>', "facts"))

        # 2. 金融传导全景脉络：结构化 4 段 + 底部解析折叠
        body = render_structured_stages(nodes, cid) + _notes_fold(ins.get("notes"))
        segs.append(_sec("金融传导全景脉络", "", body, "chain"))

        # 3. 深入研读与博弈透视
        deep = _deep_blocks(ins)
        if deep:
            segs.append(_sec("深入研读与博弈透视", "", deep, "deep"))

        # 4. 行动启示与认知内化
        takes = _takes_block(ins)
        if takes:
            segs.append(_sec("行动启示与认知内化", "", takes, "takes"))

    return (f'<article{id_attr} data-cat="{esc(cat)}"><div class="card-body">{left}'
            f'<div class="read">{"".join(segs)}</div></div></article>')



# 滑卡导航 + 底部功能栏的内嵌 JS。滑卡这部分**刻意只做三件事**：翻上一张/下一张
# （跨天也是同一条轨道，不用特判）、计数器、手机上把塞不进一屏的卡片按 --fit 缩到位；
# 后一个 IIFE 管三个抽屉的开合、收藏增删（localStorage）和设置项。
# 其余交互（解析折叠、英文原文折叠、外链、深色模式）全在原生 HTML/CSS 里。
# 脚本放在页面末尾 <script> 里；即便它没跑（比如被禁），用户仍能手动左右滑卡片，
# 归档和英文原文靠 <noscript> 里的样式摊开，只是会停在轨道最左边那张（前一天的卡）。
DECK_JS = r"""<script>
(function () {
  var PAGE = document.documentElement.getAttribute('data-page') || '';
  var PHONE = window.matchMedia('(max-width:760px)');

  // 手机端：卡片内容长度是模型写的、长短不可控 —— 与其截断文字，不如把卡内字号
  // 缩一点，让整张卡不超过一屏（除"解析"外都塞进一屏，横向翻卡时不用上下找）。
  // 页面本身照常整页纵向滚动，**不做卡内局部滚动**；缩到下限 0.60 还超就让它超，
  // 页面继续滚就是了。
  function budget() {
    var used = 0, sel = ['header', '.deck-ctl'];
    for (var q = 0; q < sel.length; q++) {
      var e = document.querySelector(sel[q]);
      if (e) used += e.offsetHeight;
    }
    return Math.max(320, window.innerHeight - used - 26);
  }
  function fit(article, force) {
    if (article.dataset.fitDone && !force) return;
    if (article.querySelector('.fold[open]')) return;   // 展开态量不准，保持原样
    var s = 1, limit = budget();
    article.style.setProperty('--fit', '1');
    while (s > 0.60 && article.offsetHeight > limit) {
      s -= 0.04;
      article.style.setProperty('--fit', s.toFixed(2));
    }
    article.dataset.fitDone = '1';
  }

  function init() {
    var track = document.querySelector('.deck-track');
    if (!track || track.children.length === 0) return;
    var slides = Array.prototype.slice.call(track.children);
    var prev = document.querySelector('.deck-prev');
    var next = document.querySelector('.deck-next');
    var pos = document.querySelector('.deck-pos');
    var N = slides.length;

    // 轨道里是[前一天][当天][后一天]的卡片，打开时先停在"当天"的第一张，
    // 而不是轨道最左边那张（那可能是前一天）。用 rect 差值定位，不依赖 offsetParent。
    var start = 0;
    for (var k = 0; k < N; k++) {
      if (slides[k].getAttribute('data-day') === PAGE) { start = k; break; }
    }
    // 从收藏列表点进来时带着 #c-<day>-<i>，直接落到那一张
    var want = (location.hash || '').indexOf('#c-') === 0 ? location.hash.slice(3) : '';
    if (want) {
      for (var h = 0; h < N; h++) {
        var art = slides[h].querySelector('article');
        if (art && art.id === 'c-' + want) { start = h; break; }
      }
    }
    if (start > 0) {
      track.scrollLeft += slides[start].getBoundingClientRect().left -
                          track.getBoundingClientRect().left;
    }

    function index() {
      var i = Math.round(track.scrollLeft / (track.clientWidth || 1));
      return Math.max(0, Math.min(N - 1, i));
    }
    function update() {
      var i = index(), s = slides[i];
      var n = parseInt(s.getAttribute('data-n'), 10) || N;
      var j = parseInt(s.getAttribute('data-i'), 10) || (i + 1);
      if (pos) pos.textContent = j + '/' + n;          // 当天内部的第几张
      if (prev) prev.disabled = (i <= 0);
      if (next) next.disabled = (i >= N - 1);
      if (PHONE.matches) {
        var a = s.querySelector('article');
        if (a) fit(a);
      }
    }
    function go(i) {
      i = Math.max(0, Math.min(N - 1, i));
      track.scrollTo({ left: i * track.clientWidth, behavior: 'smooth' });
    }
    if (prev) prev.addEventListener('click', function () { go(index() - 1); });
    if (next) next.addEventListener('click', function () { go(index() + 1); });
    track.addEventListener('scroll', update, { passive: true });
    track.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowRight') { go(index() + 1); e.preventDefault(); }
      if (e.key === 'ArrowLeft')  { go(index() - 1); e.preventDefault(); }
    });

    var timer;
    window.addEventListener('resize', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        // 屏高变了，之前缩好的 --fit 全部作废，重新量一遍
        slides.forEach(function (s) {
          var a = s.querySelector('article');
          if (a) fit(a, true);
        });
        update();
      }, 150);
    }, { passive: true });

    update();
    // 当前这张必须在第一次绘制前就量好；其余几张趁空闲量，滑过去时字不会跳一下
    if (PHONE.matches) {
      var idle = window.requestIdleCallback || function (f) { return setTimeout(f, 120); };
      idle(function () {
        slides.forEach(function (s) {
          var a = s.querySelector('article');
          if (a) fit(a);
        });
      });
    }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

// ── 底部功能栏：事件归档 / 我的收藏 / 设置 ──
// 收藏和设置都存 localStorage。收藏目前只在这台设备上，锚点是卡片的 id
// （`<day>.html#c-<day>-<i>`），点收藏列表里的一条会跳回那一天那张卡。
// 以后接小程序和账号时，把这层存储换成远端接口即可，界面不用动。
(function () {
  var FAV_KEY = 'dailybrief.favs', OPT_KEY = 'dailybrief.showOrig';
  function store(k, v) {
    try {
      if (v === undefined) return window.localStorage.getItem(k);
      window.localStorage.setItem(k, v);
    } catch (e) { /* 隐私模式或禁用存储：当作没收藏过，别让页面挂掉 */ }
    return null;
  }
  function favs() {
    try { return JSON.parse(store(FAV_KEY) || '[]') || []; } catch (e) { return []; }
  }

  var list = document.getElementById('favs'), empty = document.getElementById('favs-empty');

  function paint() {
    if (!list) return;
    var items = favs(), on = {};
    items.forEach(function (f) { on[f.id] = 1; });
    list.innerHTML = '';
    items.slice().reverse().forEach(function (f) {
      var li = document.createElement('li'), a = document.createElement('a');
      a.href = f.day + '.html#c-' + f.cid;            // 直接跳回那张卡
      a.textContent = f.label || f.title || f.day;
      var meta = document.createElement('span');
      meta.className = 'fav-meta';
      meta.textContent = f.day + (f.src ? ' · ' + f.src : '');
      var del = document.createElement('button');
      del.type = 'button';
      del.className = 'fav-del';
      del.textContent = '✕';
      del.setAttribute('aria-label', '取消收藏');
      del.addEventListener('click', function () {
        save(items.filter(function (x) { return x.id !== f.id; }));
      });
      li.appendChild(a); li.appendChild(meta); li.appendChild(del);
      list.appendChild(li);
    });
    if (empty) empty.style.display = items.length ? 'none' : '';
    var btns = document.querySelectorAll('.fav');   // 卡片右上角的星标跟着状态走
    for (var i = 0; i < btns.length; i++) {
      var fav = !!on[btns[i].getAttribute('data-id')];
      btns[i].textContent = fav ? '★' : '☆';
      btns[i].setAttribute('aria-pressed', fav ? 'true' : 'false');
      btns[i].className = fav ? 'fav on' : 'fav';
    }
  }
  function save(items) { store(FAV_KEY, JSON.stringify(items)); paint(); }

  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.fav') : null;
    if (!b) return;
    var items = favs(), id = b.getAttribute('data-id');
    var kept = items.filter(function (x) { return x.id !== id; });
    if (kept.length === items.length) {
      var art = b.closest('article'), slide = b.closest('.deck-slide');
      var link = art && art.querySelector('.news a.t');
      var what = art && art.querySelector('.seg.what .tx');
      var src = art && art.querySelector('.src');
      kept.push({
        id: id,
        cid: b.getAttribute('data-cid'),
        day: slide ? slide.getAttribute('data-day') : '',
        // 中文解读的头一句当列表标题：英文源卡片标题是英文，列表里不好认
        label: what ? what.textContent.replace(/\s+/g, ' ').slice(0, 44) : '',
        title: link ? link.textContent : '',
        src: src ? src.textContent.split(' · ')[0] : '',
        url: link ? link.href : id
      });
    }
    save(kept);
  });

  // 设置：默认展开英文原文。手机端才有折叠；桌面端永远摊开 —— 由 JS 强制
  // `open`，不赌浏览器对未 open 的 details 用 display:none 还是 content-visibility
  // 隐藏（后者光靠 CSS 覆盖压不住，英文原文会被吞成空白）。
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

  // 抽屉：一次只开一个，点遮罩 / ✕ / Esc 关掉
  var scrim = document.querySelector('.scrim');
  var tabs = document.querySelectorAll('.tabbar button');
  function mark(name) {
    for (var i = 0; i < tabs.length; i++) {
      tabs[i].setAttribute('aria-expanded',
        tabs[i].getAttribute('data-sheet') === name ? 'true' : 'false');
    }
  }
  function close() {
    var open = document.querySelectorAll('.sheet.on');
    for (var i = 0; i < open.length; i++) open[i].className = 'sheet';
    if (scrim) scrim.className = 'scrim';
    mark('');
  }
  function open(name) {
    close();
    var s = document.getElementById('sheet-' + name);
    if (!s) return;
    s.className = 'sheet on';
    if (scrim) scrim.className = 'scrim on';
    mark(name);
  }
  for (var t = 0; t < tabs.length; t++) {
    tabs[t].addEventListener('click', function () {
      var name = this.getAttribute('data-sheet');
      var s = document.getElementById('sheet-' + name);
      if (s && s.className.indexOf('on') >= 0) close(); else open(name);
    });
  }
  var xs = document.querySelectorAll('.sheet-x');
  for (var x = 0; x < xs.length; x++) xs[x].addEventListener('click', close);
  if (scrim) scrim.addEventListener('click', close);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') close();
  });

  paint();
  applyOrig();
})();
</script>"""


def render_archive(dates: list[str], current: str) -> str:
    """事件归档：年 → 月 → 日期三级，年和月都是**默认收起**的 <details>。

    放在底部功能栏的"事件归档"抽屉里，所以这里只出内容，标题由抽屉自己写。
    日期只写"几号"（月份在上一级标题里），今天那张仍然高亮。
    """
    groups: dict[str, dict[str, list[str]]] = {}
    for d in dates[:ARCHIVE_DAYS]:
        y, m, _ = d.split("-")
        groups.setdefault(y, {}).setdefault(m, []).append(d)
    if not groups:
        return ""

    years = []
    for y in sorted(groups, reverse=True):
        months = []
        for m in sorted(groups[y], reverse=True):
            days = sorted(groups[y][m], reverse=True)
            links = "".join(
                f'<a href="{d}.html" title="{d}"'
                f'{" class=\"cur\"" if d == current else ""}>{esc(d[8:10])}</a>'
                for d in days)
            months.append(f'<details class="arc-m"><summary>{int(m)} 月'
                          f'<span class="cnt">{len(days)} 天</span></summary>'
                          f'<div class="days">{links}</div></details>')
        years.append(f'<details class="arc-y"><summary>{esc(y)} 年</summary>'
                     f'{"".join(months)}</details>')
    return f'<div class="archive">{"".join(years)}</div>'


def _day_cards(payload: dict) -> list[tuple[str, dict]]:
    """一天的全部卡片，按板块顺序拉平：(板块, 卡片)。"""
    return [(cat, card) for cat in CATEGORIES for card in (payload.get(cat) or [])]


def render_page(payload: dict, dates: list[str], current: str,
                older: dict | None = None, newer: dict | None = None) -> str:
    """older / newer 是相邻可用日期的 payload（没有就 None）。

    卡组把[前一天][当天][后一天]接成一条连续轨道：滑到当天最后一张继续往前是
    后一天，往回滑是前一天，不会卡在当天两端。计数器按**当天**算，别的日期的
    卡片自带日期标签。
    """
    mode = payload.get("mode", "llm")
    badge = ('<span class="badge rules">规则模式</span>' if mode == "rules"
             else '<span class="badge">AI 解读</span>')
    # 顶部品牌区：logo（勢字）+ 知势 DailyBrief + 副标题，照着 dailybrief-ui 的
    # masthead：logo 是深色圆角块、DailyBrief 缩小变灰、副标题一行在下面。
    cn, _, en = BRAND.partition(" ")
    sub_html = f'<p class="sub">{esc(TAGLINE)}</p>' if TAGLINE else ""
    header = (f'<header><span class="logo" aria-hidden="true">勢</span>'
              f'<div class="hd"><h1>{esc(cn)} <span class="en">{esc(en or "DailyBrief")}</span></h1>'
              f'{sub_html}</div>'
              f'<span class="date">{esc(payload.get("date"))}</span>{badge}</header>')

    note = ""
    if mode == "rules":
        reason = payload.get("fallback_reason") or "LLM 不可用"
        note = (f'<div class="note">今日为规则模式：只按来源权重与时间筛出了条目，'
                f'<b>没有 AI 解读</b>。原因：{esc(reason)}</div>')

    # 每张卡片自带"所属板块"（不再有板块标题当锚点），跨天的还带"这是哪天"。
    slides: list[str] = []
    start = 0          # 当天第一张在轨道里的下标：打开页面要停在这儿
    today_n = 0
    idx = dates.index(current)
    older_day = dates[idx + 1] if idx + 1 < len(dates) else ""
    newer_day = dates[idx - 1] if idx > 0 else ""
    for day, day_payload in ((older_day, older), (current, payload), (newer_day, newer)):
        if not day_payload:
            continue
        cards = _day_cards(day_payload)
        label = "" if day == current else _day_label(day)
        if day == current:
            start, today_n = len(slides), len(cards)
        if not cards:
            slides.append(f'<div class="deck-slide" data-day="{esc(day)}">'
                          f'<div class="deck-empty">{esc(label or day)}'
                          f'无入选条目</div></div>')
            continue
        slides += [
            f'<div class="deck-slide" data-day="{esc(day)}" data-i="{i}" '
            f'data-n="{len(cards)}">'
            f'{render_card(card, cat, label, f"{day}-{i}")}</div>'
            for i, (cat, card) in enumerate(cards, 1)
        ]

    ctl = (f'<div class="deck-ctl" aria-label="卡片导航">'
           f'<button class="deck-btn deck-prev" type="button" aria-label="上一张"'
           f'{" disabled" if start == 0 else ""}>‹</button>'
           f'<span class="deck-pos">1/{today_n or len(slides)}</span>'
           f'<button class="deck-btn deck-next" type="button" '
           f'aria-label="下一张">›</button></div>')
    # 计数器和按钮在卡片**下面**：手机上一屏一张卡，卡片是阅读主体。
    body = (f'<div class="deck"><div class="deck-track" tabindex="0">'
            f'{"".join(slides)}</div>{ctl}'
            f'</div>')

    nav = render_archive(dates, current)

    # 底部固定功能栏的三个抽屉：事件归档 / 我的收藏 / 设置。
    # 归档是服务端渲染好的（没 JS 也能看），收藏和设置靠页面脚本填。
    # 没有 JS 时 <noscript> 那段 CSS 会把三个抽屉直接摊在页面底部，归档照样能用。
    panels = f"""<div class="scrim"></div>
<section class="sheet" id="sheet-archive" aria-label="事件归档">
<div class="sheet-hd"><h2>事件归档</h2><button class="sheet-x" type="button" aria-label="关闭">✕</button></div>
{render_archive(dates, current)}</section>
<section class="sheet" id="sheet-favs" aria-label="我的收藏">
<div class="sheet-hd"><h2>我的收藏</h2><button class="sheet-x" type="button" aria-label="关闭">✕</button></div>
<ul class="favs" id="favs"></ul>
<p class="favs-empty" id="favs-empty">还没有收藏。卡片右上角的 ☆ 点一下，这条就存在这里。</p>
<p class="favs-note">收藏只存在这台设备的浏览器里，换设备或清缓存就没了；以后接上账号会跟着账号走，段落划线收藏也一起做。</p>
</section>
<section class="sheet" id="sheet-set" aria-label="设置">
<div class="sheet-hd"><h2>设置</h2><button class="sheet-x" type="button" aria-label="关闭">✕</button></div>
<label class="opt"><input type="checkbox" id="opt-orig">
<span>默认展开英文原文<em>关着的时候，英文源的卡片手机端只显示中文解读，点「查看英文原文」才展开。桌面端一直显示原文。</em></span></label>
</section>
<nav class="tabbar" aria-label="功能栏">
<button type="button" data-sheet="archive" aria-expanded="false"><span class="ic">▤</span>事件归档</button>
<button type="button" data-sheet="favs" aria-expanded="false"><span class="ic">★</span>我的收藏</button>
<button type="button" data-sheet="set" aria-expanded="false"><span class="ic">⚙</span>设置</button>
</nav>
<noscript><style>.scrim,.tabbar{{display:none}}
.sheet{{position:static;transform:none;visibility:visible;max-height:none;margin-top:14px;
border:1px solid var(--line);border-radius:var(--card-r)}}</style></noscript>"""

    # 手机上的注意点，都踩过：
    # - charset 必须在 <head> 最前面（早于 title），否则中文有几率乱码
    # - viewport 不写 maximum-scale/user-scalable，锁缩放会让公式没法放大看
    # - theme-color 让 Safari/Chrome 的地址栏跟着页面配色，加到主屏后不突兀
    # - apple-mobile-web-app-* 让 iOS「添加到主屏幕」后像个独立 App
    # - data-page 给滑卡脚本认"当天"，初始定位到当天的第一张
    return f"""<!DOCTYPE html>
<html lang="zh-CN" data-page="{esc(payload.get("date"))}"><head>
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
<style>{CSS}</style></head><body><div class="wrap">
{header}
{note}
{body}
<footer>{esc(FOOTER)}<br>
条目来自各源公开 RSS 与 API，解读由 Claude 生成，仅供学习参考，不构成投资建议</footer>
</div>
{panels}
{DECK_JS}
</body></html>"""


def manifest() -> str:
    """PWA manifest：iOS/Android「添加到主屏幕」后有名字和图标底色。

    刻意不引图标文件 —— 没有 icons 字段时两家系统都会自动截图当图标，
    省掉维护一堆尺寸 png。
    """
    return json.dumps({
        "name": SITE_NAME,
        # 主屏图标下面那行只放得下四五个字，取"|"前面那截（知势），别截成半个单词
        "short_name": SITE_NAME.split("|")[0].strip()[:12] or SITE_NAME[:12],
        "start_url": "./index.html",
        "display": "standalone",
        "background_color": "#faf8f3",
        "theme_color": "#faf8f3",
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

    # 卡组要接上前一天/后一天，所以每天渲染时都得有邻居的 payload。
    # 全部读进内存也就几十 KB；--latest 只写一天，但邻居照样要读。
    payloads = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in files}

    for path in (files[:1] if args.latest else files):
        day = path.stem
        i = dates.index(day)
        older = payloads[dates[i + 1]] if i + 1 < len(dates) else None
        newer = payloads[dates[i - 1]] if i > 0 else None
        out = SITE_DIR / f"{day}.html"
        out.write_text(render_page(payloads[day], dates, day, older, newer),
                       encoding="utf-8")
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
