@echo off
REM 每日任务入口，供 Windows 任务计划程序调用。
REM 注册方式见 README.md「自动化」一节。
REM
REM API key 从用户环境变量读取，不写在这个文件里（会进版本库）。
REM 设置方式（一次即可，之后所有新进程都能读到）：
REM     setx ANTHROPIC_API_KEY "sk-ant-..."

chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0.."

python scripts\run_daily.py
if errorlevel 1 (
    echo [%date% %time%] run_daily 失败，跳过渲染 >> logs\cron.log
    exit /b 1
)

python scripts\build_site.py
echo [%date% %time%] 完成 >> logs\cron.log
