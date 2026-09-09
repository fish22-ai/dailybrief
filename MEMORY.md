# DailyBrief 工作进展记忆

> 给下一个会话看的断点记录。设计原则与完整实测记录在 `../CLAUDE.md`，
> 使用说明在 `README.md`，这里只记「做到哪了、为什么这么做、下一步做什么」。
> 最后更新：2026-09-10（自动化切到本机任务计划程序 + 开机补跑）

## 一句话现状

每日金融 sense 站已全链路跑通并通过 104 项单测，**规则模式**可用（能抓能筛能出页面），
**真实 API key 调用在五字段时代验证过一次**，**解读已改版为四段结构（what/why/chain/notes）**。
自动化已切到**本机 Windows 任务计划程序**（每周一 08:00 + 开机补跑），
GitHub Actions 定时已停用（第三方 key 云端不可用）。

## 项目定位（这是最重要的一条）

用户的原话：「我是想每天 develop 金融 sense 来面试、拓展知识面的，记录新闻我又不看，有啥用？」

所以：
- **这不是新闻聚合站**。价值在 AI 解读，不在条目本身。
- 板块只有两个：`markets`（市场·货币·金融）、`policy`（政策·地缘大事件，即"特朗普咋了"这类）。
- **AI 不是板块**，只是右栏的解读能力。游戏板块、AI 资讯板块都已明确移除，不要再加回来。
- 页面左栏 1/3 是新闻本体，右栏 2/3 是解读。解读是阅读重心。

## 关键决策与踩过的坑

按重要性排序，改代码前先看这一节，能省掉重复踩坑的时间。

### 1. 相关性闸门是必需的一层，不能省
用户看到金融板块混进「蒙特利尔三明治店换供应商」而提出质疑。根因不是排序问题 ——
BBC Business / SCMP 这些频道本身混大量软新闻，光靠来源权重和时间衰减区分不了，**必须看内容**。
`core/scoring.py` 的 `relevance()` 打 0~1 分，低于 `0.30` 不进候选池，实测拦下率约七成。

### 2. 英文词表必须按词边界匹配
子串匹配让 `rout`（暴跌）命中 `Route 66`、`euro` 命中 `Europeans`，旅游报道拿到 0.375 分。
现用 `\b<word>(?:s|es|ed|ing)?\b`，词尾变化要允许，否则 `tariff` 匹配不到 `tariffs`。
中文没有词边界概念，仍用子串。

### 3. 一词多义的词要单独处理
`surge` / `strike` / `war` / `attack` 放 `_AMBIGUOUS`，只有同现 `_MARKET_CONTEXT` 里的
金融或地缘语境词时才计核心分。否则"北海道蟑螂数量激增（surge）"被当成市场异动。

### 4. robots.txt 不能用 `RobotFileParser.read()`
它内部走 urllib 默认 UA，Fed / OpenAI / FT 对该 UA 返 403，而 robotparser 把 403 当
"全站禁止" —— 第一次跑的时候一大半源被误判跳过。必须用带正常 UA 的 requests 取回
文本再 `parse()`，取不到按"无限制"处理。见 `core/http.py`。

### 5. 央行抓不了，这是硬约束
`pbc.gov.cn/robots.txt` 只允许 Baiduspider。项目遵守 robots 不绕过，国内货币政策
靠华尔街见闻快讯 + 证监会转述。**不要试图绕过这条**，用户认可了这个取舍。

### 6. 官方源需要 7 天宽窗口
Fed / ECB / 白宫 / USTR / Economist / CNBC Finance 常常几天才发一条，36 小时默认窗口
会让它们几乎永远进不了候选池。传 `window_hours=SLOW_WINDOW_HOURS`。
代价是同一条连着几天出现 → 用 `data/seen.json` 解决（只排除**早于今天**的记录，
所以同日重跑幂等）。

### 7. 单源限额要做两层
候选池层 4 条（`run_daily.py: PER_SOURCE_CAP`）+ 产出层 2 条（`select.py: OUTPUT_SOURCE_CAP`）。
只做候选池那层的话，最终取 5 条时华尔街见闻一家能占 4/5 个席位。

### 8. `dedup()` 的返回顺序不是展示顺序
它按"同一事件保留哪条"的优先级（来源权重优先）排的。构建候选池必须重新按 `score` 排，
否则规则模式取前 5 会把权重高但过时的条目顶到最前。

### 9. 解读是结构化四段，不是一句摘要（2026-09-06 晚从五字段改过来）
`what` / `why` / `chain` / `notes`。**`chain`（金融传导，` → ` 连 8~12 环）是核心**，
面试考的就是因果推演能力；`notes` 是字符串数组，把链条里每个专业概念摊开讲。

