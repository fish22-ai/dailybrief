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

## 前端改版（2026-09-10 晚，只动 build_site.py + config.toml[site]）

用户对手机端提的是一整套"抽卡 App"诉求，已全部落地：

- **站点改名**：`知势|Daily Brief`；标题下的小字加回来了，改成
  `全球政经大事 · 趋势与影响`（`config.toml [site]` 的 name / tagline）。
  PWA `short_name` 取 `|` 前面那截（知势），别截成半个英文单词。
- **卡组跨天连续**：轨道里依次是 `[前一天][当天][后一天]`，打开时停在当天的第一张
  （`<html data-page>` + rect 差值定位，不依赖 offsetParent）。计数器是**当天**的
  i/n，跨天的卡片带 `.daychip` 日期标签。滑过当天不再卡住。
- **手机一屏一张卡，但页面照常整页纵向滚动**：卡片做得够短（标题/来源/发生了什么/
  为什么在意/金融传导都塞进一屏），字多时由页尾脚本把卡内字号按 `--fit` 从 1 缩到
  **0.65**（所有字号都写成 `calc(Xpx*var(--fit,1))`），不做文字截断。
  **用户明确否掉了"卡内局部滚动"**（原话："做成内嵌的用起来感觉更憋屈"）——
  卡内不设滚动容器，超了就让它超，页面整体滚，和普通网页一致。
  同理**叠卡（stacked card）做过又删了**，不要再加回来。
  实测：模拟 390×844 全部 fit=1；390×667 缩到 0.76~0.96，两者卡高都不超预算。
- **历史归档** `render_archive()`：年 → 月 → 日期三级原生 `<details>`，**默认全收起**，
  日期只写"几号"、当天仍高亮。**它现在不在页面流里**，而是底部功能栏「事件归档」
  抽屉的内容。
- **英文源原文折叠**（2026-09-10 二轮）：`_is_english()` 按"英文字母 ≥12 且多于汉字"
  判断英文源（防「iPhone 17 发布会」这类误判），标题 + 摘要收进 `.orig` 这个
  `<details>`，手机端默认收起、只显示中文解读。**桌面端不折** —— 靠 author CSS
  `.orig>*:not(summary){display:block}` 覆盖浏览器给 `details` 的 UA `display:none`
  （作者样式优先于 UA 样式），已实测桌面端 `summary` 为 none、正文为 block。
- **底部固定功能栏** `.tabbar` + 三个抽屉 `.sheet`：事件归档 / 我的收藏 / 设置。
  一次只开一个，遮罩、✕、Esc 都能关；`<noscript>` 里把抽屉摊平成普通区块，
  没 JS 也能用归档。抽屉内部可以滚（max-height:78vh），**卡片里依旧没有内嵌滚动**。
- **收藏**：卡片右上角 `.fav` 星标，存 `localStorage['dailybrief.favs']`；锚点是
  `<article id="c-<day>-<i>">`，列表点进去是 `<day>.html#c-<day>-<i>`，滑卡脚本
  读到 hash 就直接定位到那张卡。列表标题取中文 `what` 的前 44 字（英文源标题是英文，
  列表里不好认）。设置项存 `dailybrief.showOrig`。

踩坑与验证方式：headless Chrome 的 `--window-size` 在 Windows 上有最小宽度（390 会
被撑到 490），测真机尺寸要注入 `.wrap{width:390px!important;height:844px!important}`
再 `--dump-dom` 读布局数字；截图这条路走不通（Read 读不了 png）。
`tests/test_core.py` 现有 **5 项失败**（每板块 5 条 ×3 + prompt 公式 ×2），是上一轮
改 `per_category=4`/prompt 去公式化留下的，与前端无关；build_site 那一段全过。

## 视觉改版（2026-09-10 深夜，参考 fish22-ai/dailybrief-ui）

用户给了个 React 参考站（`dailybrief-ui`，Tailwind+React，功能很多但用户只要样式）。
只借了**样式层**，信息架构不变（仍是 原文link + 经济传导 + 解析），纯 `build_site.py`
改动，没有引任何依赖：

- **换皮肤**：冷蓝 → 暖纸编辑部风。`:root` 全套变量重排：米白底 `#faf8f3`、墨字
  `#191918`、砖红主强调 `--accent:#b44322`（市场/链接/收藏）、深青次强调
  `--accent2:#1c4e4f` + `--chain:#1c4e4f`（传导），policy 用赭金 `#a8690f`。
  加了 `--serif`（Georgia + 宋体），`h1` 和卡片标题改衬线。
- **小节标题** `.lb`：从灰字改成「色点 + 加粗标签」，what=砖红、why=深青、chain=传导色。
- **传导链升级** `render_chain()`：每环加编号圆点 `.hn`，读起来像一条编号传导管线
  （`<span class="hop"><i class="hn">1</i>xxx</span>`）。零 JS。
