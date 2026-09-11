# DailyBrief

每天自动更新一次的静态站，帮你**建立金融 sense**，而不是记录新闻。

两个板块：**市场·货币·金融** 和 **政策·地缘大事件**。一条新闻一张卡片，桌面宽屏
左边是新闻本体，右边是 AI 解读，每一节一张**研读卡**：发生了什么 · 市场为什么在意 ·
**经济传导脉络**（链条拆成 4 阶段步骤条，点开看触发源/传导逻辑/资产影响/监测指标，
解析折叠在卡底部）· 行动启示。卡片横向滑（手机左右滑、桌面 ← → 或按钮），
**滑过当天会接着前一天 / 后一天**，不受日期边界限制；手机上一屏就一张卡，像抽卡一样翻。

设计原则与全部实测记录见 [`../CLAUDE.md`](../CLAUDE.md)。

## 改配置（不用动代码）

常改的东西都在根目录的 **`config.toml`**：更新时间、每板块条数、站点名称、
AI 解读的写作要求。文件里每一项都有注释说明和可以直接抄的例子。

```toml
[schedule]
cron = "0 8 * * *"          # 每天 08:00，按下面的 timezone 算，不用换算 UTC
timezone = "Asia/Shanghai"

[output]
per_category = 1            # 每个板块出几条（现在是 1；补上科技板块后即每天 3 条）

[site]
name = "每日金融 sense"      # 网页标题

[llm.prompt]
extra = ""                  # 想调解读口吻就写在这，例："解析多举一个现实例子。"
```

改完之后：

| 改了哪一节 | 要跑什么 |
| --- | --- |
| `[schedule]` | 改本机定时：重跑 `scripts\install_task.ps1` 带新参数（见下「本机自动化」） |
| `[site]` | `python scripts/build_site.py` |
| 其他（`[output]` `[fetch]` `[llm]`） | 下次抓取自动生效 |

`[schedule]` 现在是**文档默认值**：本机任务计划程序只认 Windows 本地时间、不读
`config.toml`，所以改时间要走 `install_task.ps1` 的参数（每天 08:00 是默认）。
`scripts/apply_config.py`（把本地时间换算成 UTC 写进 GitHub 工作流）只在将来
重新启用云端 Actions 时才需要。

**配置写错不会让站点挂掉**：类型不对、超出范围、TOML 语法错误、时区名拼错，
都会在日志里 WARNING 一句然后回落到内置默认值。站点每天只跑一次，
一个拼写错误让当天空白不值得。

## 快速开始

```bash
python -m pip install -r requirements.txt
python scripts/run_daily.py --dry-run   # 零成本验证抓取质量，不调 LLM
```

看着满意了再接 LLM：

```bash
export ANTHROPIC_API_KEY=sk-...   # 不配也能跑，会降级为规则模式（无解读）
python scripts/run_daily.py       # 生成 data/YYYY-MM-DD.json
python scripts/build_site.py      # 渲染 site/，
打开 site/index.html
```

Windows 注意：`requirements.txt` 里的 `tzdata` 是必需的，系统自带 Python 没有 IANA
时区库，缺了会在 `ZoneInfo("Asia/Shanghai")` 直接报错。

**Windows 上设第三方 key（cmd 里跑一次，之后新进程都能读到）：**

```cmd
setx ANTHROPIC_API_KEY  "<你的 key>"
setx ANTHROPIC_BASE_URL "https://agentrouter.org"
```

然后**重开终端**（`setx` 不影响已打开的进程）。`ANTHROPIC_BASE_URL` 不能漏：第三方
key 漏了它会被 SDK 打到 api.anthropic.com，那边一律回 401，整天降级成规则模式。

## AI 解读的七段结构

这是项目的核心产出。每条卡片的解读是结构化七段，不是一句摘要：

