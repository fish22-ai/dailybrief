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
from core.models import CATEGORIES, DIRECTION_VALUES, INSIGHT_FIELDS, Item  # noqa: E402
from core.scoring import (                                     # noqa: E402
    RELEVANCE_THRESHOLD, SOURCE_WEIGHTS, relevance, score_all, score_item,
)
from core.seen import SeenStore                                # noqa: E402
from core.select import (                                      # noqa: E402
    MAX_RETRIES, MIN_FACTS, MIN_INDICATORS, MIN_NOTES, MIN_STAGES,
    MIN_STAKEHOLDERS, OUTPUT_SOURCE_CAP, TOP_N, _extract_json, build_prompt,
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

print("\nselect：解读 schema 校验（2026-09-11 起是七段结构化）")
i1 = mk("Fed 加息预期升温", "https://a.com/m1", cat="markets")
i2 = mk("新行政令签署", "https://a.com/p1", cat="policy")
index = {1: i1, 2: i2}


def node(step: int, direction: str = "up") -> dict:
    """一段金融传导。字段与 core.select._validate_insight 要求的一一对应。"""
    return {
        "step": step,
        "name": f"第{step}段名称",
        "trigger": f"第{step}段的触发动作与数字。",
        "mechanism": "为什么 A 导致 B：把中间被压缩掉的那几步摊开写清楚。",
        "assetImpacts": [{"asset": f"资产{step}", "direction": direction,
                          "note": "为什么这样影响"}],
        "timeHorizon": "1 - 2 周发酵",
        "keySensitivity": f"第{step}段要盯的具体指标",
    }


good = {
    "id": 1,
    "summary30s": ["事实一，含具体数字。", "事实二。", "事实三。"],
    "transmissionChain": [node(1), node(2), node(3), node(4, "neutral")],
    "historicalAnalogy": {"event": "历史上的同类事件", "year": "2005 年",
                          "comparison": "当时怎么走的，和现在像在哪、又差在哪。"},
    "gameTheoryStakeholders": [
        {"party": "美联储", "stance": "台面上的公开立场", "bottomLine": "不妥协的真实底牌"},
        {"party": "美国财政部", "stance": "台面上的公开立场", "bottomLine": "不妥协的真实底牌"},
    ],
    "forwardIndicators": [
        {"indicator": "10 年期美债收益率", "threshold": "4.50% - 4.75%",
         "significance": "触发风险平价策略被动去杠杆。"},
        {"indicator": "隔夜逆回购余额", "threshold": "低于 500 亿美元",
         "significance": "缓冲垫耗尽后会引发短端钱荒。"},
    ],
    "takeaways": {"investor": "缩短债券久期。", "industry": "用掉期锁死融资成本。",
                  "personal": "不要赌房贷利率会随时下跌。"},
    "notes": ["概念一：把链条里第一个专业词摊开讲。", "概念二：解释反向变动关系。",
              "概念三：解释远期现金流为什么更受伤。"],
}
ok = validate({"markets": [good], "policy": []}, index)
check("合法输出通过", len(ok["markets"]) == 1 and ok["policy"] == [])
check("卡片带 url 与 insight",
      ok["markets"][0]["url"] == "https://a.com/m1"
      and set(ok["markets"][0]["insight"]) == set(INSIGHT_FIELDS))
check("notes 保持为数组",
      isinstance(ok["markets"][0]["insight"]["notes"], list)
      and len(ok["markets"][0]["insight"]["notes"]) == 3)
check("传导链 step 按位置重新编号",
      [n["step"] for n in ok["markets"][0]["insight"]["transmissionChain"]] == [1, 2, 3, 4])
check("资产方向原样保留",
      ok["markets"][0]["insight"]["transmissionChain"][3]["assetImpacts"][0]["direction"]
      == "neutral")


def raises(payload) -> bool:
    try:
        validate(payload, index)
    except (ValueError, KeyError, TypeError):
        return True
    return False


def without(field: str) -> dict:
    return {"markets": [{k: v for k, v in good.items() if k != field}]}


check("编造 id 被拒", raises({"markets": [{**good, "id": 99}]}))
check("板块串台被拒", raises({"markets": [{**good, "id": 2}]}))
check("板块非数组被拒", raises({"markets": "not a list"}))
check("id 缺失被拒", raises(without("id")))
check("缺 summary30s 被拒", raises(without("summary30s")))
check("缺 transmissionChain 被拒", raises(without("transmissionChain")))
check("缺 historicalAnalogy 被拒", raises(without("historicalAnalogy")))
check("缺 gameTheoryStakeholders 被拒", raises(without("gameTheoryStakeholders")))
check("缺 forwardIndicators 被拒", raises(without("forwardIndicators")))
check("缺 takeaways 被拒", raises(without("takeaways")))
check("缺 notes 被拒", raises(without("notes")))

# 以下几条是**质量下限**，不是格式检查：段数少、博弈方只有一个、前瞻指标只有一个，
# 都说明推演被压缩成了结论清单，而"看不懂"的根因正是这个 —— 宁可重试一次。
check(f"事实内核少于 {MIN_FACTS} 条被拒",
      raises({"markets": [{**good, "summary30s": ["只有一条"]}]}))
check(f"传导链少于 {MIN_STAGES} 段被拒",
      raises({"markets": [{**good, "transmissionChain": [node(1), node(2), node(3)]}]}))
check(f"博弈方少于 {MIN_STAKEHOLDERS} 个被拒",
      raises({"markets": [{**good,
                           "gameTheoryStakeholders": good["gameTheoryStakeholders"][:1]}]}))
check(f"前瞻指标少于 {MIN_INDICATORS} 个被拒",
      raises({"markets": [{**good, "forwardIndicators": good["forwardIndicators"][:1]}]}))
check(f"notes 少于 {MIN_NOTES} 条被拒",
      raises({"markets": [{**good, "notes": ["只给一条"]}]}))

# 资产方向是页面选绿涨/红跌/琥珀震荡的依据，写别的值会让配色无处可落
check("非法 direction 被拒",
      raises({"markets": [{**good, "transmissionChain": [
          node(1), node(2), node(3),
          {**node(4), "assetImpacts": [{"asset": "某资产", "direction": "看多",
                                        "note": "中文方向"}]}]}]}))
check("direction 合法值就是这四个",
      DIRECTION_VALUES == ("up", "down", "volatile", "neutral"))
check("空 assetImpacts 被拒",
      raises({"markets": [{**good, "transmissionChain": [
          node(1), node(2), node(3), {**node(4), "assetImpacts": []}]}]}))
check("takeaways 缺一个视角被拒",
      raises({"markets": [{**good, "takeaways": {"investor": "x", "industry": "y"}}]}))
check("传导链段里空 mechanism 被拒",
      raises({"markets": [{**good, "transmissionChain": [
          node(1), node(2), node(3), {**node(4), "mechanism": "   "}]}]}))

# LaTeX 页面渲染不了（零依赖静态站，不引 KaTeX），出现就重试
check("notes 里的 LaTeX 被拒",
      raises({"markets": [{**good, "notes": [
          r"折现公式 $PV=\sum \frac{CF_t}{(1+r)^t}$", "第二条", "第三条"]}]}))
check("传导链机制里的 LaTeX 被拒",
      raises({"markets": [{**good, "transmissionChain": [
          node(1), node(2), node(3), {**node(4), "mechanism": r"折现率 \beta 上行"}]}]}))
check("行动启示里的 LaTeX 被拒",
      raises({"markets": [{**good, "takeaways": {
          **good["takeaways"], "investor": r"用 \frac{1}{2} 仓位"}}]}))
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
      len(validate({"markets": [{**good, "notes": ["a：一\nb：二", "c：三", "d：四"]}]},
                   index)["markets"][0]["insight"]["notes"]) == 4)

