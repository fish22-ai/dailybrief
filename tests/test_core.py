"""核心规则层单元测试：URL 归一化、去重、相关性闸门、打分、解读 schema 校验。

跑法：python tests/test_core.py（不依赖 pytest，也不发网络请求）
"""
from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.dedup import dedup, jaccard, normalize_url          # noqa: E402
from core.models import CATEGORIES, INSIGHT_FIELDS, Item      # noqa: E402
from core.scoring import (                                     # noqa: E402
    RELEVANCE_THRESHOLD, SOURCE_WEIGHTS, relevance, score_all, score_item,
)
from core.seen import SeenStore                                # noqa: E402
from core.select import (                                      # noqa: E402
    MIN_CHAIN_HOPS, MIN_NOTES, MAX_RETRIES, TOP_N, _extract_json, build_prompt,
    prompt_template, rules_fallback, validate,
)
from core.timeutil import in_window, now_local, parse_any, today_str   # noqa: E402
from fetchers.registry import SOURCES                          # noqa: E402

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        failures.append(name)


def mk(title: str, url: str, source: str = "BBC",
       cat: str = "markets", **kw) -> Item:
    return Item(title=title, url=url, source=source, category=cat, **kw)


print("normalize_url")
check("去 utm 参数",
      normalize_url("https://a.com/x?utm_source=x&id=1") == "https://a.com/x?id=1")
check("去 www 与末尾斜杠", normalize_url("http://www.a.com/x/") == "https://a.com/x")
check("query 排序稳定",
      normalize_url("https://a.com/p?b=2&a=1") == normalize_url("https://a.com/p?a=1&b=2"))
check("根路径保留斜杠", normalize_url("https://a.com") == "https://a.com/")

print("\njaccard")
check("完全相同为 1.0", jaccard("美联储降息 25 个基点", "美联储降息 25 个基点") == 1.0)
check("标点差异仍判同一事件", jaccard("美联储降息25个基点！", "美联储降息25个基点") > 0.9)
check("不同事件低相似", jaccard("美联储降息25个基点", "证监会发布私募新规") < 0.3)

print("\n相关性闸门（这是解决 sandwich 问题的关键）")
soft = [
    ("三明治店换供应商",
     mk("Why a famous Montreal sandwich shop has been forced to switch suppliers",
        "https://a.com/s1", summary="Schwartz's, one of Montreal's most famous delis")),
    ("66 号公路旅游",
     mk("The Europeans Obsessed With a U.S. Road Trip", "https://a.com/s2",
        summary="As Route 66 turns 100, tourists in search of Americana")),
    ("蟑螂激增（surge 误命中）",
     mk("Japan's Hokkaido battles cockroach surge", "https://a.com/s3")),
    ("中暑职业病", mk("Hong Kong open to classifying heatstroke as occupational",
                  "https://a.com/s4")),
    ("死刑判决", mk("TV presenter among 11 sentenced to death in Egypt drugs case",
                "https://a.com/s5")),
    ("登山救援", mk("Moment 64-year-old Nepali woman is found alive after landslide",
                "https://a.com/s6")),
    ("单车事故", mk("Fatal cycling accidents in Hong Kong more than double",
                "https://a.com/s7")),
]
for label, item in soft:
    r = relevance(item)
    check(f"拦下：{label}", r < RELEVANCE_THRESHOLD, f"rel={r}")

hard = [
    ("美联储人事与加息",
     mk("Trump turns up the heat on Warsh as Fed rate hike looms", "https://a.com/h1")),
    ("美债抛售",
     mk("Treasury sell-off piles pressure on weakest US borrowers", "https://a.com/h2")),
    ("银行再融资", mk("农业银行拟募资不超过1600亿元用于补充资本", "https://a.com/h3")),
    ("降息", mk("央行宣布降准0.5个百分点", "https://a.com/h4")),
    ("关税", mk("US announces new tariffs on imported steel", "https://a.com/h5")),
    ("油轮袭击（地缘+市场）",
     mk("US strikes three Iranian oil tankers in response to attack",
        "https://a.com/h6", cat="policy")),
    ("行政令", mk("Declaring a National Emergency To Secure the United States Border",
               "https://a.com/h7", cat="policy",
               summary="美国总统行政令 / 公告原文（Federal Register 一手文件）")),
]
for label, item in hard:
    r = relevance(item)
    check(f"放行：{label}", r >= RELEVANCE_THRESHOLD, f"rel={r}")

