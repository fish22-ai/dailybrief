"""精选 + AI 解读：一次 LLM 调用产出全部卡片；失败则降级为规则模式。

对应 CLAUDE.md 第七、八节。每日调用上限 2 次（首次 + JSON 非法时重试 1 次）。

这里是整个项目的价值所在。**不要让模型复述新闻** —— 标题本身就是新闻，用户
自己会读。模型要回答的是"这条为什么重要、钱怎么一环一环流动、背后是哪个机制在动"。
所以每条卡片的解读是结构化内容，而不是一句摘要：

  summary30s            30秒事实内核，3 条客观事实
  transmissionChain     金融传导全景脉络，固定 4 段（trigger/mechanism/assetImpacts/
                        timeHorizon/keySensitivity），**页面直接照着渲染**
  historicalAnalogy     历史参照系 {event, year, comparison}
  gameTheoryStakeholders 博弈各方 [{party, stance, bottomLine}]
  forwardIndicators     前瞻红线指标 [{indicator, threshold, significance}]
  takeaways             行动启示 {investor, industry, personal}
  notes                 解析（字符串数组，把链条里每个专业概念摊开讲）

2026-09-11 改版原因：上一版 chain 是一根平文本（"A → B → C"），页面为了摆出参考站
fish22-ai/dailybrief-ui 那种「4 个 STEP + 详情面板」的样子，只能用 _chain_stages()
把字符串按位置硬切成 4 段。三个字段同源，渲染出来自然是同一句话。所以这次让模型
**直接产出结构化字段**，页面不再猜。参考站的 surfaceVsCore / reflectionQuestion
用户明确要求不要，这边既不产出也不校验。

**prompt 是用户可手改的 `prompt.zh.md`**：文件存在时，正文与段数从它读取（优先于
内置 DEFAULT_PROMPT 与 config 里的数字），没有文件时回落内置。文件开头的 YAML front matter
给出段数/条数的上下限，**校验用的下限也跟着它走**，和正文里的 {{STAGES}}、
{{NOTES_LO}}~{{NOTES_HI}} 保持一致 —— 这正是"prompt 要几段、校验就要几段"的保证，
否则每天必然重试一次。用户改这份文件即可微调解读，无需改代码。

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
    CATEGORIES, CATEGORY_LABELS, CHAIN_STAGES, DIRECTION_VALUES,
    INSIGHT_LIST_FIELDS, Item,
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
    只关心四个整数参数 chain_stages_min / chain_stages_max / notes_min / notes_max。
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
    params = {"chain_stages_min": CHAIN_STAGES, "chain_stages_max": CHAIN_STAGES,
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
# max_tokens 太小会让每次输出都被截断、白烧重试。下面这几个"下限"是**质量下限、
# 不是格式检查** —— 段数少于 4、博弈方只有 1 个、前瞻指标只有 1 个，都说明模型
# 把推演压缩成了结论清单，宁肯带错误信息重试一次。
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
# 数字必须同源，否则"prompt 要 8 段、校验要 12 段"会让每天必然重试一次。
MIN_STAGES = _PF_PARAMS.get("chain_stages_min",
                            config.get_int("llm", "min_chain_stages", CHAIN_STAGES,
                                           lo=2, hi=8))
MIN_NOTES = _PF_PARAMS.get("notes_min",
                           config.get_int("llm", "min_notes", 3, lo=1, hi=12))
# 下面三条没有对应配置项：它们不是"用户会想调的旋钮"，而是结构完整性的底线。
MIN_FACTS = 3            # 30秒事实内核至少几条
MIN_STAKEHOLDERS = 2     # 博弈方至少几个（只有一个说明没在做博弈分析）
MIN_INDICATORS = 2       # 前瞻红线指标至少几个

TOP_N = config.get_int("output", "per_category", 5, lo=1, hi=10)
MAX_CANDIDATES_PER_CAT = config.get_int("output", "pool_size", 18, lo=3, hi=60)

# 公式必须是纯文本。反斜杠后面跟字母或括号在中文财经行文里几乎不可能出现，
# 出现就是模型在写 LaTeX（\frac、\sum、\( \)），页面渲染不了，带错误信息重试。
LATEX_PAT = re.compile(r"\\[A-Za-z(\[]")

DEFAULT_PROMPT = """你是一位帮我建立金融 sense 的资深买方分析师。我在准备金融行业面试，
需要的不是新闻摘要（标题我自己会读），而是**能让我把这件事的传导机制自己讲出来的解读**。

