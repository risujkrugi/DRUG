"""Резервные модели: при таймауте или ошибке агент пробует ещё до 4 других моделей, не отдавая шаблон."""
import asyncio
import json

from bot import config, templates
from bot.access import Access
from bot.agent import Assistant
from bot.cache import ResponseCache
from bot.providers import LLMResult

GOOD = json.dumps({"answer": "Скидок нет ни при каких условиях.", "rule_ids": ["P6"],
                   "needs_operator": False, "clarifying_question": False}, ensure_ascii=False)


class FakeTools:
    tools: dict = {}


class FakeLLM:
    def __init__(self, failing: int):
        self.failing = failing        # сколько вызовов агента подряд падают по таймауту
        self.agent_models: list[str] = []

    async def run(self, model_key, system, messages, *, schema=None, **kw):
        name = config.routes()["models"][model_key]["name"]
        if schema == config.answer_schema():
            self.agent_models.append(name)
            if len(self.agent_models) <= self.failing:
                raise TimeoutError
            return LLMResult(GOOD, name, name)
        return LLMResult(json.dumps({"ok": True, "violations": []}), name, name)  # верификатор


def ask(llm, tmp_path):
    a = Assistant(llm, FakeTools(), ResponseCache(), Access(set()), trace_path=tmp_path / "t.jsonl")
    return asyncio.run(a.answer("u1", "есть промокод?"))


def test_four_failures_then_fifth_model_answers(tmp_path):
    llm = FakeLLM(failing=4)
    res = ask(llm, tmp_path)
    assert res.source == "llm" and "Скидок нет" in res.text
    assert len(llm.agent_models) == 5 and len(set(llm.agent_models)) == 5  # 5 разных моделей
    assert sum("error" in a for a in res.trace["attempts"]) == 4


def test_all_five_fail_gives_service_down(tmp_path):
    llm = FakeLLM(failing=99)
    res = ask(llm, tmp_path)
    assert res.source == "template" and res.text == templates.SERVICE_DOWN
    assert len(llm.agent_models) == 5
