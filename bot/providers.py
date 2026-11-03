"""Модели через API-прокси: Anthropic — нативный Anthropic-формат (structured outputs, prompt caching),
Gemini — OpenAI-совместимый формат. Единый цикл инструментов для обоих."""
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from bot import config

ToolRunner = Callable[[str, dict], Awaitable[dict]]


class LLMError(RuntimeError):
    pass


class RefusalError(LLMError):
    pass


@dataclass
class LLMResult:
    text: str
    model: str
    returned_model: str | None
    usage: dict = field(default_factory=dict)
    ms: int = 0
    tool_calls: list[dict] = field(default_factory=list)   # [{"name", "args", "result"}]
    calls: int = 0


def parse_json(text: str) -> dict:
    """Модели за прокси не обязаны соблюдать схему: снимаем markdown и берём первый JSON-объект."""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _add_usage(total: dict, part: dict) -> None:
    for k, v in part.items():
        if isinstance(v, int):
            total[k] = total.get(k, 0) + v


class Providers:
    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        api_key = api_key or os.environ["STAR_API_KEY"]
        base_url = (base_url or os.environ.get("STAR_BASE_URL", "https://ai.starimg.ru")).rstrip("/")
        lim = config.routes()["limits"]
        self.anthropic = AsyncAnthropic(api_key=api_key, base_url=base_url,
                                        timeout=lim["request_timeout_s"], max_retries=lim["request_retries"])
        self.openai = AsyncOpenAI(api_key=api_key, base_url=base_url + "/v1",
                                  timeout=lim["request_timeout_s"], max_retries=lim["request_retries"])

    async def run(self, model_key: str, system: str, messages: list[dict], *, schema: dict | None = None,
                  tools: list[dict] | None = None, tool_runner: ToolRunner | None = None,
                  max_tokens: int = 2000, max_tool_calls: int = 3) -> LLMResult:
        """messages — простые текстовые реплики [{"role": "user"|"assistant", "content": str}].
        tools — нейтральные описания {"name", "description", "input_schema"}."""
        m = config.routes()["models"][model_key]
        fn = self._run_anthropic if m["api"] == "anthropic" else self._run_openai
        t0 = time.monotonic()
        res = await fn(m["name"], system, messages, schema, tools or [], tool_runner, max_tokens, max_tool_calls)
        res.ms = int((time.monotonic() - t0) * 1000)
        return res

    # --- Anthropic: /v1/messages ------------------------------------------------

    async def _run_anthropic(self, model, system, messages, schema, tools, runner, max_tokens, max_calls):
        msgs = [dict(x) for x in messages]
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            # Системный промпт статичен → кешируется; регламент и вопрос идут в сообщении после точки кэша.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        }
        if schema:
            kwargs["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
        if tools:
            kwargs["tools"] = sorted(tools, key=lambda t: t["name"])
        res = LLMResult("", model, None)
        for i in range(max_calls + 1):
            if tools and i == max_calls:  # лимит вызовов: последний шаг — только ответ, без инструментов
                kwargs["tool_choice"] = {"type": "none"}
            r = await self.anthropic.messages.create(messages=msgs, **kwargs)
            res.calls += 1
            res.returned_model = r.model
            _add_usage(res.usage, r.usage.model_dump(include={"input_tokens", "output_tokens",
                                                              "cache_read_input_tokens", "cache_creation_input_tokens"}))
            if r.stop_reason == "refusal":
                raise RefusalError(model)
            uses = [b for b in r.content if b.type == "tool_use"]
            if not uses or runner is None:
                res.text = "".join(b.text for b in r.content if b.type == "text")
                return res
            # Ответ модели возвращаем в историю без изменений (вместе с блоками thinking).
            msgs.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content]})
            results = []
            for b in uses:
                out = await runner(b.name, dict(b.input))
                res.tool_calls.append({"name": b.name, "args": dict(b.input), "result": out})
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)})
            msgs.append({"role": "user", "content": results})
        raise LLMError("tool_call_limit")

    # --- Gemini: /v1/chat/completions ----------------------------------------

    async def _run_openai(self, model, system, messages, schema, tools, runner, max_tokens, max_calls):
        msgs = [{"role": "system", "content": system}] + [dict(x) for x in messages]
        kwargs = {"model": model, "max_tokens": max_tokens}
        if schema:
            kwargs["response_format"] = {"type": "json_schema",
                                         "json_schema": {"name": "reply", "strict": True, "schema": schema}}
        if tools:
            kwargs["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in sorted(tools, key=lambda t: t["name"])]
            kwargs["tool_choice"] = "auto"
        res = LLMResult("", model, None)
        for i in range(max_calls + 1):
            if tools and i == max_calls:
                kwargs["tool_choice"] = "none"
            r = await self.openai.chat.completions.create(messages=msgs, **kwargs)
            res.calls += 1
            res.returned_model = r.model
            if r.usage:
                u = r.usage
                _add_usage(res.usage, {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens,
                                       "cache_read_input_tokens": getattr(u.prompt_tokens_details, "cached_tokens", 0) or 0})
            if not r.choices:
                raise LLMError("empty_choices")
            msg = r.choices[0].message
            if not msg.tool_calls or runner is None:
                res.text = msg.content or ""
                return res
            msgs.append({"role": "assistant", "content": msg.content or "",
                         "tool_calls": [tc.model_dump() for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                out = await runner(tc.function.name, args)
                res.tool_calls.append({"name": tc.function.name, "args": args, "result": out})
                msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out, ensure_ascii=False)})
        raise LLMError("tool_call_limit")
