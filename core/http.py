"""礼貌 HTTP 客户端：统一 UA、每域名限速、指数退避重试、robots.txt、当日缓存。

对应 CLAUDE.md 第五节。所有 fetcher 必须走这里，不要直接用 requests。
"""
from __future__ import annotations

import hashlib
import logging
import random
import time
import urllib.robotparser as robotparser
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "DailyBrief/0.1 (personal learning news aggregator; non-commercial; "
    "1 request per source per day)"
)

MIN_INTERVAL = 3.0      # 同域名最小请求间隔（秒）
TIMEOUT = 20
MAX_RETRIES = 2         # 首次之外最多再试 2 次

_last_hit: dict[str, float] = {}
_robots: dict[str, Optional[robotparser.RobotFileParser]] = {}


class PoliteClient:
    def __init__(self, cache_dir: Path, respect_robots: bool = True):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })

    # ---------- 缓存 ----------
    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]
        return self.cache_dir / f"{h}.bin"

    # ---------- robots ----------
    def _allowed(self, url: str) -> bool:
        """检查 robots.txt。

        注意：不能用 RobotFileParser.read()，它内部走 urllib，很多站（Fed、OpenAI、
        Anthropic、FT）对 urllib 的默认 UA 直接返回 403，而 robotparser 把 403 当成
        "全站禁止"，会导致大批本来允许的源被误判跳过。这里改用带正常 UA 的 session
        取回文本再 parse，取不到（4xx/网络错误）按"无 robots 限制"处理。
        """
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"

        if origin not in _robots:
            parser: Optional[robotparser.RobotFileParser] = None
            try:
                resp = self.session.get(f"{origin}/robots.txt", timeout=10)
                if resp.status_code == 200 and len(resp.text) < 200_000:
                    parser = robotparser.RobotFileParser()
                    parser.parse(resp.text.splitlines())
                else:
                    log.debug("robots.txt HTTP %d for %s, treating as allow",
                              resp.status_code, origin)
            except requests.RequestException as exc:
                log.debug("robots.txt unreachable for %s: %s", origin, exc)
            _robots[origin] = parser

        parser = _robots[origin]
        if parser is None:
            return True
        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    # ---------- 限速 ----------
    @staticmethod
    def _throttle(url: str) -> None:
        host = urlsplit(url).netloc
        last = _last_hit.get(host)
        if last is not None:
            wait = MIN_INTERVAL - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        _last_hit[host] = time.monotonic()

    # ---------- 主入口 ----------
    def get(self, url: str, *, use_cache: bool = True, **kwargs) -> Optional[bytes]:
        """返回响应 body；被 robots 禁止、重试用尽或 4xx 时返回 None（调用方负责跳过）。"""
        cache = self._cache_path(url)
        if use_cache and cache.exists():
            log.debug("cache hit %s", url)
            return cache.read_bytes()

        if not self._allowed(url):
            log.info("robots.txt disallows, skipping %s", url)
            return None

        for attempt in range(MAX_RETRIES + 1):
            self._throttle(url)
            try:
                resp = self.session.get(url, timeout=TIMEOUT, **kwargs)
            except requests.RequestException as exc:
                log.debug("attempt %d failed for %s: %s", attempt + 1, url, exc)
            else:
                if resp.status_code == 200:
                    if use_cache:
                        cache.write_bytes(resp.content)
                    return resp.content
                # 4xx 是稳定失败（403 反爬 / 404 源已挪走），不值得重试
                if 400 <= resp.status_code < 500:
                    log.info("HTTP %d for %s, giving up", resp.status_code, url)
                    return None
                log.debug("HTTP %d for %s", resp.status_code, url)

            if attempt < MAX_RETRIES:
                time.sleep(2 ** attempt + random.uniform(0, 0.5))   # 指数退避

        log.warning("all attempts exhausted for %s", url)
        return None

    def post(self, url: str, data: dict, **kwargs) -> Optional[bytes]:
        """POST 不缓存、不重试到底（目前只有个别国内接口需要）。"""
        if not self._allowed(url):
            return None
        self._throttle(url)
        try:
            resp = self.session.post(url, data=data, timeout=TIMEOUT, **kwargs)
            return resp.content if resp.status_code == 200 else None
        except requests.RequestException as exc:
            log.debug("POST failed for %s: %s", url, exc)
            return None