# 旧归档（2026-09-11 之前）是 what/why/chain 一根平文本。新校验**不再接受**它 ——
# 历史数据不用改（渲染层会回落），但模型若退回旧格式必须被拦下重试，
# 否则页面会渲染出一张空卡。
check("旧的 what/why/chain 格式被拒",
      raises({"markets": [{"id": 1, "what": "x", "why": "y",
                           "chain": "A → B → C", "notes": ["一", "二", "三"]}]}))

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
# 条数由 config.toml 的 [output] per_category 决定，断言一律用 TOP_N，不写死数字 ——
# 用户调条数不该让测试变红。
pool = {"markets": [mk(f"降息消息{i}", f"https://a.com/n{i}",
                       summary="源自带摘要") for i in range(8)],
        "policy": []}
cards = rules_fallback(pool)
check(f"每板块最多 {TOP_N} 条", len(cards["markets"]) == TOP_N)
check("空板块产出空列表", cards["policy"] == [])
check("规则模式 insight 为空", cards["markets"][0]["insight"] == {})
check("规则模式保留源摘要", cards["markets"][0]["raw_summary"] == "源自带摘要")

# 产出层单源限额：候选池阶段的限额在只取几条时形同虚设，实测华尔街见闻能占满
# 多数席位。TOP_N ≤ 上限时这条限额天然不起作用，所以只断言它**没被突破**，
# 不断言"一定生效"—— 生效与否取决于用户把 per_category 调到了几。
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
check(f"产出层单源不超 {OUTPUT_SOURCE_CAP} 条",
      max(counts.values()) <= OUTPUT_SOURCE_CAP, str(counts))