print("\nscore_all 闸门集成")
mixed = [i for _, i in soft] + [i for _, i in hard]
for it in mixed:
    it.published = now_local()
kept, blocked = score_all(mixed)
check("软新闻被过滤", blocked == len(soft), f"blocked={blocked} 期望 {len(soft)}")
check("硬新闻全部保留", len(kept) == len(hard), f"kept={len(kept)}")
check("按分数降序", all(kept[i].score >= kept[i + 1].score for i in range(len(kept) - 1)))

print("\ndedup")
a = mk("Fed cuts rates", "https://x.com/a?utm_source=rss", source="Federal Reserve")
b = mk("Fed cuts rates", "https://www.x.com/a/", source="MarketWatch")
kept2, removed = dedup([a, b])
check("URL 归一化去重", len(kept2) == 1 and removed == 1, f"kept={len(kept2)}")
check("保留来源权重更高者", kept2[0].source == "Federal Reserve", kept2[0].source)

c = mk("美联储宣布降息25个基点", "https://p.com/1", source="ECB")
d = mk("美联储宣布降息 25 个基点。", "https://q.com/2", source="东方财富")
kept2, _ = dedup([c, d])
check("标题相似跨源去重", len(kept2) == 1 and kept2[0].source == "ECB",
      str([k.source for k in kept2]))

e = mk("关税新规出台", "https://p.com/3", cat="markets")
f = mk("关税新规出台", "https://q.com/4", cat="policy")
check("跨板块同题不去重", len(dedup([e, f])[0]) == 2)

print("\nscoring")
now = now_local()
fresh = score_item(mk("央行降息", "https://a.com/1", source="Federal Reserve",
                      published=now))
old = score_item(mk("央行降息", "https://a.com/2", source="Federal Reserve",
                    published=now - timedelta(hours=30)))
check("同源新条目分更高", fresh.score > old.score, f"{fresh.score} vs {old.score}")

official = score_item(mk("降息决议", "https://a.com/3", source="Federal Reserve",
                         published=now))
tabloid = score_item(mk("降息决议", "https://a.com/4", source="MarketWatch",
                        published=now))
check("官方源权重更高", official.score > tabloid.score)

strong = score_item(mk("美联储降息25基点，通胀回落", "https://a.com/5", published=now))
weak = score_item(mk("某国经济数据公布", "https://a.com/6", published=now))
check("相关性强的分更高", strong.score > weak.score,
      f"{strong.score} vs {weak.score}")

no_date = score_item(mk("降息", "https://a.com/7"))
check("无日期给偏低中性值", no_date.score_detail["recency"] == 0.25)
check("打分明细含四项",
      set(no_date.score_detail) == {"source", "recency", "heat", "relevance"})

print("\ntimeutil")
# 窗口大小现在由 config.toml 的 [fetch] window_hours 决定，所以这几条一律显式
# 传小时数 —— 用默认值的话，用户把窗口从 36 调到 168 就会让"窗口外"的用例失败，
# 而那是配置变更、不是回归。
check("窗口内保留", in_window(now - timedelta(hours=10), 36))
check("窗口外剔除", not in_window(now - timedelta(hours=40), 36))
check("无日期保留", in_window(None))
check("宽窗口生效", in_window(now - timedelta(hours=40), 24 * 7))
check("窗口大小可配置", in_window(now - timedelta(hours=100), 168)
      and not in_window(now - timedelta(hours=200), 168))
check("解析中文日期", parse_any("2026年9月4日").strftime("%Y-%m-%d") == "2026-09-04")
check("解析 RFC822", parse_any("Fri, 04 Sep 2026 23:00:00 GMT") is not None)
check("解析 ISO 日期", parse_any("2026-09-03").strftime("%Y-%m-%d") == "2026-09-03")
check("解析垃圾串返回 None", parse_any("不是日期") is None)