- **卡片元信息** `.card-meta`：卡片顶部一行「传导 N 环 · 解析 M 条」，展开前心里有数。
- **footer 品牌行**：logo 方块 +「天下大势 · 明金融传导 · 深内化于心」。
- **明确不做**（参考站有但砍了）：自选解析/搜索/分类筛选/Ask AI/分享卡片/传导自动播放/
  focus mode —— 用户只要精简。

**二轮（照 UI 头卡改 + 行动启示）**：
- 删掉了「传导 N 环 · 解析 M 条」那个 `card-meta` 行（用户嫌丑）。
- 每个小节改成**研读卡**：白卡 + 「深色图标块(chip) + 衬线标题(+副题)」头卡，参照
  reference 的 TransmissionVisualizer/DeepAnalysis 头卡。`.lb` 色点样式删了；
  `.notes` 折叠块改名为 `.fold`（`_collapsible` 的 details 不再自带 `seg` 卡样式，
  由外层 `sec()` 包）。
- 卡片：`article` 变透明，`.news` 和每个 `.seg` 各是独立白卡，`.card-body` 栅格加 gap。
- **行动启示**：`core/models.py` 加 `INSIGHT_OPTIONAL_LIST_FIELDS=("actions",)`，
  `select.validate()` 里 actions 可选（给了就校验、没有不重试），`prompt.zh.md` 加了
  actions 说明和样例 —— **下一次跑 LLM 才会有这字段**，现有 4 天数据没有，页面不显示
  该卡。用户还没决定要不要补跑旧四天。
- 手机 667 高下收紧内边距后 14/15 张卡不超预算，最长 1 张超 17px 由整页滚动兜底。
  深浅两套色板、theme-color、manifest 都随换肤同步过。

**三轮（顶栏品牌 + 经济传导脉络 + 英文桌面修复 + 图标换点）**：
- 顶部改品牌区：`header .logo`（知字块）+ `BRAND`（`SITE_NAME.split("|")[0]+" DailyBrief"`）
  + 日期/徽章，副标题字号按 UI（h1 22px 衬线、tagline 13px）。
- **金融传导 + 解析合并成「经济传导脉络」一张卡**：`render_stages()` 把主链均分成
  4 段（`_chain_stages()`，纯启发式重组原文、**不烧 LLM**），每段一个 `<details class="step"
  name="chain">` 步骤条，点开看 触发源/传导逻辑/资产影响/监测指标；解析 `.fold` 收在
  卡底部。「；」支线不进步骤条、原样列在下面；环数<4 退回旧的横向流式。
- **英文 source 桌面空白的根因**：JS `applyOrig()` 把 `.orig` 全设回 open=false，桌面全靠
  那条 CSS 覆盖扛着，浏览器用 `content-visibility:hidden` 时覆盖失效就空白。
  修法：桌面端（≥761px）JS **强制 `open=true`**，不赌覆盖；CSS 顺带加
  `content-visibility:visible` 兜底 no-JS。
- **"核/因"图标块删了**，`sec()` 头卡改回色点（8px 圆点，what=砖红/why=深青/chain=传导绿）。
- 测试更新：四段标签改查「经济传导脉络」；新增 4 阶段/监测指标/步骤条三项断言。

**四轮（顶栏照 UI + 传导步骤条重做 + 删两行）**：
- 顶栏照 dailybrief-ui masthead：**势** logo（38px 深色圆角块）+「知势 DailyBrief」
  （DailyBrief 缩小变灰）+ 副标题 `header .sub`（全球政经大事 · 趋势与影响），
  底部一条分隔线。字体按 UI：h1 19px 衬线、sub 12px、logo 19px 勢。
- **删了两行**：「左右滑动 · ‹ › 翻卡…」deck-hint 和 footer 品牌行
  （天下大势 · 明金融传导 · 深内化于心）。fit 预算里对应的 sel 改成了
  `['header', '.deck-ctl']`。
- **传导步骤条重做**照 reference 的 TransmissionVisualizer：`.steps` 改 grid
  （手机 2 列 / 桌面 4 列）；步骤卡 = mono「STEP 0n」徽章（选中时反色 ink 底）
  + `stepl` 标题**完整换行、不省略**（没有 line-clamp）；选中态 ink 边框 + 底部
  砖红细条。**详情面板在整个模块下方统一切换，不跟着某个 Step 列走**：选 Step
  用原生 radio（`render_stages(chain, cid)` 每张卡独立 radio 组 `chain-<cid>`），
  面板显隐靠 CSS `:has()`（`.steps:has(input[value="n"]:checked) ~ .step-panels
  .panel-n{display:block}`）—— 零 JS、键盘方向键可切。面板 = `.step-hd`
  （序号圆点 + 衬线阶段名）+ `.step-grid` 双栏 `.sf` 小卡（触发源/传导逻辑/
  资产影响/监测指标）。
- 手机 667 高下卡片都在预算内（顶栏变高后预算自动跟着缩，探针里写死的 536
  只是硬编码参考值，别拿它当标准）。

