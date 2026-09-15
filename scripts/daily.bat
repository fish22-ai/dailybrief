@echo off
REM Re-exec self under a 65001 console. Keep every line above ":utf8" ASCII-only.
REM cmd parses this file as it reads it, and decodes with the console code page
REM captured at process start (936 on zh-CN). This file is UTF-8, so Chinese text
REM gets decoded as 936, split mid-character, and the fragments of REM lines are
REM executed as commands -- that is where the "'xxx' is not recognized" noise in
REM the task log comes from (diagnosed 2026-09-15). A UTF-8 BOM does not help:
REM this cmd ignores it and then fails on "@echo off" itself. The child cmd starts
REM with the console already at 65001, reads this file consistently, and is clean.
chcp 65001 >nul
if "%~1"=="_utf8" goto :utf8
cmd /d /c ""%~f0" _utf8 %*"
exit /b %errorlevel%
:utf8

REM 这里不能用 shift 把 _utf8 标记去掉 —— shift 会连带改掉 %0，而下面的
REM cd /d "%~dp0.." 正是靠 %0 定位仓库根的（实测 shift 后 %~dp0 变成错的目录，
REM 整个脚本就跑到别处去了）。所以子进程里 %1 固定是 _utf8 标记，真正的参数在 %2，
REM 用到参数的地方两个都看。见下面守卫里的 force 判断。

REM 每日任务入口，供 Windows 任务计划程序调用（scripts\install_task.ps1 注册）。
REM 顺序：当日去重 → 预检(命令/已跟踪文件干净) → fast-forward 同步 → 抓取 → 渲染 → 提交 → 推送。
REM 任一步失败立即以非零退出并写 logs\cron.log；绝不自动 stash/rebase/覆盖用户改动。
REM
REM key 与端点从用户环境变量读取，不写在这个文件里（会进版本库）。设置一次：
REM     setx ANTHROPIC_API_KEY  "<你的 key>"
REM     setx ANTHROPIC_BASE_URL "https://agentrouter.org"
REM 然后重开终端。BASE_URL 不能漏：漏了 anthropic SDK 会默认打 api.anthropic.com，
REM 第三方 key 在那里一律 401，整天降级成规则模式（页面显示「今日无 AI 解读」）。

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."
set "ROOT=%CD%"
set "LOG=%ROOT%\logs\cron.log"
if not exist "%ROOT%\logs" mkdir "%ROOT%\logs"

echo [%date% %time%] ===== 开始 ===== >> "%LOG%"

REM ---------- 当日去重：今天已经成功产出过就跳过 ----------
REM 背景：任务计划开了 StartWhenAvailable，开机后会把「错过的那次」补跑一遍。但补跑只是
REM 把任务挪到现在执行，不是回头生成错过那天的那一期 —— run_daily.py 不带 --date 时
REM 取的是**当天**日期（core/timeutil.py 的 today_str）。所以同一天会出现两次运行：
REM 开机补偿跑一次 + 15:00 定时再跑一次，产出的却是同一个日期。当日简报只该有一份，
REM 这里把第二次挡掉。
REM
REM 判定依据是 logs\.last_success（整条流水线跑完、push 成功之后才写），而不是 data\ 下
REM 有没有当天的产物文件：产物写出来但 build_site/push 失败时，还需要 15:00 那次来续推，
REM 用「文件存在」判断会把那次重试也一起挡掉。logs\ 是 gitignore 的，哨兵是本机状态，
REM 不进版本库。
REM
REM 手动强制重跑（想让当天再更新一次）：scripts\daily.bat force
set "TODAY="
for /f "usebackq delims=" %%d in (`python -c "from core.timeutil import today_str; print(today_str())"`) do set "TODAY=%%d"
REM python 拿不到日期时不在这里报错 —— 下面「预检 1」会给出更准确的提示。
if not defined TODAY goto :guard_done
REM %1 是 _utf8 标记（见文件开头），真正的参数在 %2，所以两个都看。
if /i "%~1"=="force" goto :guard_done
if /i "%~2"=="force" goto :guard_done
set "LAST_SUCCESS="
if exist "%ROOT%\logs\.last_success" set /p LAST_SUCCESS=<"%ROOT%\logs\.last_success"
if /i "%LAST_SUCCESS%"=="%TODAY%" (
    echo [%date% %time%] 今日（%TODAY%）已成功产出，跳过本次（强制重跑：daily.bat force） >> "%LOG%"
    exit /b 0
)
:guard_done