print("\nselect：解读 schema 校验")
i1 = mk("Fed 加息预期升温", "https://a.com/m1", cat="markets")
i2 = mk("新行政令签署", "https://a.com/p1", cat="policy")
index = {1: i1, 2: i2}
long_chain = ("通胀持续高位 → 美联储官员释放鹰派表态 → 市场修正降息预期 → "
              "未来政策基准利率预期抬升 → 短期无风险利率 Rf 上移 → "
              "旧美债固定票息性价比下降、投资者抛售 → 2 年期美债价格下跌、YTM 被动上行 → "
              "全市场资产定价基准抬高 → DCF 折现率 r 上行、远期现金流现值缩水 → "
              "高估值成长股承压")
good = {
    "id": 1,
    "what": "美联储理事巴尔称若通胀继续高于 2% 目标，他可能支持加息。",
    "why": "市场原本定价的是降息，现在要给加息风险加权重，短端利率与美元同步上行。",
    "chain": long_chain,
    "notes": [
        "鹰派核心目标：把抑制通胀放在第一位，只要通胀高于目标就倾向加息、收紧货币政策。",
        "加息存在时间滞后！完整传导要 6~12 个月，所以通胀刚抬头鹰派就呼吁动手。",
        "why 债券价格与收益率反向变动？债券定价公式 "
        "`P = C/(1+y)¹ + C/(1+y)² + … + (C+Face)/(1+y)ⁿ`。票息 C 固定不变，"
        "投资者抛售使价格 P 下跌，分子不变则只能是分母的 y 变大，YTM 被动抬升。",
    ],
}
ok = validate({"markets": [good], "policy": []}, index)
check("合法输出通过", len(ok["markets"]) == 1 and ok["policy"] == [])
check("卡片带 url 与 insight",
      ok["markets"][0]["url"] == "https://a.com/m1"
      and set(ok["markets"][0]["insight"]) == set(INSIGHT_FIELDS))
check("notes 保持为数组",
      isinstance(ok["markets"][0]["insight"]["notes"], list)
      and len(ok["markets"][0]["insight"]["notes"]) == 3)


def raises(payload) -> bool:
    try:
        validate(payload, index)
    except (ValueError, KeyError, TypeError):
        return True
    return False


check("编造 id 被拒", raises({"markets": [{**good, "id": 99}]}))
check("板块串台被拒", raises({"markets": [{**good, "id": 2}]}))
check("缺 chain 被拒",
      raises({"markets": [{k: v for k, v in good.items() if k != "chain"}]}))
check("缺 notes 被拒",
      raises({"markets": [{k: v for k, v in good.items() if k != "notes"}]}))
check("空 why 被拒", raises({"markets": [{**good, "why": "   "}]}))
check("chain 没有箭头被拒",
      raises({"markets": [{**good, "chain": "加息导致股票下跌"}]}))
check("板块非数组被拒", raises({"markets": "not a list"}))
check("id 缺失被拒",
      raises({"markets": [{k: v for k, v in good.items() if k != "id"}]}))

# 以下三条是"看不懂"那次改版加的质量下限：链条不许压缩成结论、解析不许只给一条、
# 公式不许写 LaTeX（页面零依赖，不引 KaTeX，渲染不出来）。
check(f"chain 少于 {MIN_CHAIN_HOPS} 环被拒",
      raises({"markets": [{**good,
                           "chain": "加息预期上升 → 美债收益率上行 → 成长股承压"}]}))
check("chain 刚好够环数通过",
      len(validate({"markets": [{**good, "chain": " → ".join(
          f"第{n}环机制" for n in range(MIN_CHAIN_HOPS))}]}, index)["markets"]) == 1)
check(f"notes 少于 {MIN_NOTES} 条被拒",
      raises({"markets": [{**good, "notes": ["只给一条"]}]}))
