"""精选 + AI 解读：一次 LLM 调用产出全部卡片；失败则降级为规则模式。

对应 CLAUDE.md 第七、八节。每日调用上限 2 次（首次 + JSON 非法时重试 1 次）。

这里是整个项目的价值所在。**不要让模型复述新闻** —— 标题本身就是新闻，用户
自己会读。模型要回答的是"这条为什么重要、钱怎么一环一环流动、背后是哪个公式在动"。
所以每条卡片的解读是四段结构化内容，而不是一句摘要：

  what   发生了什么（大白话，不用行话缩写）
  why    市场为什么在意（它改变了市场原本在定价的哪个预期）
  chain  金融传导（每环只表达一个因果跳跃，通常 4~15 字；机制没法一句话说清就拆成两环）
  notes  解析（字符串数组，把链条里每个专业概念摊开讲；涉及定价就给公式并讲公式怎么动）

**prompt 是用户可手改的 `prompt.zh.md`**：文件存在时，正文与步数从它读取（优先于
内置 DEFAULT_PROMPT 与 config 里的数字），没有文件时回落内置。文件开头的 YAML front matter
给出环数/条数的上下限，**校验用的下限也跟着它走**，和正文里的 {{HOPS_LO}}~{{HOPS_HI}}
保持一致 —— 这正是"看到几环、就要几环"的保证，否则每天必然重试一次。
用户改这份文件即可微调解读，无需改代码。

2026-09-06 改版原因：原先是 what/why/chain/watch/term 五个短字段，用户反馈"看不懂"。
根因不是写得不对，而是**中间机制被压缩掉了** —— chain 只有 3~4 环，从"官员转鹰"
一步跳到"成长股承压"，中间的 Rf、折现率、现值三步全省了；term 只讲一个概念，
链条里其余专业词全靠猜。所以这次把链条拉长、把 term 扩成 notes 数组。
公式一律写成**反引号包住的纯文本**（`P = C/(1+y)¹ + …`），不用 LaTeX ——
页面是零依赖静态站，不引 KaTeX，纯文本公式在任何环境下都读得出来。

规则模式（无 key / API 失败）下这些字段为空，页面明确标注"今日无解读"，
不拿新闻摘要冒充解读。
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from . import config
from .models import (
    CATEGORIES, CATEGORY_LABELS, INSIGHT_LIST_FIELDS, INSIGHT_TEXT_FIELDS, Item,
)

log = logging.getLogger(__name__)

SNIPPET_CHARS = 140

# ── 用户可手改的 prompt 文件 ──
# prompt.zh.md 存在时，它决定"发给模型什么"（文件正文）以及"校验收几条/几环"
# （文件开头 YAML front matter 里的参数）。用户改这份文件就是微调解读，无需改代码。
# 文件不存在时回落到下面内置的 DEFAULT_PROMPT 与 config 里的数字。
PROMPT_FILE = Path(__file__).resolve().parent.parent / "prompt.zh.md"


def _load_prompt_file() -> tuple[str, dict] | None:
    """读 prompt.zh.md。返回 (正文, 参数)，文件缺失或坏掉时返回 None。

    YAML front matter 我们不用 yaml 库（避免新增依赖），这里只认简单格式：
       key: value
    只关心四个整数参数 chain_hops_min / chain_hops_max / notes_min / notes_max。
    正文 = 第二个 `---` 之后的全部内容，原样发给模型（含 {{...}} 占位符）。
    """
    try:
        text = PROMPT_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        log.warning("prompt.zh.md 开头没有 --- front matter，改用内置 prompt")
        return None
    raw = m.group(1)
    params = {"chain_hops_min": 5, "chain_hops_max": 8,
              "notes_min": 3, "notes_max": 8}
    for line in raw.splitlines():
        kv = re.match(r"\s*([A-Za-z_]+)\s*:\s*(\d+)\s*$", line)
        if kv and kv.group(1) in params:
            params[kv.group(1)] = int(kv.group(2))
    return text[m.end():], params


_PF = _load_prompt_file()
# DEFAULT_PROMPT 在下面由内置文本定义；若有用户文件，其正文覆盖之。
_PF_PARAMS = _PF[1] if _PF else {}

# 以下均可在 config.toml 覆盖，改配置不用动代码。下限刻意设得保守：
# max_tokens 太小会让每次输出都被截断、白烧重试；min_chain_hops 是质量下限，
# 环数少就说明中间机制被压成结论了（2026-09-06 改版那次的教训）。这个下限压到
# 5 是配合"每环只写一个步骤"的写法 —— 步骤短了，需要的环数反而少了。
MODEL = config.get_str("llm", "model", "claude-opus-5")
MAX_TOKENS = config.get_int("llm", "max_tokens", 16000, lo=2000, hi=64000)
# 中转站的模型（如 deepseek-v4-flash）默认输出思考块，思考 token 会吃掉大半
# 输出预算，正文还没写就撞 max_tokens 截断。默认关掉，省下的全给 JSON 正文。
DISABLE_THINKING = config.get_bool("llm", "disable_thinking", True)
MAX_RETRIES = config.get_int("llm", "max_retries", 1, lo=0, hi=3)
# 中转站是多通道轮询：同一个模型名，这次可能落到好通道、下次落到坏通道（500 /
# 把请求转给 Bedrock 后报"模型标识无效"）。这类错误换次通道就好，所以单独给一份
# 重试预算，不占用上面 max_retries 那份（那份是留给 JSON 格式不合规的修复重试的）。
UPSTREAM_RETRIES = config.get_int("llm", "upstream_retries", 3, lo=0, hi=8)
UPSTREAM_BACKOFF = 4.0        # 秒，按次数线性递增：4s、8s、12s
# prompt.zh.md 的 front matter 一旦给出下限就优先于 config：校验层和 prompt 里的
# 数字必须同源，否则"prompt 要 8 环、校验要 12 环"会让每天必然重试一次。
MIN_CHAIN_HOPS = _PF_PARAMS.get("chain_hops_min",
                                config.get_int("llm", "min_chain_hops", 5, lo=3, hi=30))
MIN_NOTES = _PF_PARAMS.get("notes_min",
                           config.get_int("llm", "min_notes", 3, lo=1, hi=12))

TOP_N = config.get_int("output", "per_category", 5, lo=1, hi=10)
MAX_CANDIDATES_PER_CAT = config.get_int("output", "pool_size", 18, lo=3, hi=60)

# 公式必须是纯文本。反斜杠后面跟字母或括号在中文财经行文里几乎不可能出现，
# 出现就是模型在写 LaTeX（\frac、\sum、\( \)），页面渲染不了，带错误信息重试。
LATEX_PAT = re.compile(r"\\[A-Za-z(\[]")

DEFAULT_PROMPT = """你是一位帮我建立金融 sense 的资深买方分析师。我在准备金融行业面试，\
需要的不是新闻摘要（标题我自己会读），而是**能让我把这件事的传导机制自己讲出来的解读**。