check(f"产出层仍凑满 {TOP_N} 条", len(sk["markets"]) == TOP_N)
check("产出层不都来自同一个源", len(counts) >= min(TOP_N, 2), str(counts))

# 只有一个来源时不能因为限额就少给条目
mono = {"markets": [mk(f"降息{i}", f"https://a.com/m{i}", source="华尔街见闻")
                    for i in range(6)], "policy": []}
check("单一来源时用溢出补齐", len(rules_fallback(mono)["markets"]) == TOP_N)

print("\nselect：prompt 组装")
prompt, idx = build_prompt({
    "markets": [mk("降息", "https://a.com/x1", summary="摘要一")],
    "policy": [mk("行政令", "https://a.com/x2", cat="policy")],
})
check("prompt 含两个板块标题",
      all(c in prompt for c in CATEGORIES), "板块名缺失")
check("index 覆盖全部候选", len(idx) == 2)
check("prompt 明确禁止复述新闻", "不是新闻摘要" in prompt or "复述标题" in prompt)
# 2026-09-11 起要求模型直出结构化字段，页面照着渲染、不再从平文本里猜。
check("prompt 要求结构化传导链", "transmissionChain" in prompt)
check("prompt 要求七段齐全",
      all(k in prompt for k in ("summary30s", "transmissionChain", "historicalAnalogy",
                                "gameTheoryStakeholders", "forwardIndicators",
                                "takeaways", "notes")))
check("prompt 要求资产方向取值", "direction" in prompt and "volatile" in prompt)
check("prompt 不要求用户点名删掉的字段",
      "surfaceVsCore" not in prompt and "reflectionQuestion" not in prompt)
# 解析已去公式化（用大白话讲机制），所以这里验的是"明确禁止写公式"而不是"公式怎么写"。
check("prompt 禁止写公式", "不要写公式" in prompt)
check("prompt 禁止 LaTeX", "LaTeX" in prompt and r"\frac" in prompt)
check("prompt 里没有残留的 format 占位符", "{{CANDIDATES}}" not in prompt)

print("\nbuild_site：解读渲染")
# 传导链拆环、公式成块这些不再是 f-string 排版，是真逻辑，值得单测。纯函数、不发请求。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_site  # noqa: E402

# 旧归档那根平文本，用来验回退路径（render_chain / _chain_stages / 旧卡片）
long_chain = ("通胀持续高位 → 美联储官员释放鹰派表态 → 市场修正降息预期 → "
              "未来政策基准利率预期抬升 → 短期无风险利率 Rf 上移 → "
              "旧美债固定票息性价比下降、投资者抛售 → 2 年期美债价格下跌、YTM 被动上行 → "
              "全市场资产定价基准抬高 → DCF 折现率 r 上行、远期现金流现值缩水 → "
              "高估值成长股承压")

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

# 新路径：结构化字段直接渲染，不再走 _chain_stages 的启发式切分
insight = ok["markets"][0]["insight"]
new_card = build_site.render_card(
    {"title": "t", "url": "https://a.com", "insight": insight}, "markets")
check("四张研读卡齐全",
      all(x in new_card for x in ("30秒事实内核", "金融传导全景脉络",
                                  "深入研读与博弈透视", "行动启示与认知内化")))