读者有两类人，**必须同时照顾到**：正在备考金融面试的人（要能自己复述推演、能被追问），
以及基本没接触过金融的人（专业术语第一次出现就必须解释清楚）。宁可多写一句把机制说透，
也不要写成结论清单，也不要卖弄术语。

下面是今天抓取的候选新闻，分两个板块。请：

1. 每个板块挑出最重要的 {{TOP_N}} 条以内。判断标准：**对利率、汇率、资产价格、
   资本流动的实际影响力**。果断剔除：公司公关稿、个股炒作、无传导链的社会新闻、
   纯人道议题。宁可少选几条真正重要的，也不要凑够 {{TOP_N}} 条。

2. 每条给出下面七段解读，全部用中文。**七段缺一不可**。

   - summary30s（30秒事实内核）：**3 条**客观事实，每条一句话。只写"谁做了什么、
     数字是多少"，客观可核查，不写观点、不写情绪、不写影响。

   - transmissionChain（金融传导全景脉络）：固定 **{{STAGES}} 段**，从事件一路推到具体资产。
     每段一个对象，字段一个都不能少：
       · step：序号，从 1 连续排到 {{STAGES}}。
       · name：这一段在干什么，**6~14 个字**的短语，像小标题（正例："政策降息与财政赤字对冲"）。
       · trigger（输入触发源）：推向下一段的那一下动作，一句话 25~45 字，不要复述标题。
       · mechanism（底层传导机理）：**最关键**，60~120 字，必须回答"为什么 A 导致 B"，
         把中间被压缩掉的机制摊开写。反例（跳步）："加息预期上升导致成长股承压"。
         正例："降息只压低超短端利率；10 年期以上长债的价格取决于未来通胀均值与国债供给。
         国债供给过剩 → 长端价格下跌 → 收益率被动飙升。"
       · assetImpacts：1~2 个受影响的大类资产，每项 {asset, direction, note}。
         asset 要具体（"10 年期美债收益率"，不要只写"债券"）；
         direction **只能是** "up" / "down" / "volatile" / "neutral" 之一；
         note 是 10~25 字的原因说明，不要重复 asset 的名字。
       · timeHorizon（传导时滞）：形如 "T+0 至 1 周"、"1 - 3 周发酵"、"2 - 3 个季度显现"。
       · keySensitivity（关键敏感监测变量）：一个具体的指标或关口，能拿去盯盘的。
         正例："10 年期 TIPS 实际收益率是否站上 2.2%"；反例："关注市场情绪"。

   - historicalAnalogy（历史参照系与经验校准）：{event, year, comparison} 三个字段。
     找**真实发生过**的同类事件，说清"当时怎么走的、和现在像在哪、又差在哪"，
     comparison 60~120 字。不要编造年份。

   - gameTheoryStakeholders（博弈各方的台前立场与真实底牌）：**2~4 个**利益方，
     每项 {party, stance, bottomLine}。party 要是具体的"谁"；stance 是台面上的公开立场；
     bottomLine 是它不妥协的真实底牌（这条最重要）。

   - forwardIndicators（前瞻红线指标）：**2~3 个**，每项 {indicator, threshold, significance}。
     threshold 要给出具体数字或状态，significance 说清触发后会引发什么，40~80 字。

   - takeaways（行动启示与认知内化）：{investor, industry, personal} 三个字段，各 60~110 字，
     分别对**投资者**（股债金汇）、**企业经营者/外贸供应链**、**普通人**（房贷/换汇/理财/消费）。
     三条都要落到具体动作，不要空喊"注意风险"。

   - notes（解析）：{{NOTES_LO}}~{{NOTES_HI}} 条字符串，把链条里每一个可能不懂的概念摊开讲，
     一条一个知识点，可以用"为什么 2Y 先动？因为……"这种设问开头。
     **不要写公式、不要用反引号、不要出现 `y↑则P↓` 这类符号简写**，用大白话讲透机制；
     涉及定价/折现/利差/收益率的概念用自然语言解释彼此关系即可。不要 LaTeX 记法。