读者有两类人，**必须同时照顾到**：正在备考金融面试的人（要能自己复述推演、能被追问）、
以及基本没接触过金融的人（专业术语第一次出现就必须解释清楚）。所以宁可多写一句
把机制说透，也不要写成结论清单，也不要卖弄术语。

下面是今天抓取的候选新闻，分三个板块（市场 / 政策 / 科技）。请：

1. 每个板块挑出最重要的 {{TOP_N}} 条以内。判断标准：**对利率、汇率、资产价格、\
资本流动的实际影响力**。果断剔除：公司公关稿、个股炒作、无传导链的社会新闻、\
纯人道议题。宁可少选几条真正重要的，也不要凑够 {{TOP_N}} 条。

2. 每条给出四段解读，全部用中文。

   - what（发生了什么）：一句话，谁做了什么、说了什么、数字是多少。\
不用行话缩写，不超过 60 字。
   - why（市场为什么在意）：说清这条**改变了市场原本在定价的哪个预期**，\
以及哪一类资产因此要重新估值。不超过 80 字。
   - chain（金融传导）：用 " → " 连接 **{{HOPS_LO}}~{{HOPS_HI}} 环**，从事件一路推到具体资产价格。
     · **每一环只写一个步骤，越短越好，4~12 个字**，用大白话，不要塞行话缩写。\