check("notes 里的 LaTeX 被拒",
      raises({"markets": [{**good, "notes": [
          r"折现公式 $PV=\sum \frac{CF_t}{(1+r)^t}$", "第二条", "第三条"]}]}))
check("chain 里的 LaTeX 被拒",
      raises({"markets": [{**good, "chain": long_chain + r" → \beta 上行"}]}))
# 成本护栏：每天的 API 调用数 = 1 + MAX_RETRIES，别让它悄悄变成循环重试
check("每日调用上限 2 次", MAX_RETRIES == 1)

# 模型偶尔把 notes 写成一整段带 * 的文本而不是数组。内容是对的只是包装错了，
# 按行拆开比整体重试划算（重试要多烧上万 token，且未必更听话）。
loose = validate({"markets": [{**good, "notes": (
    "* 第一条：概念解释\n"
    "* 第二条：另一个概念\n"
    "- 第三条：机制提醒\n")}]}, index)["markets"][0]["insight"]["notes"]
check("notes 是整段文本时按行拆开", loose == [
    "第一条：概念解释", "第二条：另一个概念", "第三条：机制提醒"], str(loose))
check("notes 数组元素里的多行也拆开",
      len(validate({"markets": [{**good, "notes": ["a：一\nb：二", "c：三"]}]},
                   index)["markets"][0]["insight"]["notes"]) == 3)

print("\nselect：JSON 提取")
check("剥 ```json 围栏",
      _extract_json('```json\n{"markets":[]}\n```') == {"markets": []})
check("剥前言", _extract_json('好的：\n{"markets":[]}') == {"markets": []})
check("裸 JSON", _extract_json('{"markets":[]}') == {"markets": []})
try:
    _extract_json("完全没有 JSON")
    check("无 JSON 抛异常", False)
except ValueError:
    check("无 JSON 抛异常", True)

print("\nselect：规则降级")
pool = {"markets": [mk(f"降息消息{i}", f"https://a.com/n{i}",
                       summary="源自带摘要") for i in range(8)],
        "policy": []}
cards = rules_fallback(pool)
check("每板块最多 5 条", len(cards["markets"]) == 5)
check("空板块产出空列表", cards["policy"] == [])
check("规则模式 insight 为空", cards["markets"][0]["insight"] == {})
check("规则模式保留源摘要", cards["markets"][0]["raw_summary"] == "源自带摘要")

# 产出层单源限额：候选池的 4 条限额在只取 5 条时形同虚设，实测华尔街见闻
# 能占满 4/5 个席位。这里验证收紧后的 2 条上限确实生效。
skewed = {
    "markets": ([mk(f"降息{i}", f"https://a.com/w{i}", source="华尔街见闻")
                 for i in range(6)]
                + [mk("关税落地", "https://a.com/o1", source="CNBC Economy"),
                   mk("美债抛售", "https://a.com/o2", source="FT Markets"),
                   mk("行政令签署", "https://a.com/o3", source="Federal Reserve")]),
    "policy": [],
}
sk = rules_fallback(skewed)
counts: dict[str, int] = {}
for c in sk["markets"]:
    counts[c["source"]] = counts.get(c["source"], 0) + 1
check("产出层单源不超 2 条", max(counts.values()) <= 2, str(counts))
check("产出层仍凑满 5 条", len(sk["markets"]) == 5)
check("产出层覆盖多个来源", len(counts) >= 3, str(counts))

# 只有一个来源时不能因为限额就少给条目
mono = {"markets": [mk(f"降息{i}", f"https://a.com/m{i}", source="华尔街见闻")
                    for i in range(6)], "policy": []}
check("单一来源时用溢出补齐", len(rules_fallback(mono)["markets"]) == 5)

print("\nselect：prompt 组装")
prompt, idx = build_prompt({
    "markets": [mk("降息", "https://a.com/x1", summary="摘要一")],
    "policy": [mk("行政令", "https://a.com/x2", cat="policy")],
})
check("prompt 含两个板块标题",
      all(c in prompt for c in CATEGORIES), "板块名缺失")
