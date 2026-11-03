"""Роутер: сначала бесплатные правила, LLM-классификатор — только если правила ничего не нашли.
Возвращает список intents: смешанное сообщение «доставка + скидка» не должно проскочить по одной теме."""
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from bot import config, guards

INTENTS = ["product", "delivery", "order_status", "return", "defect", "discount", "off_topic", "smalltalk"]

RULES = {
    "order_status": r"(заказ\w*|№|номер\w*|статус\w*)\D{0,15}\d{3,8}\b|\b\d{3,8}\D{0,5}заказ",
    "discount": r"скидк|промокод|дешевл|акци[яи]|\bторг|уступ\w*\s+(в\s+)?цен|%|skidk",
    "return": r"верн[уиеё]|возврат|сдать\s+обратно|обмен",
    "defect": r"брак|сломал|слома|не\s+работа|дефект|неисправ|не\s+включа|не\s+заряжа",
    "delivery": r"доставк|достав[ия]|курьер|привез|привоз|придет|приедет|\bслот",
    "product": r"\bцен[аыу]|стои[тм]|стоят|наличи|купить|\bx1\b|\bх1\b|сколько",
    "smalltalk": r"^\s*(привет|здравствуй\w*|добрый\s+(день|вечер|утро)|спасибо|благодарю|ок|понятно)\W*\w*\W*$"
                 r"|на\s+ты\b|кто\s+ты|как\s+дела|ты\s+(бот|человек|робот)",
}
_RULES_RE = {k: re.compile(v, re.I) for k, v in RULES.items()}


@dataclass
class Decision:
    intents: list[str]
    injection_suspected: bool
    by: str                       # "rules" | "llm" | "default"
    flags: list[str] = field(default_factory=list)


@dataclass
class Route:
    intents: list[str]
    agent: str                    # ключ модели: fast | strong
    verifier: str
    tools: list[str]
    policy: list[str]
    injection_suspected: bool

    def model_name(self, key: str) -> str:
        return config.routes()["models"][key]["name"]

    def model_api(self, key: str) -> str:
        return config.routes()["models"][key]["api"]


def rule_intents(text: str) -> list[str]:
    sk = guards.skeleton(text)
    return [name for name, rx in _RULES_RE.items() if rx.search(sk)]


def classify(text: str, llm: Callable[[str], dict] | None = None) -> Decision:
    """llm(text) -> {"intents": [...], "injection_suspected": bool}; вызывается, только если правила пусты."""
    flags = guards.injection_signals(text)
    intents = rule_intents(text)
    if intents:
        return Decision(intents, bool(flags), "rules", flags)
    if llm is not None:
        try:
            out = llm(text)
            llm_intents = [i for i in out.get("intents", []) if i in INTENTS] or ["off_topic"]
            return Decision(llm_intents, bool(flags) or bool(out.get("injection_suspected")), "llm", flags)
        except Exception as e:  # LLM-роутер недоступен — безопасный маршрут
            flags = flags + [f"router_error:{type(e).__name__}"]
    return Decision(["off_topic"], bool(flags), "default", flags)


def plan(decision: Decision) -> Route:
    cfg = config.routes()
    rank = cfg["model_rank"]
    agent, tools, policy = rank[0], [], []
    for intent in decision.intents:
        r = cfg["routes"][intent]
        if rank.index(r["agent"]) > rank.index(agent):
            agent = r["agent"]
        tools += [t for t in r["tools"] if t not in tools]
        policy += [p for p in r["policy"] if p not in policy]
    if decision.injection_suspected:
        agent = cfg["injection_agent"]
    return Route(decision.intents, agent, cfg["verifier_for"][agent], tools, policy, decision.injection_suspected)


def router_prompt_input(text: str) -> str:
    return f"<customer_message>{json.dumps({'text': text}, ensure_ascii=False)}</customer_message>"