REM ---------- 预检 1：命令可用 ----------
where python >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] 找不到 python，终止 >> "%LOG%"
    exit /b 1
)
where git >nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] 找不到 git，终止 >> "%LOG%"
    exit /b 1
)

REM ---------- 预检 2：已跟踪文件必须干净（自动任务不碰你的手头改动） ----------
REM 只拦「已跟踪文件被改动/已暂存」——这两类会被下面的 commit 连带提交，或者
REM 让 fast-forward 变得语义不清，所以必须停。
REM 未跟踪文件不拦：本脚本只 add data site 两个显式路径，未跟踪文件不可能被误提交。
REM （以前这里是硬拦的，结果工作区放任何一个临时脚本都会让自动任务静默死掉 ——
REM  2026-09-12/13 两次误杀就是这么来的。见 .gitignore 里 _measure.js 的说明。）
git diff --quiet 2>>"%LOG%"
if errorlevel 1 (
    echo [%date% %time%] 有未提交改动，自动任务拒绝运行（不替你 stash/覆盖） >> "%LOG%"
    exit /b 1
)
git diff --cached --quiet 2>>"%LOG%"
if errorlevel 1 (
    echo [%date% %time%] 有已暂存改动，自动任务拒绝运行 >> "%LOG%"
    exit /b 1
)
REM 未跟踪文件只记录一行。唯一风险是远端新增了同名文件、导致下面的 fast-forward
REM 失败（git 会拒绝覆盖未跟踪文件）——那一步有自己的报错，够定位了。
git ls-files --others --exclude-standard 2>nul | findstr /r "." >nul
if not errorlevel 1 echo [%date% %time%] 提示：工作区有未跟踪文件，不拦（只 add data site） >>"%LOG%"

REM ---------- 同步远端：只接受 fast-forward，分叉就停下让用户手动处理 ----------
git fetch origin main 2>>"%LOG%"
if errorlevel 1 (
    echo [%date% %time%] git fetch 失败（网络/凭据？），终止 >> "%LOG%"
    exit /b 1
)
git merge --ff-only origin/main 2>>"%LOG%"
if errorlevel 1 (
    echo [%date% %time%] 本地与远端分叉，无法自动 fast-forward，请手动同步后重试 >> "%LOG%"
    exit /b 1
)

REM ---------- 抓取 + 渲染 ----------
python scripts\run_daily.py
if errorlevel 1 (
    echo [%date% %time%] run_daily 失败，已保留本地已有产物，终止 >> "%LOG%"
    exit /b 1
)
REM --latest 只渲染最新一天。不加这个参数会把 data\ 下所有日期重渲一遍，
REM 而 9.11 及更早存的是七段数据 + 复杂版式，重渲会被覆盖掉。
python scripts\build_site.py --latest
if errorlevel 1 (
    echo [%date% %time%] build_site 失败，终止 >> "%LOG%"
    exit /b 1
)

REM ---------- 提交：只 add data site；commit 后无条件 push（能续推上次没推上去的） ----------
git add data site 2>>"%LOG%"
if errorlevel 1 (
    echo [%date% %time%] git add 失败，终止 >> "%LOG%"
    exit /b 1
)
git diff --cached --quiet
if not errorlevel 1 (
    echo [%date% %time%] 无新产物，跳过提交 >> "%LOG%"
    goto :push
)
git commit -m "daily: %date%" >>"%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] git commit 失败，终止 >> "%LOG%"
    exit /b 1
)
echo [%date% %time%] 已提交，开始推送 >> "%LOG%"

:push
git push origin main >>"%LOG%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] git push 失败：本地提交已保留，下次运行会自动续推，本次计为失败 >> "%LOG%"
    exit /b 1
)

REM 走到这里说明整条流水线都成功了（含 push）。记下今天的日期，供下次运行的当日去重判断。
> "%ROOT%\logs\.last_success" echo %TODAY%
echo [%date% %time%] ===== 完成 ===== >> "%LOG%"
exit /b 0