check("index 覆盖全部候选", len(idx) == 2)
check("prompt 要求 chain 用箭头", " → " in prompt)
check("prompt 明确禁止复述新闻", "不是新闻摘要" in prompt or "复述标题" in prompt)
# 长传导链是旧版对"看懂"的解法，现在改成"短环、一环一步、5~8 环"，见 prompt.zh.md。
# 这里只验"prompt 里有环数区间、且下限不低于校验阈值"，具体数字由文件 front matter 决定。
check("prompt 要求环数区间", "环" in prompt and "~" in prompt)
check("prompt 要求公式用反引号纯文本", "`PV = ∑ CFₜ/(1+r)ᵗ`" in prompt)
check("prompt 禁止 LaTeX", "禁止 LaTeX" in prompt and r"\frac" in prompt)
check("prompt 里没有残留的 format 占位符", "{{CANDIDATES}}" not in prompt)

print("\nbuild_site：解读渲染")
# 传导链拆环、公式成块这些不再是 f-string 排版，是真逻辑，值得单测。纯函数、不发请求。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_site  # noqa: E402

flow = build_site.render_chain(long_chain)
check("传导链一环一格", flow.count('class="hop"') == 10, str(flow.count('class="hop"')))
check("环之间有箭头", flow.count('class="arw"') == 9)
two = build_site.render_chain("A → B → C；D → E → F")
check("支线另起一行", two.count('class="flow"') == 2)
check("没有箭头时不吞掉内容", "整条都没有箭头" in build_site.render_chain("整条都没有箭头"))

long_fml = build_site.render_note("why 价格反向？公式 `P = C/(1+y)¹ + (C+Face)/(1+y)ⁿ`。票息固定。")
check("长公式单独成等宽块", '<div class="fml">' in long_fml)
check("设问句被加粗", '<b class="nk">why 价格反向？</b>' in long_fml)
check("公式块后的孤立句号被吃掉", "</div>。" not in long_fml, long_fml)
check("短公式走行内 code",
      "<code>Rf</code>" in build_site.render_note("锚是 `Rf` 这个无风险利率。"))
check("概念名前缀被加粗",
      '<b class="nk">信用利差：</b>' in build_site.render_note("信用利差：风险溢价。"))
check("反引号没配对也不崩",
      "<li>" in build_site.render_note("公式 `P = C/(1+y) 少了一个反引号"))
check("解析内容被转义",
      "&lt;script&gt;" in build_site.render_note("注意 <script> 标签"))

new_card = build_site.render_card({"title": "t", "url": "https://a.com",
                                   "insight": ok["markets"][0]["insight"]}, "markets")
check("四段标签齐全",
      all(x in new_card for x in ("发生了什么", "市场为什么在意", "金融传导", "解析")))
check("解析渲染成列表", new_card.count("<li>") == 3)
check("卡片带板块标签", 'data-cat="markets"' in new_card)
# 2026-09-06 之前的归档没有 notes，重跑不能让历史页面掉内容
old_card = build_site.render_card({"title": "t", "url": "https://a.com", "insight": {
    "what": "旧五字段", "why": "回落渲染", "chain": "A → B",
    "watch": "9 月 16 日 FOMC", "term": "点阵图：利率路径预测分布"}}, "markets")
check("旧归档回落渲染 盯什么/概念", ">盯什么<" in old_card and ">概念<" in old_card)
check("规则模式仍显示无解读占位",
      "今日无 AI 解读" in build_site.render_card(
          {"title": "t", "url": "https://a.com", "insight": {}}, "policy"))

