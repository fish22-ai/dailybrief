"""读取根目录的 config.toml，给各模块提供可配置项。

设计取向：**配置缺失永不报错，一律回落到内置默认值。** 用户手改 TOML 很容易
删错一行或写错类型，这种时候还是要能出页面 —— 站点每周只跑一次，一个拼写错误
让整周空白不值得。所以 get() 系列全部带类型检查 + 范围裁剪，不合法就 WARNING
后用默认值，而不是抛异常。

tomllib 是 Python 3.11+ 标准库，不引第三方 YAML/TOML 依赖。
"""
from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

_cache: dict | None = None


def load(path: Path | None = None, force: bool = False) -> dict:
    """读 config.toml。文件不存在或语法错误都返回 {}，让全部项走默认值。"""
    global _cache
    if _cache is not None and not force and path is None:
        return _cache

    target = path or CONFIG_PATH
    data: dict = {}
    if target.exists():
        try:
            data = tomllib.loads(target.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError) as exc:
            # 语法写错不能让整站瘫掉 —— 提醒一句，然后全走默认值
            log.warning("config.toml 读取失败（%s），全部使用内置默认值：%s",
                        target.name, exc)
    if path is None:
        _cache = data
    return data


def _section(name: str, cfg: dict | None = None) -> dict:
    """取一个 [表]，允许 "llm.prompt" 这种点号路径。"""
    node: Any = load() if cfg is None else cfg
    for part in name.split("."):
        if not isinstance(node, dict):
            return {}
        node = node.get(part, {})
    return node if isinstance(node, dict) else {}


def get_str(section: str, key: str, default: str, cfg: dict | None = None) -> str:
    val = _section(section, cfg).get(key, default)
    if not isinstance(val, str):
        log.warning("config [%s] %s 应为字符串，收到 %r，改用默认值 %r",
                    section, key, val, default)
        return default
    return val


def get_int(section: str, key: str, default: int, cfg: dict | None = None,
            lo: int | None = None, hi: int | None = None) -> int:
    val = _section(section, cfg).get(key, default)
    # bool 是 int 的子类，true 会被当成 1 —— 这种明显是填错了，不要静默接受
    if isinstance(val, bool) or not isinstance(val, int):
        log.warning("config [%s] %s 应为整数，收到 %r，改用默认值 %d",
                    section, key, val, default)
        return default
    if lo is not None and val < lo:
        log.warning("config [%s] %s = %d 小于下限 %d，按 %d 处理",
                    section, key, val, lo, lo)
        return lo
    if hi is not None and val > hi:
        log.warning("config [%s] %s = %d 超过上限 %d，按 %d 处理",
                    section, key, val, hi, hi)
        return hi
    return val


def get_float(section: str, key: str, default: float, cfg: dict | None = None,
              lo: float | None = None, hi: float | None = None) -> float:
    val = _section(section, cfg).get(key, default)
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        log.warning("config [%s] %s 应为数字，收到 %r，改用默认值 %s",
                    section, key, val, default)
        return default
    val = float(val)
    if lo is not None and val < lo:
        log.warning("config [%s] %s = %s 小于下限 %s，按 %s 处理",
                    section, key, val, lo, lo)
        return lo
    if hi is not None and val > hi:
        log.warning("config [%s] %s = %s 超过上限 %s，按 %s 处理",
                    section, key, val, hi, hi)
        return hi
    return val