反例（又长又挤）："市场修正降息预期、开始定价未来加息概率上行"；\
正例："市场修正降息预期"。宁肯多拆一环，也不要一环塞两个动作。
     · **每一环都要写出机制**，不能只写结果。反例："加息预期上升 → 成长股承压"\
（跳掉了无风险利率、折现率、现值三步）；正例："短期无风险利率 Rf 上移 → \
旧债性价比下降、投资者抛售 → 债券价格下跌、YTM 被动上行 → \
全市场定价基准抬高 → 折现率 r 上行、远期现金流现值缩水 → 高估值成长股承压"。
     · 专业概念**第一次出现就顺手点出符号或英文**，如"无风险利率 Rf"、\
"到期收益率 YTM"、"期限溢价 term premium"。
     · 只有**最后一环**可以用"如果 A 和 B 同时成立，C 才可能发生"这种带条件的说法，\
前面每一环都必须是确定的因果。不要每一环都"可能""可能"，会显得没有传导。
     · 若还有一条并行支线（汇率、跨境资金流等），用 "；" 隔开另起一条链，\
不要硬塞进主链。
   - notes（解析）：{{NOTES_LO}}~{{NOTES_HI}} 条字符串，把上面链条里\
**每一个可能不懂的概念摊开讲**，一条一个知识点，写成下面几种之一：
     · 定义式："信用利差：风险债券相对无风险美债多出来的利息溢价。走阔代表市场\
担心违约，要求更高利息才肯借钱给企业。"
     · 提问式（多用这种）："why 债券价格与收益率反向变动？……"
     · 机制提醒式："加息存在时间滞后！今天宣布加息，不会明天物价就降下来。"
     · **只要这条涉及定价、折现、利差、收益率，就必须给公式**：\
先写公式，再用 1~2 句话说清"哪个变量动了、往哪个方向动、于是哪一项变大变小"。\
公式写在反引号里并**附上中文解释**，不要只扔一个公式让读者自己猜：\
`P = C/(1+y)¹ + … + (C+Face)/(1+y)ⁿ`（票息 C 固定，价格 P 下降时只能是分母 y 变大，所以 YTM 抬升）。\
其余反引号纯文本公式：`PV = ∑ CFₜ/(1+r)ᵗ`、`Ri = Rf + β(Rm − Rf)`、`y_corporate = y_treasury + Spread`。

只输出 JSON，不要任何解释或寒暄。notes 是字符串数组。格式：
{"markets":[{"id":1,"what":"...","why":"...","chain":"...","notes":["...","..."]}],"policy":[...],"tech":[...]}