只输出 JSON，不要任何解释或寒暄。summary30s 和 notes 是字符串数组。格式：
{"markets":[{"id":1,"summary30s":["...","...","..."],
"transmissionChain":[{"step":1,"name":"...","trigger":"...","mechanism":"...",
"assetImpacts":[{"asset":"...","direction":"up","note":"..."}],"timeHorizon":"...","keySensitivity":"..."}],
"historicalAnalogy":{"event":"...","year":"...","comparison":"..."},
"gameTheoryStakeholders":[{"party":"...","stance":"...","bottomLine":"..."}],
"forwardIndicators":[{"indicator":"...","threshold":"...","significance":"..."}],
"takeaways":{"investor":"...","industry":"...","personal":"..."},
"notes":["...","..."]}],"policy":[...]}

下面是一条完整示范，照这个深度和口吻写，**不要更浅、不要少任何字段**：
{"id":7,
 "summary30s":["美联储理事巴尔表示，若通胀持续高于 2% 目标，他可能支持加息。",
  "这是本月第三位释放偏鹰信号的美联储官员。",
  "利率期货市场对年内降息的定价随即回落。"],
 "transmissionChain":[
  {"step":1,"name":"官员表态与市场预期重构",
   "trigger":"美联储理事巴尔公开表示，如果通胀继续高于 2% 目标，他可能支持加息。",
   "mechanism":"市场此前定价的是年内降息。官员表态是美联储与市场沟通的政策工具之一，鹰派表态会直接改变交易员对未来政策利率路径的预期 —— 不是等 FOMC 开会才动，而是听到话就重新下单。",
   "assetImpacts":[{"asset":"2 年期美债收益率","direction":"up","note":"它对政策利率最敏感，降息预期回落直接推高其收益率"}],
   "timeHorizon":"T+0 至 2 天",
   "keySensitivity":"利率期货隐含的年内降息次数是否从 2 次降到 1 次"},
  {"step":2,"name":"短端利率上行与债券重定价",
   "trigger":"降息预期回落，短端无风险利率预期抬升，投资者开始抛售手里的旧债。",
   "mechanism":"债券的票息发行后就固定不变。市场利率预期一升，旧债的相对吸引力下降，投资者卖出使价格下跌；价格跌了而票息不变，到期收益率就被动抬升。这就是债券价格与收益率永远反向变动的原因。",
   "assetImpacts":[{"asset":"短久期国债基金","direction":"down","note":"净值随旧债价格下跌而回撤"},{"asset":"货币市场基金","direction":"up","note":"新发短债利息更高，资金流入"}],
   "timeHorizon":"1 - 2 周发酵",
   "keySensitivity":"2 年期与 10 年期美债的利差是否继续收窄"},
  {"step":3,"name":"定价基准抬升与估值压缩",
   "trigger":"短端美债收益率上行逐级传导到长端，全市场无风险利率基准被抬高。",
   "mechanism":"无风险利率是所有风险资产定价的基准。它一上行，股票和风险债券要求的最低回报就跟着变高；而成长股的大量盈利发生在遥远未来，远期收益折算到现在的价值缩水远比价值股严重，所以估值承压更明显。",
   "assetImpacts":[{"asset":"高估值成长股","direction":"down","note":"远期现金流折现后缩水更多，估值倍数被压缩"},{"asset":"美元指数","direction":"up","note":"高利率吸引资金流入美元资产"}],
   "timeHorizon":"2 - 4 周",
   "keySensitivity":"纳斯达克 100 指数相对道指的强弱"},
  {"step":4,"name":"实体融资成本与终端需求",
   "trigger":"长端利率上行传导到银行信贷定价，按揭与企业贷款利率同步抬升。",
   "mechanism":"银行的资金成本锚定在长端国债与掉期曲线上，长端一涨，房贷固定利率与信用债利差很快跟上。借贷成本上升会压掉一部分购房与扩产需求；若房价与投资同步走弱，就形成「名义上没加息、实体却更紧」的效果。",
   "assetImpacts":[{"asset":"美国 30 年期房贷利率","direction":"up","note":"月供变贵，购房者观望情绪加重"},{"asset":"高收益企业债信用利差","direction":"up","note":"再融资违约风险被重新定价"}],
   "timeHorizon":"2 - 3 个季度显现",
   "keySensitivity":"美国成屋签约销售指数是否连续两个月下滑"}],
 "historicalAnalogy":{"event":"格林斯潘的「利率谜题」及其逆转版","year":"2005 年",
  "comparison":"2005 年美联储连续加息，长端美债收益率却因海外央行狂买而不升反降；当前正相反：政策利率预期抬升，长端因国债供给过剩而更敏感。两次的共同点是短端政策利率管不住长端，区别在于当年是需求端的外储买盘，这次是供给端的发债压力。"},
 "gameTheoryStakeholders":[
  {"party":"美联储（货币当局）","stance":"维持双重使命平衡，警惕过早放松导致通胀二次反扑","bottomLine":"只要就业市场不断崖式失速，就绝不轻言兜底收益率曲线。"},
  {"party":"美国财政部（发债方）","stance":"保证天量发债顺利完成，尽量多发短端国库券缓解长债压力","bottomLine":"付息成本占联邦收入的比例已破 18%，承受不了长端利息长期高位。"},
  {"party":"全球对冲基金与基差套利者","stance":"利用美债现货与期货的基差做高杠杆套利","bottomLine":"对长债拍卖的需求缺口极度敏感，一有风吹草动就抛现货放大波动。"}],
 "forwardIndicators":[
  {"indicator":"10 年期美债收益率","threshold":"4.50% - 4.75%","significance":"一旦突破该区间，会触发美股风险平价策略的被动去杠杆抛售，跌幅往往被杠杆放大。"},
  {"indicator":"美联储隔夜逆回购余额","threshold":"低于 500 亿美元","significance":"缓冲垫耗尽后，财政部发债会直接抽取银行准备金，引发短端货币市场的钱荒。"}],
 "takeaways":{
  "investor":"别再用「降息＝看多长债和高成长股」的简单公式。财政赤字主导期，应偏向高股息和抗通胀的现金流资产，缩短债券久期，并用实物黄金对冲纸币信用被稀释的风险。",
  "industry":"有海外融资或大额美元贷款的企业，不要赌借贷利率会断崖式下行，应尽早用掉期把融资成本锁死；进出口企业要防强美元延续带来的结汇波动。",
  "personal":"如果你在考虑海外置业或按揭，别以为降息就意味着房贷利率随时下跌。国内理财端，全球长端高息资产仍有吸引力，分散配置比单押利率下行更稳。"},
 "notes":[
  "为什么 2 年期国债最敏感？它对政策利率最敏感，反映的是市场对未来两年利率路径的预期，所以官员一说话它先动。",
  "收益率上升债券价格下跌：债券价格和收益率呈反向变动，这是同一件事的两种说法。",
  "为什么成长股更受伤？成长股的盈利大多在遥远未来，利率上行时，远期收益折算到现在的价值缩水更明显。",
  "美元走强的原因：高利率吸引资本流入美国，对美元的需求增加，汇率就走高。",
  "鹰派 vs 鸽派：鹰派优先压通胀，只要通胀高于目标就倾向加息收紧，宁可牺牲一点增长；鸽派优先保增长保就业，对通胀容忍度高，不喜欢加息。"]}