改版起因是用户原话「目前的解读我看不懂」。根因不是写得不对，而是**中间机制被压缩掉了**：
旧 `chain` 只有 3~4 环，从"官员转鹰"一步跳到"成长股承压"，中间的 Rf、折现率、现值
三步全省了；旧 `term` 只讲一个概念，链条里其余专业词全靠猜。所以：
- 链条拉长到 8~12 环，**每环必须写出机制而不只是结果**，prompt 里给了正例反例；
- `term` 扩成 `notes` 数组（4~8 条），涉及定价/折现/利差就必须给公式并讲透公式怎么动；
- **`watch`（盯什么）删掉** —— 用户要的是理解机制，不是盯盘；
- 公式一律**反引号包住的纯文本**（`` `PV = ∑ CFₜ/(1+r)ᵗ` ``），**不用 LaTeX**：
  页面零依赖、不引 KaTeX，LaTeX 渲染不出来，校验层会直接拦下重试。

校验里 `chain` 少于 7 环、`notes` 少于 3 条、出现 LaTeX 都整体重试 ——
**这是质量下限，不是格式检查**。旧归档没有 `notes`，渲染器回落到旧标签，
重跑 `build_site.py` 不会让历史页面掉内容。

### 10. 规则模式下不合成假解读
`insight` 留空，页面显示「今日无 AI 解读（规则模式）」。拿 RSS 摘要冒充解读会污染判断。

### 11. Federal Register 的 `fields[]` 白名单很严
`presidential_document_type` 不是合法字段，带上返 400。只能取
`title` / `publication_date` / `html_url` / `abstract`。

### 12. Windows 必须装 tzdata
系统自带 Python 没有 IANA 时区库，缺了 `ZoneInfo("Asia/Shanghai")` 直接报错。

### 13. 中转站 key 绑定 deepseek-v4-flash，且默认输出思考块（2026-09-10 实锤）
用户的 agentrouter key（sk-wIIz...AbjK）只对 `deepseek-v4-flash` 有通道/额度：
claude-opus-5 走它报 402「Budget pool quota has been exhausted」（池无额度/无通道）。
裸 requests 直连还被它 401「unauthorized client」拦（客户端指纹校验），必须走
anthropic SDK。**deepseek 默认输出 thinking 块，实测思考占掉 ~85% 输出预算**，
16000 max_tokens 正文还没写就被截断、白烧重试。处理（已落进 config.toml）：
- `[llm] model = "deepseek-v4-flash"`（当前 key 只能用它）
- `[llm] disable_thinking = true` → SDK 传 `thinking={"type":"disabled"}`，中转站认
- `[output] per_category = 4`（thinking 关掉后 9 张仍贴 16000 上限）
换回 claude key 时：模型改回 claude-opus-5、disable_thinking 可关、每板块可回 5。

## 验证到什么程度

| 部分 | 状态 |
| --- | --- |
| 抓取 → 闸门 → 去重 → 打分 → 规则产出 → 渲染 | 真实跑通，19/21 源有产出，stable 源无空缺 |
| 规则层单测 `tests/test_core.py` | 104 项全通过，含 14 项闸门用例 + 四段解读校验 + 渲染用例 |
| LLM 路径（成功/重试/两次失败降级/API 报错降级/围栏包裹） | 用 mock SDK 端到端验证，调用上限 2 次已确认 |
| 前端双栏、传导链一环一格、公式等宽块、旧归档回落、降级占位 | 注入用户给的两条示例解读验证通过 |
| **真实 API key 调用** | **两次都验证过**（五字段 17:35、四段 23:00）—— 详见下节 |
| **自动化调度** | **未配置** —— 见下面待办 |

## 四段解读改版（2026-09-06 晚）

用户原话：「目前的解读我看不懂」。改动落在四个文件：
- `core/models.py`：`INSIGHT_FIELDS` 拆成 `INSIGHT_TEXT_FIELDS`（what/why/chain）
  + `INSIGHT_LIST_FIELDS`（notes，是数组，校验时要分开处理）
- `core/select.py`：prompt 重写并塞进**一条完整示范解读**（示范少不得，不给示范
  模型就退回结论式短链）；新增 `MIN_CHAIN_HOPS=7`、`MIN_NOTES=3`、LaTeX 拦截；
  `MAX_TOKENS` 8000 → 16000（用户指定的上限）、`MAX_RETRIES = 1` 显式写成常量；
  prompt 组装改用占位符替换（原来的 `str.format`
  在满是 JSON 花括号的 prompt 里每改一次都要数括号）
- `scripts/build_site.py`：`render_chain()` 按 ` → ` 拆成一环一格、`；` 支线另起一行；
  `render_note()` 处理反引号公式（长的成等宽块、短的行内 code）与开头加粗；
  旧五字段归档回落渲染
- `tests/test_core.py`：82 → 104 项

**已用真实 key 跑通并重新渲染**（2026-09-06 23:00），`data/2026-09-06.json` 与
`site/` 都是四段格式了。实测质量：链条 11~14 环、解析 6~8 条、每条 2~5 个公式，
没有一条写 LaTeX、没有一条环数不足 —— prompt 里那条完整示范起了作用。

