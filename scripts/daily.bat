@echo off
REM 每日任务入口，供 Windows 任务计划程序调用（scripts\install_task.ps1 注册）。
REM 顺序：预检(命令/已跟踪文件干净) → fast-forward 同步 → 抓取 → 渲染 → 提交 → 推送。
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

echo [%date% %time%] ===== 完成 ===== >> "%LOG%"
exit /b 0
