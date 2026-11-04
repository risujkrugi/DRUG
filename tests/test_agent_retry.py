"""Повторная попытка после отказа guard не обязана заново вызывать инструмент:
результат check_return из первой попытки сохраняется."""
import asyncio
import json

from bot import config
from bot.access import Access
from bot.agent import Assistant
from bot.cache import ResponseCache
from bot.providers import LLMResult

FIRST = json.dumps({"answer": "Вернуть можно в течение 14 дней с момента покупки.", "rule_ids": ["P3"],
                    "needs_operator": False, "clarifying_question": False}, ensure_ascii=False)
SECOND = json.dumps({"answer": "Вскрытые наушники без брака можно вернуть в течение 14 дней, 16-й день уже вне срока. "
                               "Есть ли у наушников брак?", "rule_ids": ["P3", "P4"],
                     "needs_operator": False, "clarifying_question": True}, ensure_ascii=False)


class Tools:
    tools = {"check_return": {"name": "check_return", "description": "d", "input_schema": {"type": "object"}}}

    async def call(self, name, args, allowed):
        return {"eligible": False, "rule_ids": ["P3", "P4"], "need_info": "уточнить, есть ли брак"}


class LLM:
    def __init__(self):
        self.calls, self.prompts = 0, []

    async def run(self, model_key, system, messages, *, schema=None, tool_runner=None, **kw):
        name = config.routes()["models"][model_key]["name"]
        if schema == config.answer_schema():
            self.calls += 1
            self.prompts.append(messages[-1]["content"])
            if self.calls == 1:   # первая попытка: инструмент вызван, но в тексте выдуманная точка отсчёта
                await tool_runner("check_return", {"days": 16, "opened": True, "defect": None})
                return LLMResult(FIRST, name, name)
            return LLMResult(SECOND, name, name)   # вторая: ответ без повторного вызова инструмента
        return LLMResult(json.dumps({"ok": True, "violations": []}), name, name)


def test_retry_without_new_tool_call_still_passes(tmp_path):
    llm = LLM()
    a = Assistant(llm, Tools(), ResponseCache(), Access(set()), trace_path=tmp_path / "t.jsonl")
    res = asyncio.run(a.answer("u1", "вернуть вскрытые наушники на 16-й день"))
    assert res.source == "llm" and "без брака" in res.text
    assert llm.calls == 2
    assert "need_info" in llm.prompts[1]   # результат инструмента передан в повторный запрос