**踩到的坑：首次调用撞上 `max_tokens=16000` 被截断**，JSON 半截，`_extract_json()`
报的是"响应中找不到 JSON 对象"这种完全看不出真因的错，重试才成功。所以
`select()` 里加了 `stop_reason == "max_tokens"` 的单独告警。9 张卡片就顶到上限，
说明四段解读比五短字段费得多。**上限是用户指定的 16000，要省那次重试就调小
`TOP_N`（5 → 4），别偷偷加大上限。**

## 真实 LLM 调用实测结果（2026-09-06 下午，**五字段时代**）

用户提供的 key 是**第三方中转站**（非 Anthropic 官方端点）：`claude-opus-5` 可用，
`claude-sonnet-5` 返回 503「当前分组 core 下无可用渠道」。项目用的是 opus-5，没问题。

- 首次输出把一条 markets 候选放进 `policy` 数组 → 被 `validate()` 的跨板块检查拦下
  → 带错误信息重试一次后通过。**跨板块串台是最常见的失败模式**，这条校验必须保留。
- 产出 markets 5 条 + policy 4 条（policy 只给 4 条是模型主动判断的，prompt 里明确
  写了"宁可只选 3 条真正重要的，也不要凑够 5 条"，属预期行为）。
- 耗时约 2.5 分钟（两次调用），合计约 1.1 万输入 + 6 千输出 token。
  **四段解读的输出量约是这个的三到四倍**，所以耗时会明显拉长，别以为是卡住了。
- 当时判断"解读质量达标"，但用户实际读下来的结论是**看不懂** —— 传导链四五环
  看着像推演，中间机制其实被压缩成了结论。这是四段改版的直接起因，
  别再拿"环数看着够多"当质量标准。

## 待办（按优先级）

1. **把 key 写进用户环境变量**（仍未持久化，没做就会整天 rules 模式）：
   cmd：`setx ANTHROPIC_API_KEY "<key>"`，然后重开终端。key 是第三方中转站的，
   不要提交进版本库。BASE_URL 已是用户级 `https://agentrouter.org`。
2. **本机自动化已完成并注册**：`scripts/install_task.ps1` 注册了「DailyBrief」任务，
   每周一 08:00（本地时间）+ `StartWhenAvailable` 开机补跑 + 单实例
   （`MultipleInstances=IgnoreNew`）；卸载用 `scripts/uninstall_task.ps1`。
   `scripts/daily.bat` 已加固：干净工作区预检、失败即退出、只 add data/site、
   push 失败保留本地提交下次续推。**首次真实运行前工作区仍是脏的，任务会拒绝执行**，
   需先手动同步/清理（main 分叉 ahead 1 / behind 6）。
3. 每天要不要省掉那次截断重试？把 `TOP_N` 从 5 调到 4 就不会撞 `max_tokens=16000`。
   代价是每板块少一条。目前维持 5 条 + 一次重试。
4. ~~闸门阈值偏宽松~~ **已确认不用改**：实测 LLM 会二次过滤掉过闸的软新闻
   （SCMP 的"海外房产市场赢家""中暑职业病"都没被选中），闸门只需保证不漏掉硬新闻。
   阈值维持 `0.30`。**注意 2026-09-06 晚这条"海外房产市场赢家"被选进了 policy** ——
   四段 prompt 的二次过滤没有五字段那次严，若反复出现软新闻就收紧 prompt 的剔除措辞。
5. 国内政策源偏薄（央行缺位）。若觉得不够，可考虑商务部、发改委的公开列表页。

## 环境事实

- 代码：`D:\吃鱿鱼的鱿鱼\dailybrief\`，**已是 git 仓库**，远端
  `https://github.com/fish22-ai/dailybrief.git`。**当前 main 与 origin/main 分叉
  （ahead 1 / behind 6）且工作区有未提交修改** —— 自动任务会因此拒绝运行，需先人工处理。
- Python 3.13.2 在 `C:\Users\吃鱿鱼的鱿鱼\AppData\Local\Programs\Python\Python313\python.exe`；
  依赖已 `--user` 装好（requests / feedparser / bs4 / lxml / anthropic / tzdata）。
- `ANTHROPIC_API_KEY` **用户级未持久化** → 计划任务会走规则模式；`setx` 后重开终端。
- 用户级 `ANTHROPIC_BASE_URL=https://agentrouter.org` 已设；Claude Code 会话自带
  `ANTHROPIC_AUTH_TOKEN`，**SDK 会优先用 AUTH_TOKEN**，拿用户的 key 跑时要
  `env -u ANTHROPIC_AUTH_TOKEN`，否则用的不是那把 key。
- 中文输出需要 `PYTHONIOENCODING=utf-8`；`daily.bat` 必须 CRLF（`.gitattributes` 锁定）。