print("\nconfig：用户配置")
# 核心要求是"配置写错也要能出页面" —— 站点每周只跑一次，一个拼写错误让整周空白
# 不值得。所以下面每条都在验证"回落到默认值"而不是"抛异常"。
from core import config as cfgmod  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    def w(text: str) -> dict:
        f = Path(tmp) / "c.toml"
        f.write_text(text, encoding="utf-8")
        return cfgmod.load(f)

    c = w('[output]\nper_category = 3\n[site]\nname = "我的笔记"\n')
    check("读取整数", cfgmod.get_int("output", "per_category", 5, c) == 3)
    check("读取字符串", cfgmod.get_str("site", "name", "默认", c) == "我的笔记")
    check("缺失的键用默认值", cfgmod.get_int("output", "不存在", 7, c) == 7)
    check("缺失的表用默认值", cfgmod.get_str("没这个表", "k", "d", c) == "d")

    check("语法错误回落空配置", w("这不是 = = TOML [[[") == {})
    bad = w('[output]\nper_category = "五"\npool_size = true\n')
    check("类型错误用默认值", cfgmod.get_int("output", "per_category", 5, bad) == 5)
    # bool 是 int 的子类，true 会被静默当成 1 —— 明显是填错了，不能接受
    check("布尔值不被当成整数", cfgmod.get_int("output", "pool_size", 18, bad) == 18)

    rng = w("[output]\nper_category = 999\n[fetch]\nrelevance_threshold = -3\n")
    check("超上限裁剪到上限",
          cfgmod.get_int("output", "per_category", 5, rng, lo=1, hi=10) == 10)
    check("低于下限裁剪到下限",
          cfgmod.get_float("fetch", "relevance_threshold", 0.3, rng, lo=0.0, hi=1.0) == 0.0)

    nested = w('[llm.prompt]\nextra = "多写公式"\n')
    check("点号路径取嵌套表",
          cfgmod.get_str("llm.prompt", "extra", "", nested) == "多写公式")

check("文件不存在回落空配置", cfgmod.load(Path("绝对不存在的路径.toml")) == {})

# prompt 里写给模型的数量要求必须与校验阈值一致，否则"prompt 要 8 环、校验要 12 环"
# 会导致每天必然重试一次再降级。有 prompt.zh.md 时数字由它的 front matter 给出。
tmpl = build_prompt({"markets": [mk("x", "https://a.com/1")], "policy": []})[0]
check("prompt 的环数下限不低于校验阈值",
      f"{MIN_CHAIN_HOPS}~{MIN_CHAIN_HOPS + 3} 环" in tmpl
      or f"{MIN_CHAIN_HOPS}~8 环" in tmpl)
check("prompt 的条数与 TOP_N 一致", f"{TOP_N} 条" in tmpl)

print("\nseen 归档")
with tempfile.TemporaryDirectory() as tmp:
    store = SeenStore(Path(tmp) / "seen.json")
    batch = [mk("t1", "https://s.com/1?utm_source=x"), mk("t2", "https://s.com/2")]
    for it in batch:
        it.norm_url = normalize_url(it.url)

    check("空档案不排除任何条目", store.filter_new(batch) == (batch, 0))
    store.mark(batch)
    check("同日重跑结果不变", store.filter_new(batch)[1] == 0)

    store.save()
    reloaded = SeenStore(Path(tmp) / "seen.json")
    check("存档可回读", len(reloaded._data) == 2)

    for k in reloaded._data:
        reloaded._data[k] = "2000-01-01"
    check("往日条目被排除", reloaded.filter_new(batch)[1] == 2)
    check("档案不存在按空处理",
          SeenStore(Path(tmp) / "missing.json").filter_new(batch)[0] == batch)

print("\n注册表与权重表一致性")
declared = {s.key for s in SOURCES}
check("注册表 key 唯一", len(declared) == len(SOURCES))
check("每个板块都有 stable 源",
      all(any(s.category == c and s.tier == "stable" for s in SOURCES)
          for c in CATEGORIES))
check("所有源的 category 合法",
      all(s.category in CATEGORIES for s in SOURCES),
      str([s.key for s in SOURCES if s.category not in CATEGORIES]))
check("所有源的 tier 合法",
      all(s.tier in ("stable", "optional") for s in SOURCES))
check("权重表键均为字符串且值在 0~1",
      all(isinstance(k, str) and 0 < v <= 1.0 for k, v in SOURCE_WEIGHTS.items()))

print()
if failures:
    print(f"{len(failures)} 项失败：{', '.join(failures)}")
    raise SystemExit(1)
print("全部通过")