| 字段 | 内容 |
| --- | --- |
| `summary30s` | 30秒事实内核，3 条客观事实，只写"谁做了什么、数字是多少" |
| `transmissionChain` | 金融传递全景脉络，固定 **4 段**，每段含 触发源 / 传导机理 / 资产影响 / 时滞 / 监测指标 |
| `historicalAnalogy` | 历史参照系 —— 真实发生过的同类事件，当时怎么走、和现在像在哪差在哪 |
| `gameTheoryStakeholders` | 博弈各方的台前立场与真实底牌，2~4 个利益方 |
| `forwardIndicators` | 前瞻红线指标，2~3 个，每个给具体阈值和触发后果 |
| `takeaways` | 行动启示，投资者 / 企业经营者 / 普通人 三个视角 |
| `notes` | 解析，字符串数组 3~8 条，把链条里每个专业概念摊开讲 |

`transmissionChain` 每段的 `assetImpacts` 里有一项 `direction`，取值只能是
`up` / `down` / `volatile` / `neutral` —— 页面靠它决定绿涨 / 红跌 / 琥珀震荡的配色。

`notes` 用**大白话**讲机制，不写公式：哪个变量动、往哪动、结果是什么、为什么。
页面刻意不引 KaTeX，也不要求模型输出 LaTeX —— 零依赖、离线也读得出来。

schema 校验很严：七段缺一不可，传导链少于 4 段、博弈方少于 2 个、前瞻指标少于 2 个、
`notes` 少于 3 条、任何字段出现 LaTeX 记法，都会整体重试。**段数是质量下限而不是
格式检查** —— 早期版本把传导链写成一根平文本，页面为了摆出分步的样子只能按位置硬切，
结果「触发源 / 传导机理 / 资产影响」三个字段渲染出同一句话。规则模式下 `insight` 留空，
页面显式标注「今日无 AI 解读」，不拿 RSS 摘要冒充解读。

参考站 `fish22-ai/dailybrief-ui` 还有「表面直觉 vs 机构内核」「内化自测思考题」和
AI 追问框，**用户明确要求不要**，所以既不产出也不渲染；小节标题也一律只用中文，
不挂 "(Fact Nucleus)" 这类英文括注。

归档兼容：2026-09-11 之前的 `what/why/chain/notes` 四段格式、以及更早的
`what/why/chain/watch/term` 五字段，渲染器都会回落到旧标签，
重跑 `build_site.py` 不会让历史页面掉内容。

## 目录结构

```
core/       http.py（礼貌客户端 + robots）timeutil.py dedup.py
            scoring.py（相关性闸门 + 打分）seen.py select.py models.py
fetchers/   rss.py（通用 RSS）api.py（华尔街见闻 / Federal Register）
            html_cn.py  registry.py  ← 增删数据源只改这里
scripts/    run_daily.py  build_site.py
tests/      test_core.py  ← 纯规则层，不发网络请求
data/       YYYY-MM-DD.json + index.json + seen.json
site/       生成的静态站（GitHub Pages 根目录）
logs/       每日运行日志
```

## 常用参数

| 命令 | 作用 |
| --- | --- |
| `--dry-run` | 只抓取+闸门+去重+打分，不调 LLM；同时打印 prompt 规模与 token 估算 |
| `--only markets` | 只跑某板块（`markets`/`policy`）或某个源 key |
| `--no-cache` | 忽略当日缓存，强制重新抓取 |
| `-v` | debug 级日志，排查某个源为什么空 |

## 相关性闸门

财经频道混杂大量软新闻（三明治店换供应商、66 号公路旅游），光靠来源权重拦不住。
`core/scoring.py` 的 `relevance()` 按词表给每条打 0~1 分，低于 0.30 不进候选池，
实测拦下率约七成。

改词表时注意两点，都是踩过的坑：

- **拉丁词按词边界匹配**，否则 `rout`（暴跌）会命中 `Route 66`、`euro` 会命中
  `Europeans`。同时允许 `s/es/ed/ing` 词尾，否则 `tariff` 匹配不到 `tariffs`。