候选新闻：
{{CANDIDATES}}"""

# 若用户写了 prompt.zh.md，用它的正文覆盖上面内置模板；否则用内置。
if _PF:
    DEFAULT_PROMPT = _PF[0]

REPAIR_HINT = (
    "上一条输出不合法：{err}。请只输出符合要求的 JSON，id 必须取自候选列表。"
    "每条必须七段齐全：summary30s（≥" + str(MIN_FACTS) + " 条字符串）、"
    "transmissionChain（恰好 " + str(MIN_STAGES) + " 段，每段含 step/name/trigger/"
    "mechanism/assetImpacts/timeHorizon/keySensitivity，assetImpacts 里 direction "
    "只能取 up/down/volatile/neutral）、historicalAnalogy（event/year/comparison）、"
    "gameTheoryStakeholders（≥" + str(MIN_STAKEHOLDERS) + " 个）、"
    "forwardIndicators（≥" + str(MIN_INDICATORS) + " 个）、"
    "takeaways（investor/industry/personal 三个都不能缺）、"
    "notes（≥" + str(MIN_NOTES) + " 条字符串数组）。"
    "不要 LaTeX 记法，不要添加任何说明文字。"
)

CANDIDATES_SLOT = "{{CANDIDATES}}"

# prompt 里写给模型的数量要求必须跟校验层的阈值一致，否则会出现
# "prompt 要 8 段、校验要 12 段"这种自相矛盾 —— 每天都重试一次然后降级。
# 有 prompt.zh.md 时，以它 front matter 里的值为准（正文里的占位符也要填成同一个数）。
def _slots() -> dict[str, str]:
    return {
        "{{TOP_N}}": str(TOP_N),
        "{{STAGES}}": str(_PF_PARAMS.get("chain_stages_min", MIN_STAGES)),
        "{{NOTES_LO}}": str(MIN_NOTES),
        "{{NOTES_HI}}": str(_PF_PARAMS.get("notes_max", MIN_NOTES + 5)),
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


def _text(val: object, pid: int, field: str) -> str:
    """取一个必填字符串字段，空字符串当作缺失。"""
    s = str(val or "").strip()
    if not s:
        raise ValueError(f"id {pid} 缺少 {field} 字段")
    return s


def _obj_list(val: object, pid: int, field: str, lo: int,
              fields: tuple[str, ...]) -> list[dict]:
    """取一个「对象数组」字段，逐项校验必填字符串子字段 + 长度下限。"""
    if not isinstance(val, list):
        raise ValueError(f"id {pid} 的 {field} 不是数组")
    if len(val) < lo:
        raise ValueError(
            f"id {pid} 的 {field} 只有 {len(val)} 个，至少要 {lo} 个"
        )
    out = []
    for i, entry in enumerate(val, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"id {pid} 的 {field} 第 {i} 项不是对象")
        out.append({k: _text(entry.get(k), pid, f"{field} 第 {i} 项的 {k}")
                    for k in fields})
    return out


def _walk_text(val: object):
    """递归吐出结构里所有的字符串，供 LaTeX 扫描用。"""
    if isinstance(val, str):
        yield val
    elif isinstance(val, dict):
        for v in val.values():
            yield from _walk_text(v)
    elif isinstance(val, (list, tuple)):
        for v in val:
            yield from _walk_text(v)


def _validate_insight(p: dict, pid: int) -> dict:
    """校验并归一化一条卡片的七段解读。不合法就抛 ValueError 触发重试/降级。"""
    ins: dict = {}

    # 1. 30秒事实内核
    ins["summary30s"] = _normalize_notes(p.get("summary30s") or [])
    if len(ins["summary30s"]) < MIN_FACTS:
        raise ValueError(
            f"id {pid} 的 summary30s 只有 {len(ins['summary30s'])} 条，"
            f"至少要 {MIN_FACTS} 条"
        )

    # 2. 金融传导全景脉络：恰好 MIN_STAGES 段，每段的七个字段一个都不能少。
    # step 用**位置**重新编号，不采信模型给的数字 —— 它偶尔从 0 开始或重复编号，
    # 而真正决定页面顺序的是数组位置，与其为这个重试一次不如直接归一化。
    chain = p.get("transmissionChain")
    if not isinstance(chain, list):
        raise ValueError(f"id {pid} 的 transmissionChain 不是数组")
    if len(chain) < MIN_STAGES:
        raise ValueError(
            f"id {pid} 的 transmissionChain 只有 {len(chain)} 段，至少 "
            f"{MIN_STAGES} 段 —— 段数不足说明中间机制被跳过了"
        )
    nodes: list[dict] = []
    for k, node in enumerate(chain, 1):
        if not isinstance(node, dict):
            raise ValueError(f"id {pid} 的 transmissionChain 第 {k} 段不是对象")
        impacts = node.get("assetImpacts")
        if not isinstance(impacts, list) or not impacts:
            raise ValueError(
                f"id {pid} 的 transmissionChain 第 {k} 段 assetImpacts 是空的"
            )
        clean: list[dict] = []
        for im in impacts:
            if not isinstance(im, dict):
                raise ValueError(f"id {pid} 第 {k} 段的 assetImpacts 元素不是对象")
            direction = str(im.get("direction") or "").strip().lower()
            if direction not in DIRECTION_VALUES:
                raise ValueError(
                    f"id {pid} 第 {k} 段的 direction 是 {direction!r}，"
                    f"只能是 {'/'.join(DIRECTION_VALUES)} 之一"
                )
            clean.append({
                "asset": _text(im.get("asset"), pid, f"第 {k} 段 assetImpacts.asset"),
                "direction": direction,
                "note": _text(im.get("note"), pid, f"第 {k} 段 assetImpacts.note"),
            })
        nodes.append({
            "step": k,
            "name": _text(node.get("name"), pid, f"第 {k} 段 name"),
            "trigger": _text(node.get("trigger"), pid, f"第 {k} 段 trigger"),
            "mechanism": _text(node.get("mechanism"), pid, f"第 {k} 段 mechanism"),
            "assetImpacts": clean,
            "timeHorizon": _text(node.get("timeHorizon"), pid, f"第 {k} 段 timeHorizon"),
            "keySensitivity": _text(node.get("keySensitivity"), pid,
                                    f"第 {k} 段 keySensitivity"),
        })
    ins["transmissionChain"] = nodes

    # 3. 历史参照系
    hist = p.get("historicalAnalogy")
    if not isinstance(hist, dict):
        raise ValueError(f"id {pid} 的 historicalAnalogy 不是对象")
    ins["historicalAnalogy"] = {
        k: _text(hist.get(k), pid, f"historicalAnalogy.{k}")
        for k in ("event", "year", "comparison")
    }

    # 4. 博弈各方 / 5. 前瞻指标
    ins["gameTheoryStakeholders"] = _obj_list(
        p.get("gameTheoryStakeholders"), pid, "gameTheoryStakeholders",
        MIN_STAKEHOLDERS, ("party", "stance", "bottomLine"))
    ins["forwardIndicators"] = _obj_list(
        p.get("forwardIndicators"), pid, "forwardIndicators",
        MIN_INDICATORS, ("indicator", "threshold", "significance"))

    # 6. 行动启示：三个视角一个都不能缺
    take = p.get("takeaways")
    if not isinstance(take, dict):
        raise ValueError(f"id {pid} 的 takeaways 不是对象")
    ins["takeaways"] = {
        k: _text(take.get(k), pid, f"takeaways.{k}")
        for k in ("investor", "industry", "personal")
    }

    # 7. 解析
    for field in INSIGHT_LIST_FIELDS:
        if field not in p:
            raise ValueError(f"id {pid} 缺少 {field} 字段")
        ins[field] = _normalize_notes(p[field])
        if len(ins[field]) < MIN_NOTES:
            raise ValueError(
                f"id {pid} 的 {field} 只有 {len(ins[field])} 条，"
                f"至少要 {MIN_NOTES} 条"
            )

    # LaTeX 页面渲染不了（零依赖静态站，不引 KaTeX），出现就重试
    for text in _walk_text(ins):
        hit = LATEX_PAT.search(text)
        if hit:
            raise ValueError(
                f"id {pid} 里出现 LaTeX 记法 {hit.group()!r}，"
                f"公式必须写成反引号包住的纯文本"
            )
    return ins


def validate(raw: dict, index: dict[int, Item]) -> dict[str, list[dict]]:
    """schema 校验。任一不合法就抛异常，触发重试或降级。

    七段解读缺一不可、id 必须真实存在、不能跨板块串台。段数/博弈方/前瞻指标
    的条数下限是**质量下限而非格式检查** —— 少了就意味着推演被压缩成了结论清单，
    而"看不懂"的根因正是这个，所以宁可带错误信息重试一次。
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

            cards.append(_card(item, _validate_insight(p, pid)))

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
