"""Локальный прогон в терминале.

    python cli.py "доставка заказа 1842"             # полный конвейер с моделями через прокси
    python cli.py --offline "доставка заказа 1842"   # без LLM: вход, кэш, роутер, RAG, MCP-проверка, трейс
"""
import argparse
import asyncio
import json
import re

from bot import cache, guards, rag, router, templates, tracing
from bot.mcp_client import ShopTools


async def offline(text: str) -> dict:
    trace = tracing.Trace("cli", text, raw=True)
    inp = guards.check_input(text)
    trace.set("input_check", {"ok": inp.ok, "flags": inp.flags, "reason": inp.reason})
    if not inp.ok:
        return trace.finish({"source": "template", "answer": templates.BY_REASON[inp.reason]})

    decision = router.classify(inp.text)
    route = router.plan(decision)
    trace.set("router", {"by": decision.by, "intents": decision.intents, "flags": decision.flags,
                         "injection_suspected": route.injection_suspected,
                         "agent": route.model_name(route.agent), "verifier": route.model_name(route.verifier),
                         "tools": route.tools})
    trace.set("cache", "miss" if cache.ResponseCache().get(cache.make_key(inp.text, route.agent)) is None else "hit")
    hits = rag.retrieve(inp.text, decision.intents)
    trace.set("rag", [h.__dict__ for h in hits])

    # Без LLM вызываем инструменты маршрута напрямую, чтобы показать, что вернёт MCP.
    async with ShopTools() as tools:
        if "get_order" in route.tools:
            m = re.search(r"\d{3,8}", inp.text)
            if m:
                args = {"order_id": m.group(0)}
                trace.tool("get_order", args, await tools.call("get_order", args, route.tools), 0)
        if "check_return" in route.tools:
            days = re.search(r"(\d{1,3})\s*-?\s*(й|ый|ой|ий)?\s*(день|дн)", inp.text)
            args = {"days": int(days.group(1)) if days else None,
                    "opened": bool(re.search(r"вскры|распак|открыл", inp.text, re.I)), "defect": None}
            args, notes = guards.fix_return_args(args, inp.text)
            trace.tool("check_return", args, await tools.call("check_return", args, route.tools), 0, notes=notes)

    return trace.finish({"source": "offline", "rule_ids": [h.id for h in hits]})


async def online(text: str, no_cache: bool) -> tuple[str, dict]:
    from bot.app import build_assistant
    async with build_assistant(cache_path=":memory:" if no_cache else None) as assistant:
        res = await assistant.answer("cli", text)
        return res.text + ("\n[кнопка: " + templates.OPERATOR_BUTTON + "]" if res.needs_operator else ""), res.trace


def main():
    p = argparse.ArgumentParser()
    p.add_argument("text")
    p.add_argument("--offline", action="store_true", help="без LLM")
    p.add_argument("--no-cache", action="store_true", help="не использовать кэш готовых ответов")
    p.add_argument("--json", action="store_true", help="вывести трейс целиком")
    a = p.parse_args()
    if a.offline:
        data = asyncio.run(offline(a.text))
    else:
        answer, data = asyncio.run(online(a.text, a.no_cache))
        print(answer, "\n")
    print(json.dumps(data, ensure_ascii=False, indent=2) if a.json else tracing.debug_summary(data))


if __name__ == "__main__":
    main()
