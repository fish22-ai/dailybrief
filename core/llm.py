"""LLM 调用的公共层：客户端构建 + 模型回退链。

2026-10-30 起，中转站（agentrouter）改成按模型名供给渠道：某个模型的渠道
整个下架时，报「当前分组 default 下对于模型 X 无可用渠道」——而且一挂就是
连着几天（deepseek-v4-flash 从 10-22 挂到 10-29，八天的简报全降级成规则
模式才发现）。所以把「调一次模型」抽到这里：

  1. 主模型渠道没了 → 按 config 的 fallback_models 顺序换下一个模型，
     不再把一整天的简报押在一个模型名上。
  2. 瞬时错误（5xx / 连接错）仍在同一模型内重试 —— 中转站是多通道轮询，
     同一个模型名这次落坏通道、下次可能就好。
  3. 「无可用渠道」不重试，立刻换模型 —— 8 天日志证明重试同一个死模型
     纯属浪费。

解读精选（core/select.py）与英文翻译（core/translate.py）共用这一份。
"""
from __future__ import annotations

import logging
import os
import time

from . import config

log = logging.getLogger(__name__)

MODEL = config.get_str("llm", "model", "claude-opus-5")
# 中转站的模型默认输出思考块，思考 token 会吃掉大半输出预算，正文还没写
# 就撞 max_tokens 截断。默认关掉，省下的全给 JSON 正文。
DISABLE_THINKING = config.get_bool("llm", "disable_thinking", True)
# 同一模型内的瞬时错误重试预算：中转站多通道轮询，这次落坏通道、换次就好。
UPSTREAM_RETRIES = config.get_int("llm", "upstream_retries", 3, lo=0, hi=8)
UPSTREAM_BACKOFF = 4.0        # 秒，按次数线性递增：4s、8s、12s


class LLMUnavailable(Exception):
    """模型链上所有模型都试过了（或遇到鉴权错），本次调用彻底没戏。"""


def _fallback_models() -> list[str]:
    raw = config.get_str("llm", "fallback_models", "")
    return [m.strip() for m in raw.split(",") if m.strip()]


def models_chain() -> list[str]:
    """主模型在前、备选在后，去掉重复。"""
    return [MODEL] + [m for m in _fallback_models() if m and m != MODEL]


def make_client() -> tuple[object | None, str]:
    """构建 anthropic 客户端。返回 (client, 失败原因)；失败时 client 为 None。

    base_url 由 SDK 自己读 ANTHROPIC_BASE_URL 环境变量：走中转站时必须设置它，
    否则请求会打到 api.anthropic.com，中转站的 key 在那边一律 401。

    超时必须显式给：SDK 默认 connect 只有 5 秒，中转站握手偶尔慢一拍就是
    APITimeoutError，然后整天白白降级成规则模式。读取给足 600 秒 —— 四段解读
    本来就慢，16000 tokens 跑几分钟很正常。

    2026-10-30 从 select.py 挪过来：精选和翻译共用，顺带挂上模型回退链。
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY 未设置，降级为规则模式")
        return None, "未配置 API key"

    try:
        import anthropic
        try:
            import httpx2 as httpx    # anthropic 1.x 的 HTTP 依赖叫 httpx2
        except ImportError:
            import httpx              # 0.x 老版本用的还是 httpx
    except ImportError:
        log.warning("anthropic SDK 未安装，降级为规则模式")
        return None, "anthropic SDK 未安装"

    return anthropic.Anthropic(
        api_key=api_key,
        timeout=httpx.Timeout(600.0, connect=20.0),
    ), ""


def _is_no_channel(err: str) -> bool:
    """new-api 系中转站「这个模型名下所有通道都没了」的报错特征。"""
    return "无可用渠道" in err or "no available channel" in err.lower()


def call(client, *, max_tokens: int, messages: list,
         models: list[str] | None = None) -> tuple[str, str, object]:
    """调一次 LLM，模型链内自动回退。返回 (正文, 实际用的模型, 原始响应)。

    - 「无可用渠道」：这个模型名下通道全没了，重试无意义 → 立刻换下一个模型。
    - 5xx / 连接错：同一模型内按 UPSTREAM_RETRIES 重试（换通道常就好），
      重试用尽也换下一个模型。
    - 401/403（key 错）：换模型也没用，直接抛 LLMUnavailable。
    - 其他 4xx（参数不认等）：这个模型不吃这套参数，换下一个模型碰运气。
    全部试完抛 LLMUnavailable，message 是最后一次的错误。
    """
    chain = models if models is not None else models_chain()
    last = ""
    for mi, model in enumerate(chain):
        for attempt in range(1, UPSTREAM_RETRIES + 2):
            try:
                kwargs = dict(model=model, max_tokens=max_tokens, messages=messages)
                if DISABLE_THINKING:
                    kwargs["thinking"] = {"type": "disabled"}
                resp = client.messages.create(**kwargs)
                text = "".join(b.text for b in resp.content if b.type == "text")
                if not text.strip():
                    # 空正文：实测是中转站部分通道会丢弃 thinking 参数，模型把
                    # 整个 max_tokens 都花在不可见的推理上（stop=length、text 0 字）。
                    # 200 OK 但什么都没给，当瞬时错误重试 —— 轮询到正常通道就好。
                    last = "空正文（推理吃满输出预算，通道疑似丢弃 thinking 参数）"
                    raise ValueError(last)
                if mi > 0 or attempt > 1:
                    log.info("LLM 调用成功：model=%s（第 %d 次尝试）", model, attempt)
                return text, model, resp
            except Exception as exc:                       # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                status = getattr(exc, "status_code", None)
                if status in (401, 403):
                    raise LLMUnavailable(last) from exc
                if _is_no_channel(last):
                    log.warning("模型 %s 无可用渠道（第 %d/%d 个），换下一个",
                                model, mi + 1, len(chain))
                    break
                # status None 多半是连接错（APIConnectionError 没有状态码），
                # 和 5xx 一样算瞬时：中转站抖一下，重试常就好。
                transient = status in (500, 502, 503, 504) or status is None or (
                    status == 400 and "invalid" in last.lower()
                    and "model" in last.lower())
                if transient and attempt <= UPSTREAM_RETRIES:
                    log.warning("上游通道异常（model=%s 第 %d 次），重试：%s",
                                model, attempt, last[:160])
                    time.sleep(UPSTREAM_BACKOFF * attempt)
                    continue
                log.warning("LLM 调用失败（model=%s）：%s", model, last[:200])
                break
    raise LLMUnavailable(last)