check("30秒事实内核渲染成 3 条",
      new_card.split('<ul class="fact-list">')[1].split("</ul>")[0].count("<li>") == 3)
# 研读卡只留标题，不挂"3 条客观事实 / 4 段传导 · 点击 Step 展开"这类副题
check("研读卡不渲染副题", '<p class="sub">' not in new_card)
# 栅格必须挂在内层 .take-list 上。挂到研读卡本身（cls 也叫 takes）会把卡头也当成
# 栅格子项 —— 卡头挤进第一列、内容进第二列、第三列空着，且只在桌面端看得出来。
check("行动启示的栅格没有套在研读卡上",
      '<section class="seg takes"><div class="sec-hd">' in new_card
      and '<div class="take-list">' in new_card)
check("传导链渲染成 4 个 STEP", new_card.count('<label class="step">') == 4)
check("步骤面板零 JS（radio + :has 面板）",
      'type="radio"' in new_card and 'class="step-panel ' in new_card)
# 本次改版的核心：面板三个字段来自三个不同的数据源，所以内容互不相同。
# 旧路径把一根平文本按位置切成三段，这三处会渲染出同一句话 —— 这条断言就是防它回来。
check("面板的触发源/机理/资产影响互不相同",
      "第1段的触发动作与数字。" in new_card
      and "为什么 A 导致 B" in new_card
      and "<b>资产1</b>" in new_card)
check("资产方向映射成配色类",
      'class="dir up"' in new_card and 'class="dir neu"' in new_card)
check("时滞渲染在面板头卡",
      "时滞 1 - 2 周发酵" in new_card)
check("博弈方 / 前瞻指标 / 行动启示三块都在",
      "博弈各方的台前立场与真实底牌" in new_card
      and "需持续追踪的前瞻红线指标" in new_card
      and "投资者视角" in new_card and "个人与家庭生活" in new_card)
check("用户点名不要的三块不出现",
      not any(x in new_card for x in ("表面直觉", "内化自测", "向知势AI追问")))
check("小节标题不带英文括注",
      not any(x in new_card for x in ("Fact Nucleus", "Direct Trigger",
                                      "Surface vs", "Self-Test")))
check("卡片带板块标签", 'data-cat="markets"' in new_card)

# 旧归档回落：data/ 里 2026-09-11 之前的数据没有结构化字段，重跑不能让页面掉内容
old_card = build_site.render_card({"title": "t", "url": "https://a.com", "insight": {
    "what": "旧四段", "why": "回落渲染", "chain": long_chain,
    "notes": ["概念一", "概念二", "概念三"]}}, "markets")
check("旧归档回落渲染 发生了什么/市场为什么在意",
      ">发生了什么<" in old_card and ">市场为什么在意<" in old_card)
check("旧归档仍渲染经济传导脉络与解析",
      "经济传导脉络" in old_card and "展开解析" in old_card)
check("旧归档不会误渲染新卡", "30秒事实内核" not in old_card)
st = build_site._chain_stages(long_chain)
check("旧归档的启发式仍能拆 4 个阶段", len(st) == 4)
check("每阶段都有监测指标", all(s["monitor"] for s in st))
check("旧路径的步骤条也是零 JS",
      '<label class="step">' in build_site.render_stages(long_chain)
      and 'type="radio"' in build_site.render_stages(long_chain))

# 更早的五字段时代（what/why/chain/watch/term）没有 notes
old5 = build_site.render_card({"title": "t", "url": "https://a.com", "insight": {
    "what": "旧五字段", "why": "回落渲染", "chain": "A → B",
    "watch": "9 月 16 日 FOMC", "term": "点阵图：利率路径预测分布"}}, "markets")
check("更早的五字段回落渲染 盯什么/概念", ">盯什么<" in old5 and ">概念<" in old5)
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

# prompt 里写给模型的数量要求必须与校验阈值一致，否则"prompt 要 8 段、校验要 4 段"
# 会导致每天必然重试一次再降级。有 prompt.zh.md 时数字由它的 front matter 给出。
tmpl = build_prompt({"markets": [mk("x", "https://a.com/1")], "policy": []})[0]
check("prompt 的段数与校验阈值一致", f"{MIN_STAGES} 段" in tmpl)
check("prompt 的解析条数区间与校验阈值一致", f"{MIN_NOTES}~" in tmpl)
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