下面是一条完整示范，**照这个深度和口吻写，不要更浅**：
{"id":7,
 "what":"美联储理事巴尔释放偏鹰信号，表示如果通胀继续高于 2% 目标，他可能支持加息。",
 "why":"市场此前定价的是未来降息，这番表态意味着要重新评估降息预期、给加息风险加权重，短端利率与美元同步上行。",
 "chain":"通胀持续高位 → 美联储转向鹰派 → 市场修正降息预期 → 未来政策利率预期抬升 → 短期无风险利率 Rf 上移 → 旧债性价比重估、投资者抛售 → 债券价格下跌、YTM 被动上行 → 全市场定价基准抬高 → DCF 折现率 r 上行、远期现金流现值缩水 → 高估值成长股承压；美国利率预期抬升 → 美元资产吸引力上升 → 资金流入美元资产 → 美元走强",
 "notes":[
  "鹰派核心目标：把抑制通胀放在第一位，只要通胀高于目标就倾向加息、收紧货币政策。物价稳定最重要 —— 就算加息导致经济放缓、失业率小幅上升，也是打压通胀可以接受的代价。",
  "加息存在时间滞后！今天宣布加息，不会明天物价就降下来，完整传导需要 6~12 个月。这就是通胀刚抬头时鹰派官员就呼吁赶紧加息的原因。",
  "why 债券价格与收益率永远反向变动？债券定价公式 `P = C/(1+y)¹ + C/(1+y)² + … + (C+Face)/(1+y)ⁿ`，其中票息 C 和本金 Face 发行后固定不变。一旦市场利率预期上升，投资者卖出旧债，价格 P 下跌；分子不变而 P 变小，只能是分母里的 y 变大 —— 所以到期收益率 YTM 是被动抬升的。",
  "why 折现率能压制成长股估值？DCF 折现公式 `PV = ∑ CFₜ/(1+r)ᵗ`。成长股的现金流大量发生在远期、t 很大，分母是 (1+r) 的 t 次方，r 上行时远期那几项被成倍缩小，因此估值受损远比现金流靠前的价值股严重。",
  "CAPM 资本资产定价模型 `Ri = Rf + β(Rm − Rf)`。Rf 是美债代表的无风险利率，是所有风险资产的收益基准。Rf 上涨 → 市场对股票、风险债券要求的最低回报 Ri 变高 → 同样的盈利只值更低的价格。β 越大的股票对 Rf 变动越敏感。"
 ]}

