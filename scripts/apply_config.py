"""把 config.toml 的 [schedule] 写进 GitHub Actions 工作流，并检查整份配置。

为什么需要这一步：GitHub Actions 的 cron **只认 UTC**，不支持时区。你在
config.toml 里按本地时区写"每周一 08:00"，这个脚本负责换算成 UTC 再写进
.github/workflows/daily.yml。光改 config.toml 不跑这句，GitHub 上的定时不会变。

用法：
    python scripts/apply_config.py           # 换算并写入工作流
    python scripts/apply_config.py --check   # 只检查不改文件（CI 用）
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config                                    # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "daily.yml"
INSTALL_TASK = ROOT / "scripts" / "install_task.ps1"

# 只支持定点 cron（分和时都是具体数字），因为要做时区换算。
# "*/5 * * * *" 这种间隔式在换算上没有意义，也不该用在每周一次的任务上。
CRON_RE = re.compile(r"^(\d{1,2})\s+(\d{1,2})\s+(\S+)\s+(\S+)\s+(\S+)$")

DOW_NAMES = {"0": "周日", "1": "周一", "2": "周二", "3": "周三",
             "4": "周四", "5": "周五", "6": "周六", "7": "周日"}


class ConfigError(Exception):
    """配置写错了，且没法安全地猜出用户想要什么。"""


def parse_cron(expr: str) -> tuple[int, int, str, str, str]:
    m = CRON_RE.match(expr.strip())
    if not m:
        raise ConfigError(
            f"cron = {expr!r} 格式不对。需要 5 段「分 时 日 月 星期」，"
            f"且分与时必须是具体数字。例如每周一 08:00 写 \"0 8 * * 1\""
        )
    minute, hour = int(m.group(1)), int(m.group(2))
    if not 0 <= minute <= 59:
        raise ConfigError(f"cron 的分钟 {minute} 超出 0~59")
    if not 0 <= hour <= 23:
        raise ConfigError(f"cron 的小时 {hour} 超出 0~23")
    return minute, hour, m.group(3), m.group(4), m.group(5)


def shift_to_utc(minute: int, hour: int, dom: str, mon: str, dow: str,
                 tz_name: str) -> tuple[str, str]:
    """把本地时间的 cron 换算成 UTC cron。返回 (utc_cron, 人话说明)。

    时区偏移可能让日期跨天（比如上海周一 08:00 = UTC 周日 00:00），所以星期
    字段也得跟着挪。这里只处理"星期 + 具体时分"这一种最常见的组合；
    带具体日期（dom）又跨天的情况会明确报错，不猜。
    """
    try:
        tz = ZoneInfo(tz_name)
    except Exception as exc:                               # noqa: BLE001
        raise ConfigError(f"timezone = {tz_name!r} 不是合法的 IANA 时区名"
                          f"（例：Asia/Shanghai、America/New_York）") from exc

    # 拿一个具体日期来算偏移。用未来的周一，避免夏令时边界上的歧义。
    # 注意：有夏令时的时区（如 America/New_York）偏移一年会变两次，
    # 这里按当前偏移算 —— 每周一次的任务差一小时无所谓，但要提醒用户。
    probe = datetime.now(tz).replace(hour=hour, minute=minute,
                                     second=0, microsecond=0)
    utc = probe.astimezone(ZoneInfo("UTC"))
    day_shift = (utc.date() - probe.date()).days

    new_dow = dow
    if day_shift and dow != "*":
        if not re.fullmatch(r"[0-7](,[0-7])*", dow):
            raise ConfigError(
                f"星期字段 {dow!r} 与 {tz_name} 的时区偏移组合起来会跨天，"
                f"这种情况脚本不猜。请把星期写成数字（如 1 或 1,4），"
                f"或把时间改到不跨 UTC 日界的时段"
            )
        # 上海周一 08:00 → UTC 周日 00:00，星期要减 1
        new_dow = ",".join(
            str((int(d) % 7 + day_shift) % 7) for d in dow.split(","))

    if day_shift and dom != "*":
        raise ConfigError(
            f"日期字段 {dom!r} 遇上跨 UTC 日界的时间，脚本不猜。"
            f"请改用星期字段，或把时间挪到当地 08:00 之后"
        )

    utc_cron = f"{utc.minute} {utc.hour} {dom} {mon} {new_dow}"

    if dow != "*":
        when = "每" + "、".join(DOW_NAMES.get(d, d) for d in dow.split(","))
    elif dom != "*":
        when = f"每月 {dom} 号"
    else:
        when = "每天"
    human = (f"{when} {hour:02d}:{minute:02d}（{tz_name}）"
             f" = UTC {utc.hour:02d}:{utc.minute:02d}"
             + (f"，{'前' if day_shift < 0 else '次'}一天" if day_shift else ""))
    return utc_cron, human


CRON_LINE_RE = re.compile(r"^(\s*)- cron: \"[^\"]*\"\s*$")
COMMENT_LINE_RE = re.compile(r"^\s*#")


def patch_workflow(utc_cron: str, human: str, dry: bool) -> bool:
    """把 UTC cron 写进工作流的 schedule 段。返回是否发生了改动。

    按行处理而不是正则整块替换：正则版本会把自己生成的注释当成"上一行注释"，
    每跑一次多留一行，跑三次就有三行重复的说明（已踩）。这里先把 cron 行上方
    连续的注释全部吃掉，再重新写，才是幂等的。
    """
    if not WORKFLOW.exists():
        raise ConfigError(f"找不到 {WORKFLOW.relative_to(ROOT)}")

    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    idx = next((i for i, ln in enumerate(lines) if CRON_LINE_RE.match(ln)), None)
    if idx is None:
        raise ConfigError("工作流里找不到 schedule 的 cron 行，可能已被手改过")

    indent = CRON_LINE_RE.match(lines[idx]).group(1)
    # 往上吃掉紧邻的注释行，它们都是上一次生成的
    start = idx
    while start > 0 and COMMENT_LINE_RE.match(lines[start - 1]):
        start -= 1

    block = [
        f"{indent}# {human}",
        f"{indent}# 这三行由 scripts/apply_config.py 依据 config.toml 生成，不要手改",
        f'{indent}- cron: "{utc_cron}"',
    ]
    new_lines = lines[:start] + block + lines[idx + 1:]
    new_text = "\n".join(new_lines) + "\n"

    if new_text == WORKFLOW.read_text(encoding="utf-8"):
        return False
    if not dry:
        WORKFLOW.write_text(new_text, encoding="utf-8")
    return True


def report_settings() -> None:
    """把当前生效的配置打印出来，让用户确认改动真的生效了。"""
    from core import select, timeutil                       # noqa: PLC0415
    from core import scoring                                # noqa: PLC0415

    print("\n当前生效的配置：")
    print(f"  站点名称        {config.get_str('site', 'name', '每日金融 sense')}")
    print(f"  每板块条数      {select.TOP_N}")
    print(f"  候选池大小      {select.MAX_CANDIDATES_PER_CAT}")
    print(f"  单源产出上限    {select.OUTPUT_SOURCE_CAP}")
    print(f"  抓取窗口        {timeutil.WINDOW_HOURS} 小时"
          f"（官方源 {timeutil.SLOW_WINDOW_HOURS} 小时）")
    print(f"  相关性阈值      {scoring.RELEVANCE_THRESHOLD}")
    print(f"  模型            {select.MODEL}")
    print(f"  max_tokens      {select.MAX_TOKENS}")
    print(f"  重试上限        {select.MAX_RETRIES}"
          f"（每次运行最多 {1 + select.MAX_RETRIES} 次 API 调用）")
    print(f"  传导链环数下限  {select.MIN_CHAIN_HOPS}")
    print(f"  解析最少条数    {select.MIN_NOTES}")

    extra = config.get_str("llm.prompt", "extra", "").strip()
    tmpl = config.get_str("llm.prompt", "template", "").strip()
    print(f"  prompt 追加要求 {extra or '（无）'}")
    print(f"  prompt 整段替换 {'已启用（' + str(len(tmpl)) + ' 字）' if tmpl else '（未启用，用内置）'}")

    if select.TOP_N >= 3 and select.MAX_TOKENS <= 16000:
        print("\n  提示：四段解读每条约 1KB，per_category≥3 时输出量已经不小。"
              "撞上 max_tokens 会被截断、白烧一次重试。"
              "想省就调小 per_category 或候选池 pool_size。")


def sync_windows_task(dry: bool) -> None:
    """把本机定时任务也拉到 config.toml 这条线上，让"改时间"只剩一处。

    真正读 config.toml 的是 install_task.ps1 自己，这里只负责把它跑起来。
    任务计划程序只存在于 Windows，其他平台静默跳过。
    """
    if sys.platform != "win32":
        return
    if dry:
        print("\n本机定时任务本次未改动（--check）——"
              "跑 python scripts/apply_config.py 会一并同步它")
        return
    if not INSTALL_TASK.exists():
        return
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(INSTALL_TASK)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"\n本机定时任务没同步成功（不影响上面几步）：{exc}", file=sys.stderr)
        return
    print("\n本机定时任务：")
    for line in (proc.stdout or "").strip().splitlines():
        print("  " + line)
    if proc.returncode != 0:
        print(f"  install_task.ps1 退出码 {proc.returncode}，请手动跑一次确认",
              file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="把 config.toml 应用到工作流")
    ap.add_argument("--check", action="store_true",
                    help="只检查配置与工作流是否同步，不改文件")
    ap.add_argument("--show-prompt", action="store_true",
                    help="打印当前生效的完整 prompt（含 config.toml 里的追加要求）")
    ap.add_argument("--no-task", action="store_true",
                    help="只同步云端工作流，不动本机 Windows 定时任务")
    args = ap.parse_args()

    if args.show_prompt:
        from core import select                             # noqa: PLC0415
        print(select.prompt_template())
        return 0

    cfg_path = ROOT / "config.toml"
    if not cfg_path.exists():
        print(f"找不到 {cfg_path.name}，所有配置将走内置默认值", file=sys.stderr)
        return 1

    try:
        expr = config.get_str("schedule", "cron", "0 8 * * 1")
        tz_name = config.get_str("schedule", "timezone", "Asia/Shanghai")
        utc_cron, human = shift_to_utc(*parse_cron(expr), tz_name)
        changed = patch_workflow(utc_cron, human, dry=args.check)
    except ConfigError as exc:
        print(f"\n配置有问题：{exc}\n", file=sys.stderr)
        return 2

    print(f"更新时间：{human}")
    print(f"写进工作流的 UTC cron：{utc_cron}")

    if args.check:
        if changed:
            print("\n工作流与 config.toml 不同步，跑 python scripts/apply_config.py 修正",
                  file=sys.stderr)
            return 3
        print("工作流与 config.toml 一致")
    elif changed:
        print(f"\n已更新 {WORKFLOW.relative_to(ROOT)}")
        print("推到 GitHub 后新的定时才生效：")
        print("  git add -A && git commit -m \"chore: 更新配置\" && git push")
    else:
        print("\n工作流已是最新，无需改动")

    if "America" in tz_name or "Europe" in tz_name:
        print(f"\n注意：{tz_name} 有夏令时，UTC 偏移一年会变两次。"
              f"换季后重跑一次这个脚本即可对齐。")

    if not args.no_task:
        sync_windows_task(dry=args.check)

    report_settings()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