- **一词多义的词放 `_AMBIGUOUS`**，只有同现金融/地缘语境词时才计分。否则
  "北海道蟑螂数量激增（surge）"会被当成市场异动。

`tests/test_core.py` 里有 14 个闸门用例，改词表后跑一遍就知道有没有搞坏。

## 加一个数据源

1. 在 `fetchers/registry.py` 的 `SOURCES` 里加一条。多数源用 `_rss(...)` 就够。
2. 在 `core/scoring.py` 的 `SOURCE_WEIGHTS` 里给它权重 —— **必须与 `source` 名完全
   一致**，拼错不会报错，只会静默按默认权重排序。
3. `python scripts/run_daily.py --only <key> -v --no-cache` 单独验证。
4. `python tests/test_core.py` 确认没破坏别的。

发布频率低的官方源记得传 `window_hours=SLOW_WINDOW_HOURS`（7 天），否则 36 小时
窗口会让它几乎永远进不了候选池。

## 已知限制

- 央行 `pbc.gov.cn` 的 robots.txt 只允许 Baiduspider，本项目不绕过，国内货币政策
  靠华尔街见闻与证监会转述。其他被 robots 挡掉或已失效的源在 `registry.py` 顶部
  有完整说明。
- 闸门阈值目前偏宽松，SCMP 的部分区域新闻仍能过闸，指望 LLM 二次过滤。

## 部署到 GitHub（推荐：不用开机，手机也能看）

本地 git 仓库已经建好并提交，还差「推到 GitHub + 开 Pages」。照下面走一遍，
之后就全自动了。

### 1. 建远端仓库并推上去

在 <https://github.com/new> 建一个空仓库（**不要**勾选任何初始化文件），
名字随意，比如 `dailybrief`。建议选 **Private** —— 内容是公开新闻的解读，
但没必要让别人看到你的阅读偏好。然后：

```bash
cd D:/吃鱿鱼的鱿鱼/dailybrief
git remote add origin https://github.com/<你的用户名>/dailybrief.git
git push -u origin main
```

推送时会让你登录。密码栏**不能填 GitHub 密码**，要填 Personal Access Token：
<https://github.com/settings/tokens> → Generate new token (classic) →
勾 `repo` 和 `workflow` → 生成后复制粘贴进去。

### 2. 配 API key

仓库页 → Settings → Secrets and variables → Actions → New repository secret，
名字填 `ANTHROPIC_API_KEY`，值填你的 key。

不配也不会让任务失败，只是会降级成没有 AI 解读的规则模式。

### 3. 开 Pages

仓库页 → Settings → Pages → Source 选 **GitHub Actions**（不是 Deploy from a branch）。

地址是 `https://<你的用户名>.github.io/dailybrief/`。私有仓库的 Pages 需要
GitHub Pro；免费账号想让手机能访问就把仓库改成 Public。

### 4. 手动跑一次验证

仓库页 → Actions → 左边选 **Daily brief** → 右边 **Run workflow** → 绿色按钮。
两三分钟后刷新 Pages 地址就能看到。

**这个按钮就是"有需要时手动更新"**。日常更新已切到**本机自动跑**（见下一节），
用的是同一套流程；GitHub 云端定时已停用 —— 第三方中转 key 在 Actions 上不可用，
跑了只会生成没有 AI 解读的规则数据。

### 定时是怎么定的

更新由**本机任务计划程序**负责，默认每天 08:00（**Windows 本地时间**，任务计划
程序不读 `config.toml` 的 timezone）。`config.toml` 里的 `[schedule]` cron 现在只是
文档默认值，改时间用下面 `install_task.ps1` 的参数。

### 本机自动化（默认方式）