候选新闻：
{{CANDIDATES}}"""

# 若用户写了 prompt.zh.md，用它的正文覆盖上面内置模板；否则用内置。
if _PF:
    DEFAULT_PROMPT = _PF[0]

REPAIR_HINT = (
    "上一条输出不合法：{err}。请只输出符合要求的 JSON，id 必须取自候选列表，"
    "what/why/chain/notes 四个字段都不能省；chain 至少 " + str(MIN_CHAIN_HOPS)
    + " 环、用 ' → ' 连接且每环写出机制；notes 至少 " + str(MIN_NOTES)
    + " 条且是字符串数组；公式写成反引号包住的纯文本，不要 LaTeX。不要添加说明文字。"
)

CANDIDATES_SLOT = "{{CANDIDATES}}"

# prompt 里写给模型的数量要求必须跟校验层的阈值一致，否则会出现
# "prompt 要 8 环、校验要 12 环"这种自相矛盾 —— 每天都重试一次然后降级。
# 有 prompt.zh.md 时，上限直接取 front matter 里用户写的值（正文里的 {{HOPS_HI}}
# 也要填成同一个数）；没写上限时用"下限 + 宽裕区间"兜底。
def _slots() -> dict[str, str]:
    return {
        "{{TOP_N}}": str(TOP_N),
        "{{HOPS_LO}}": str(MIN_CHAIN_HOPS + 1 if not _PF_PARAMS
                           else _PF_PARAMS.get("chain_hops_min", MIN_CHAIN_HOPS)),
        "{{HOPS_HI}}": str(MIN_CHAIN_HOPS + 5 if not _PF_PARAMS
                           else _PF_PARAMS.get("chain_hops_max", MIN_CHAIN_HOPS + 5)),
        "{{NOTES_LO}}": str(MIN_NOTES + 1 if not _PF_PARAMS
                            else _PF_PARAMS.get("notes_min", MIN_NOTES)),
        "{{NOTES_HI}}": str(MIN_NOTES + 5 if not _PF_PARAMS
                            else _PF_PARAMS.get("notes_max", MIN_NOTES + 5)),
    }


def prompt_template() -> str:
    """取当前生效的 prompt 模板：config 里 template 非空就整段接管，否则用内置。

    自定义模板必须含 {{CANDIDATES}} 占位符，否则候选列表无处可插 —— 那样模型
    会凭空编新闻，比直接忽略这份模板更糟，所以缺占位符就回落内置并告警。
    """
    custom = config.get_str("llm.prompt", "template", "").strip()
    base = DEFAULT_PROMPT
    if custom:
        if CANDIDATES_SLOT in custom:
            base = custom
        else:
            log.warning("config [llm.prompt] template 缺少 %s 占位符，"
                        "已忽略自定义模板、改用内置 prompt", CANDIDATES_SLOT)

    for slot, val in _slots().items():
        base = base.replace(slot, val)

    extra = config.get_str("llm.prompt", "extra", "").strip()
    if extra:
        # 附加要求放最后。同一处冲突时后写的更容易被模型遵守，这正是我们想要的
        # —— extra 是用户当下的诉求，应当压过内置措辞。
        base += f"\n\n补充要求（优先遵守）：\n{extra}"
    return base


def build_prompt(pools: dict[str, list[Item]]) -> tuple[str, dict[int, Item]]:
    """把候选池打包成单个 prompt。返回 (prompt, id→Item 映射)。"""
    lines: list[str] = []
    index: dict[int, Item] = {}
    nid = 1

    for cat in CATEGORIES:
        pool = pools.get(cat, [])[:MAX_CANDIDATES_PER_CAT]
        lines.append(f"\n## {cat}（{CATEGORY_LABELS[cat]}）")
        if not pool:
            lines.append("（今日无候选）")
            continue
        for it in pool:
            index[nid] = it
            snippet = it.summary[:SNIPPET_CHARS].replace("\n", " ")
            date = it.published.strftime("%m-%d %H:%M") if it.published else "未知时间"
            lines.append(f"{nid}. [{it.source} {date}] {it.title}"
                         + (f" —— {snippet}" if snippet else ""))
            nid += 1

    # 用占位符替换而不是 str.format —— prompt 里有大量 JSON 花括号示范，
    # 走 format 就得把每个括号写成双份，改一次 prompt 踩一次坑。
    return prompt_template().replace(CANDIDATES_SLOT, "\n".join(lines)), index


def _extract_json(text: str) -> dict:
    """模型偶尔会裹 ```json 围栏或加前言，取第一个完整 JSON 对象。"""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        return json.loads(fenced.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("响应中找不到 JSON 对象")
    return json.loads(text[start:end + 1])


def _normalize_notes(val: object) -> list[str]:
    """把 notes 收成字符串列表。

    模型偶尔会给一整段带 `*` 的文本而不是数组，或者在数组元素里塞多行。
    这种是包装错了、内容是对的，按行拆开比整体重试划算 —— 重试要多烧一次
    上万 token，而且第二次未必更听话。
    """
    if isinstance(val, str):
        raw = val.splitlines()
    elif isinstance(val, (list, tuple)):
        raw = [line for x in val for line in str(x).splitlines()]
    else:
        raise ValueError("notes 既不是数组也不是字符串")

    out: list[str] = []
    for line in raw:
        # 模型爱在每条前面加 markdown 列表符号，页面自己会排项目符号
        cleaned = line.strip().lstrip("*·•-—>＊ ").strip()
        if cleaned:
            out.append(cleaned)
    return out


def validate(raw: dict, index: dict[int, Item]) -> dict[str, list[dict]]:
    """schema 校验。任一不合法就抛异常，触发重试或降级。

    校验得比较严：四个字段缺一不可、id 必须真实存在、不能跨板块串台。
    另外两条是 2026-09-06 改版新增的**质量下限**，不只是格式检查：
      - chain 至少 MIN_CHAIN_HOPS 环 —— 环数少就意味着机制又被压缩成结论了，
        而"看不懂"的根因正是这个，所以宁可重试。
      - notes 至少 MIN_NOTES 条，且不许出现 LaTeX（页面不引 KaTeX，渲染不了）。
    """
    if not isinstance(raw, dict):
        raise ValueError("顶层不是 JSON 对象")

    out: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        picks = raw.get(cat, [])
        if not isinstance(picks, list):
            raise ValueError(f"{cat} 不是数组")

        cards: list[dict] = []
        for p in picks[:TOP_N]:
            if not isinstance(p, dict):
                raise ValueError(f"{cat} 内元素不是对象")
            try:
                pid = int(p["id"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"{cat} 内 id 缺失或非整数")

            item = index.get(pid)
            if item is None:
                raise ValueError(f"id {pid} 不在候选池中（模型编造）")
            if item.category != cat:
                raise ValueError(f"id {pid} 属于 {item.category}，却被放进 {cat}")

            insight: dict = {}
            for field in INSIGHT_TEXT_FIELDS:
                val = str(p.get(field) or "").strip()
                if not val:
                    raise ValueError(f"id {pid} 缺少 {field} 字段")
                insight[field] = val

            for field in INSIGHT_LIST_FIELDS:
                if field not in p:
                    raise ValueError(f"id {pid} 缺少 {field} 字段")
                items = _normalize_notes(p[field])
                if len(items) < MIN_NOTES:
                    raise ValueError(
                        f"id {pid} 的 {field} 只有 {len(items)} 条，"
                        f"至少要 {MIN_NOTES} 条"
                    )
                insight[field] = items

            hops = [h for h in insight["chain"].split(" → ") if h.strip()]
            if len(hops) < MIN_CHAIN_HOPS:
                raise ValueError(
                    f"id {pid} 的 chain 只有 {len(hops)} 环（要求 ≥{MIN_CHAIN_HOPS} 环，"
                    f"用 ' → ' 连接），中间机制被跳过了"
                )

            for text in (insight["chain"], *insight["notes"]):
                hit = LATEX_PAT.search(text)
                if hit:
                    raise ValueError(
                        f"id {pid} 里出现 LaTeX 记法 {hit.group()!r}，"
                        f"公式必须写成反引号包住的纯文本"
                    )

            cards.append(_card(item, insight))

        out[cat] = cards
    return out


def _card(item: Item, insight: dict | None = None) -> dict:
    card = {
        "title": item.title,
        "source": item.source,
        "url": item.url,
        "published": item.published.strftime("%Y-%m-%d %H:%M") if item.published else "",
        # 规则模式下没有解读，用源自带摘要保底，页面会区分展示
        "raw_summary": item.summary[:200],
    }
    card["insight"] = insight or {}
    return card


# 单板块最终产出里同一来源最多几条。可在 config.toml 的 [output] max_per_source 改。
OUTPUT_SOURCE_CAP = config.get_int("output", "max_per_source", 2, lo=1, hi=10)


def rules_fallback(pools: dict[str, list[Item]]) -> dict[str, list[dict]]:
    """规则模式：取每板块打分 Top5，只给标题与源摘要，insight 留空。

    候选池阶段的单源限额（4 条）在只取 5 条时不起作用 —— 实测华尔街见闻能占满
    4/5 个市场板块席位。这里再收紧到 2 条，保证一眼能看到不同来源的视角。

    刻意不合成假解读 —— 页面会显示"今日无 AI 解读"，让降级状态一眼可见。
    """
    out: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        picked, spill, used = [], [], {}
        for it in pools.get(cat, []):
            if used.get(it.source, 0) < OUTPUT_SOURCE_CAP:
                picked.append(it)
                used[it.source] = used.get(it.source, 0) + 1
            else:
                spill.append(it)
            if len(picked) >= TOP_N:
                break
        # 限额下凑不满 TOP_N 时，用被限额挡下的高分条目补齐
        out[cat] = [_card(it) for it in (picked + spill)[:TOP_N]]
    return out


def select(pools: dict[str, list[Item]]) -> tuple[dict[str, list[dict]], str, str]:
    """返回 (板块卡片, mode, note)。mode 为 llm 或 rules。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY 未设置，降级为规则模式")
        return rules_fallback(pools), "rules", "未配置 API key"

    try:
        import anthropic
        try:
            import httpx2 as httpx    # anthropic 1.x 的 HTTP 依赖叫 httpx2
        except ImportError:
            import httpx              # 0.x 老版本用的还是 httpx
    except ImportError:
        log.warning("anthropic SDK 未安装，降级为规则模式")
        return rules_fallback(pools), "rules", "anthropic SDK 未安装"

    prompt, index = build_prompt(pools)
    if not index:
        return rules_fallback(pools), "rules", "今日无任何候选"

    # base_url 由 SDK 自己读 ANTHROPIC_BASE_URL 环境变量：走中转站时必须设置它，
    # 否则请求会打到 api.anthropic.com，中转站的 key 在那边一律 401。
    #
    # 超时必须显式给：SDK 默认 connect 只有 5 秒，中转站握手偶尔慢一拍就是
    # APITimeoutError，然后整天白白降级成规则模式。读取给足 600 秒 —— 四段解读
    # 本来就慢，16000 tokens 跑几分钟很正常。
    client = anthropic.Anthropic(
        api_key=api_key,
        timeout=httpx.Timeout(600.0, connect=20.0),
    )
    messages = [{"role": "user", "content": prompt}]
    last_err = ""

    # 循环同时承载两种重试：JSON 不合规的修复重试（≤ MAX_RETRIES 次，会把错误
    # 回喂给模型）和上游通道异常的换通道重试（≤ UPSTREAM_RETRIES 次，原样重发）。
    # 两者共用计数上限，够用且不会把一天的调用次数放大到失控。
    for attempt in range(1, MAX_RETRIES + UPSTREAM_RETRIES + 2):
        try:
            create_kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, messages=messages)
            if DISABLE_THINKING:
                create_kwargs["thinking"] = {"type": "disabled"}
            resp = client.messages.create(**create_kwargs)
            text = "".join(b.text for b in resp.content if b.type == "text")
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            # 401（key 错）这类错误重试也没用，直接降级。但走中转站时，5xx / 400
            # 往往只是这次轮到的通道坏了或把模型转给了错误的后端（Bedrock 报
            # "model identifier is invalid"）—— 换一次通道就好。所以对这类错误
            # 多试几次再放弃，别一遇错就整天降级成规则模式。
            status = getattr(exc, "status_code", None)
            transient = status in (500, 502, 503, 504) or (
                status == 400 and "invalid" in last_err.lower()
                and "model" in last_err.lower())
            if transient and attempt <= UPSTREAM_RETRIES:
                log.warning("上游通道异常（第 %d 次），换通道重试：%s",
                            attempt, last_err[:160])
                time.sleep(UPSTREAM_BACKOFF * attempt)
                continue
            log.warning("LLM 调用失败（第 %d 次）：%s", attempt, last_err)
            break        # 鉴权错 / 重试用尽，直接降级

        # 撞上 max_tokens 时 JSON 一定是半截的，后面报的会是"找不到 JSON 对象"
        # 这种误导性错误。这里先把真正的原因喊出来，省得下次对着 JSON 报错查半天。
        if getattr(resp, "stop_reason", None) == "max_tokens":
            log.warning("输出被 max_tokens=%d 截断（第 %d 次），JSON 必然不完整；"
                        "若反复出现就调小 TOP_N 或加大 MAX_TOKENS", MAX_TOKENS, attempt)

        try:
            cards = validate(_extract_json(text), index)
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = str(exc)
            log.warning("LLM 输出校验失败（第 %d 次）：%s", attempt, last_err)
            if attempt <= MAX_RETRIES:
                messages += [
                    {"role": "assistant", "content": text},
                    {"role": "user", "content": REPAIR_HINT.format(err=last_err)},
                ]
                continue
            break

        total = sum(len(v) for v in cards.values())
        log.info("LLM 精选成功（第 %d 次调用），产出 %d 条解读", attempt, total)
        return cards, "llm", ""

    log.warning("LLM 不可用，降级为规则模式：%s", last_err)
    return rules_fallback(pools), "rules", last_err
