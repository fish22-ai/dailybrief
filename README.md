# 每日金融 sense（DailyBrief）

每天自动更新一次的静态站，帮你**建立金融 sense**，而不是记录新闻。

两个板块：**市场·货币·金融** 和 **政策·地缘大事件**。每条卡片左边是发生了什么，
右边是 AI 解读 —— 市场为什么在意、钱怎么一环一环流动、背后是哪个公式在动。

设计原则与全部实测记录见 [`../CLAUDE.md`](../CLAUDE.md)。

## 改配置（不用动代码）

常改的东西都在根目录的 **`config.toml`**：更新时间、每板块条数、站点名称、
AI 解读的写作要求。文件里每一项都有注释说明和可以直接抄的例子。

```toml
[schedule]
cron = "0 8 * * 1"          # 每周一 08:00，按下面的 timezone 算，不用换算 UTC
timezone = "Asia/Shanghai"

[output]
per_category = 5            # 每个板块出几条

[site]
name = "每日金融 sense"      # 网页标题

[llm.prompt]
extra = ""                  # 想调解读口吻就写在这，例："解析多写公式推导。"
```

改完之后：

| 改了哪一节 | 要跑什么 |
| --- | --- |
| `[schedule]` | `python scripts/apply_config.py` 然后 git push（**必须**，见下） |
| `[site]` | `python scripts/build_site.py` |
| 其他（`[output]` `[fetch]` `[llm]`） | 下次抓取自动生效 |

`[schedule]` 之所以要多跑一步：GitHub Actions 的 cron **只认 UTC、不支持时区**。
`apply_config.py` 负责把「周一 08:00 上海」换算成 UTC 写进
`.github/workflows/daily.yml`（周一 08:00 上海 = UTC 周日 00:00，星期也得跟着挪）。
光改 `config.toml` 不跑这句，GitHub 上的定时不会变。这个脚本还会打印一份
当前生效配置，可以用来确认改动真的读到了。

**配置写错不会让站点挂掉**：类型不对、超出范围、TOML 语法错误、时区名拼错，
都会在日志里 WARNING 一句然后回落到内置默认值。站点每周只跑一次，
一个拼写错误让整周空白不值得。

## 快速开始

```bash
python -m pip install -r requirements.txt
python scripts/run_daily.py --dry-run   # 零成本验证抓取质量，不调 LLM
```

看着满意了再接 LLM：

```bash
export ANTHROPIC_API_KEY=sk-...   # 不配也能跑，会降级为规则模式（无解读）
python scripts/run_daily.py       # 生成 data/YYYY-MM-DD.json
python scripts/build_site.py      # 渲染 site/，打开 site/index.html
```

Windows 注意：`requirements.txt` 里的 `tzdata` 是必需的，系统自带 Python 没有 IANA
时区库，缺了会在 `ZoneInfo("Asia/Shanghai")` 直接报错。

## AI 解读的四段结构

这是项目的核心产出。每条卡片的解读是结构化四段，不是一句摘要：

| 字段 | 内容 |
| --- | --- |
| `what` | 发生了什么，大白话一句话，不用行话缩写 |
| `why` | 市场为什么在意 —— 它改变了市场原本在定价的哪个预期 |
| `chain` | 金融传导，用 ` → ` 连接 **8~12 环**，每一环都要写出机制而不只是结果 |
| `notes` | 解析，字符串数组 4~8 条，把链条里每个专业概念摊开讲 |

`notes` 里只要涉及定价、折现、利差、收益率就必须给公式，并说清"哪个变量动了、
往哪动、于是哪一项变大变小、结果是什么"。公式一律写成**反引号包住的纯文本**
（`` `P = C/(1+y)¹ + … + (C+Face)/(1+y)ⁿ` ``），不用 LaTeX —— 页面刻意不引 KaTeX，
零依赖、离线也读得出来。长公式渲染成等宽块，短的走行内 `code`。

schema 校验很严：四个字段缺一不可，`chain` 少于 7 环、`notes` 少于 3 条、
公式里出现 LaTeX 记法都会整体重试。**环数是质量下限而不是格式检查** —— 早期版本
chain 只有 3~4 环，从"官员转鹰"一步跳到"成长股承压"，中间的 Rf、折现率、现值全省了，
结果就是看不懂。规则模式下 `insight` 留空，页面显式标注「今日无 AI 解读」，
不拿 RSS 摘要冒充解读。

2026-09-06 之前的归档是旧的 `what/why/chain/watch/term` 五字段，没有 `notes`。
渲染器对这种数据回落到旧标签，重跑 `build_site.py` 不会让历史页面掉内容。

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

**这个按钮就是"有需要时手动更新"**，手机浏览器也能点。每周一 08:00 的自动更新
和它跑的是同一套流程。

### 定时是怎么定的

`config.toml` 的 `[schedule]` 说了算，改完跑 `python scripts/apply_config.py`
再 push。当前是每周一 08:00（Asia/Shanghai）。

GitHub 的定时在高峰期可能延迟几分钟到半小时，这是平台行为，不是配置问题。
另外**仓库连续 60 天没有任何提交时 GitHub 会自动停掉定时任务** —— 我们每次
运行都会提交当天产物，所以只要它在跑就不会被停。

### 备选：Windows 任务计划程序（本机跑）

不想用 GitHub 的话可以本机跑，但**电脑必须在那个时间点开着**（睡眠不算），
错过的日子不补跑。

```cmd
setx ANTHROPIC_API_KEY "sk-ant-..."
schtasks /create /tn "DailyBrief" /tr "D:\吃鱿鱼的鱿鱼\dailybrief\scripts\daily.bat" /sc weekly /d MON /st 08:00
schtasks /run /tn "DailyBrief"          :: 立刻跑一次验证
schtasks /query /tn "DailyBrief" /v     :: 看上次运行结果
schtasks /delete /tn "DailyBrief" /f    :: 删除
```

`scripts\daily.bat` 依次跑 `run_daily.py` 和 `build_site.py`，结果追加到
`logs\cron.log`。**这个 .bat 必须是 CRLF 换行**，LF 在 cmd 下会逐字符报错
（`.gitattributes` 里已经用 `eol=crlf` 锁住了）。

## 手机上看

部署好之后手机浏览器直接开 Pages 地址就行，**不会乱码** —— 页面声明了
UTF-8 且 `<meta charset>` 放在 `<head>` 最前面。

样式已经针对手机做过适配，不是简单把两栏叠起来：

- **760px 以下**转单栏，解读区字号略放大（14.5px），触摸目标加大
- **公式块可以横向滑动**而不是硬换行 —— 公式被劈成两行就读不懂了
- **430px 以下**（iPhone SE、mini 一类）传导链改成**一环一行的竖排**，
  箭头转成向下。横排在窄屏上每格挤成两三个字，根本读不了
- 深色模式跟随系统，地址栏颜色也会跟着变

想更像个 App：Safari 分享按钮 →「添加到主屏幕」（Android Chrome 是菜单 →
「安装应用」）。加上之后从主屏打开会隐藏浏览器地址栏，页面里带了
`manifest.webmanifest` 和 iOS 需要的 meta 标签。

刷新拿不到最新内容的话，GitHub Pages 的 CDN 有几分钟缓存，等一下或强制刷新。