```cmd
:: 1) 一次性：设置第三方 key（cmd 里跑，然后重开终端）
setx ANTHROPIC_API_KEY  "<你的 key>"
setx ANTHROPIC_BASE_URL "https://agentrouter.org"

:: 2) 注册每天任务（默认每天 08:00；改时间加参数，如 -At "09:00"）
powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1

::    想退回"每周固定一天"就加 -Weekly，例如：
::    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Weekly -DayOfWeek Monday

:: 3) 验证 / 手动跑一次 / 卸载
schtasks /query /tn DailyBrief /v
schtasks /run  /tn DailyBrief
powershell -ExecutionPolicy Bypass -File scripts\uninstall_task.ps1
```

任务特性：

- **到点电脑没开 → 开机登录后自动补跑一次**（`StartWhenAvailable`，系统级行为，
  不用额外启动项）。补跑按实际运行那天生成 `data\当天.json`，抓最近 168 小时，
  **不会**倒推补出错过那周的档案。
- **单实例**（`MultipleInstances=IgnoreNew`）：同一时间只会跑一个，不会并发重复调 LLM。
- **不替你收拾工作区**：运行前要求 git 工作区干净，检测到未提交/未跟踪改动就
  拒绝执行并写进 `logs\cron.log`，绝不 stash、rebase 或覆盖你手头的东西；
  push 失败返回失败状态、本地提交保留，下次运行自动续推。
- 只 `git add data site`，其余文件一律不碰。日志追加在 `logs\cron.log`。

Git push 需要凭据：先用 Git Credential Manager 在这个账号手动 push 过一次即可，
计划任务会继承当前用户的凭据和环境变量。

## 手机上看

部署好之后手机浏览器直接开 Pages 地址就行，**不会乱码** —— 页面声明了
UTF-8 且 `<meta charset>` 放在 `<head>` 最前面。

样式已经针对手机做过适配，而且不是简单把两栏叠起来：

- **一屏一张卡**：卡片做得够短 —— 标题、来源、发生了什么、为什么在意、金融传导
  都塞进一屏，横向翻卡时不用上下找内容。字多的卡片由脚本自动把卡内字号往下缩
  （最低 0.65 倍），不做文字截断
- **页面照常整页上下滚**，不做卡内局部滚动 —— 长一点就让它长，读起来跟普通网页
  一致，不会憋在一个小框里
- **英文源的卡片默认只显示中文解读**：标题和英文摘要收在「查看英文原文」按钮后面，
  点一下才展开（桌面端屏幕够宽，原文一直是摊开的）
- **「解析」是折叠块**，本就默认收起
- **公式块可以横向滑动**而不是硬换行 —— 公式被劈成两行就读不懂了
- 深色模式跟随系统，地址栏颜色也会跟着变

## 底部功能栏

三个入口，手机和桌面都在底部固定着一行：

- **事件归档**：按 年 → 月 → 日期 三级折叠，默认全收起，展开某个月才列出那几天
- **我的收藏**：卡片右上角的 ☆ 点一下就收藏，列表里再点一条会跳回那天的**那张卡**
  （链接带 `#c-<日期>-<序号>` 锚点）。**收藏只存在这台设备的浏览器里**
  （`localStorage`），换设备或清缓存就没了 —— 以后接账号/小程序时把这层存储换成
  远端接口即可，界面不用动，段落划线收藏也在那时做
- **设置**：目前一个开关「默认展开英文原文」，关着时英文源卡片手机端只显示中文解读

没有 JS 时（或抓取工具看页面时）`<noscript>` 里那段样式会把三个抽屉直接摊在页面
底部，归档照样能点。

想更像个 App：Safari 分享按钮 →「添加到主屏幕」（Android Chrome 是菜单 →
「安装应用」）。加上之后从主屏打开会隐藏浏览器地址栏，页面里带了
`manifest.webmanifest` 和 iOS 需要的 meta 标签。

刷新拿不到最新内容的话，GitHub Pages 的 CDN 有几分钟缓存，等一下或强制刷新。