参考站里 `transmissionChain` 是「可点击分步 + 自动播放」的结构化数据；我们是平文本
渲染，没接它的数据模型。README/MEMORY 都保持"一屏一张卡、整页滚动、无卡内滚动"基调。

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

## 改版（2026-09-11 下午，七段 → 四段 + 简单版式 + 科技板块）

- **背景**：凌晨那版（七段结构化解读 + 复杂版式，存档在 commit `75e0a6c`）用户觉得
  「内容过于复杂」，要退回 **9.10 那一版**的样子。
- **内容回四段**：`prompt.zh.md` / `core/select.py` / `core/models.py` /
  `scripts/build_site.py` / `tests/test_core.py` 退到 `7f20cd9`；其余（抓取、打分、
  `run_daily.py`、config 大部分）保持凌晨状态。四段的字段常量、校验、测试是一套，
  必须成套退 —— 只改一两处会每次更新自相矛盾 → 重试 → 降级成规则模式。
- **版式**：简单四段版式 + 暖色调（`#faf8f3/#b44322/#1c4e4f`）+「勢」logo 头图，
  另加 `.orig` 英文原文折叠（手机 ≤760px 默认收起、点「查看英文原文」才展开；
  桌面 ≥761px 用 author CSS 覆盖 details 的 display 摊平）。
- **科技板块**：`CATEGORIES` 加 `"tech"`（标签「科技 · AI 与产业」）；registry 加
  Ars Technica / The Verge / Bloomberg Technology / 量子位 四个源（实测 20/10/20/10 条）；
  `scoring.py` 单开 `_CORE_TECH` 等三张词表 —— 金融词表会把「英伟达发布新架构」
  判成无关而拦在候选池外。`per_category=1` × 3 板块 = 每天 3 条。
- **省 token**：四段每条约 1KB，七段约 6.5KB（实测 9.11 数据）。3 条四段 ≈ 3KB/天，
  约为原「2 条七段」的 1/4；prompt 也从 14.8KB 瘦回 6.9KB。`max_tokens` 16000 → 8000。
- **日更入口加 `--latest`**：`scripts/daily.bat` 与 `.github/workflows/daily.yml` 都改成
  只渲最新一天。不加会把 `data/` 下所有日期重渲 —— 9.11 是七段数据，渲出来是空卡，
  9.6/9.7/9.9 的复杂版也会被覆盖。
- **站点现状**：`site/2026-09-06,07,09,11.html` 仍是复杂版；`2026-09-10.html` 和
  9.12 起是简单新版式。
- **测试**：`tests/test_core.py` 有 5 条在本次改版前就已经是红的（硬编码期望
  `per_category=5`、断言旧 prompt 的公式要求）。已修：限额相关几条临时把 `TOP_N`
  抬到 5 测完还原，prompt 断言改成验「不要写公式 / 禁用 LaTeX 记法」。现在全绿。
- **`apply_config.py --check` 仍报不同步**：`config.toml` 的 `cron = "0 8 * * *"` 与
  `daily.yml` 的 `0 0 * * 1` 对不上。这是改版前就有的，没动 —— 本机日更由 Windows
  任务计划程序负责，云端那个 workflow 的 schedule 本来也被 `workflow_dispatch` 门住。

## PWA（2026-09-11 晚，装到手机当 App 用）

- 微信小程序 / App Store 都上不了：小程序要求域名 ICP 备案（github.io 备不了）+
  新闻类目资质（个人主体拿不到）；App Store 有 4.2 最低功能、5.2.1 内容版权、
  且没有「只给自己装」的通道（TestFlight 90 天要审、Ad Hoc 要 $99/年）。
  → 自用走 PWA。
- `build_site.py` 新增两块，都写在 site/ 下：
  - `sw.js`：service worker。HTML 走 network-first（联网永远拿最新，断网回落缓存），
    图标/manifest 走 cache-first。缓存名带构建日期，页面一更新旧缓存整批清掉。
    **和 manifest 一样，`--date` 单渲一天时也会重写** —— 否则装到手机上的壳会停旧版。
  - 页面末尾注册 service worker（`SW_REGISTER`），失败静默吞掉。
- `manifest()` 加了 `icons`（192/512，purpose `any maskable`）和 `scope: "./"`。
  Chrome 必须同时看到 icons **和** service worker 才给真正的「安装应用」
  （独立窗口）；缺一样就只能加到主屏幕当书签。
- 三张图标是**现成的二进制文件**，不由 build_site.py 生成（纯 Python 没法光栅化汉字）：
  `site/icon-192.png`、`site/icon-512.png`、`site/apple-touch-icon.png`，
  暗底 `#191918` + 白色「勢」（SimSun Bold，字号 0.6×边长，留够 maskable 安全边距）。
  重做方法：System.Drawing 画一遍，字形用 `[char]0x52E2` 传，避免中文编码坑。
- 页面 `<head>` 加了 `<link rel="apple-touch-icon">`；iOS 另靠已有的
  `apple-mobile-web-app-*` 三个 meta。
