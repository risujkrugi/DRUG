"""Детерминированные проверки без LLM: вход (нормализация, сигналы инъекций) и выход (guard).
Модель за прокси может ошибиться — эти проверки не зависят от того, какая модель ответила."""
import json
import re
import unicodedata
from dataclasses import dataclass, field

MAX_INPUT_LEN = 1000
MAX_ANSWER_LEN = 600

_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤﻿]")
# Латинские буквы, похожие на кириллицу: «скидкa» с латинской a не должна проходить мимо проверок.
_HOMOGLYPHS = str.maketrans("aceopxykmtbhABCEHKMOPTXY", "асеорхукмтвнАВСЕНКМОРТХУ")

# --- Вход ------------------------------------------------------------------

INJECTION_PATTERNS = {
    "forget_rules": r"(забудь|забыть|игнорир\w*|отмени|сбрось)\W+(\w+\W+){0,3}(правил|инструкц|ограничен|промпт)",
    "ignore_en": r"ignore\W+(all\W+|the\W+)?(previous|prior|above)|disregard\W+(all|previous|the)",
    "new_role": r"ты\W+(теперь|больше\s+не)|представь,?\s+что\s+ты|веди\s+себя\s+как|act\s+as|you\s+are\s+now",
    "jailbreak": r"\bdan\b|developer\s+mode|режим\w*\s+разработчик|jailbreak|без\s+ограничений",
    "prompt_leak": r"(системн\w*|system)\W+(промпт|prompt|инструкц)|выведи\W+(свои|твои)\W+(инструкц|правил)|покажи\W+(свой\W+)?промпт",
    "fake_authority": r"\bя\W+(владелец|директор|админ\w*|администратор|разработчик|тестировщик|менеджер)|по\s+распоряжению|руководство\s+разрешил",
    "translit": r"zabud\w*|ignoriru\w*|skidk\w*|pravil\w*",
}
_INJECTION_RE = {name: re.compile(p, re.I) for name, p in INJECTION_PATTERNS.items()}
_DEFECT_RE = re.compile(r"брак|сломал|слома|не\s+работа|дефект|неисправ|не\s+включа|не\s+заряжа|хрип|трещ", re.I)


@dataclass
class InputCheck:
    ok: bool
    text: str
    flags: list[str] = field(default_factory=list)
    reason: str | None = None


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = text.replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip()


def skeleton(text: str) -> str:
    """Версия текста для поиска сигналов: латинские двойники заменены на кириллицу."""
    return normalize(text).lower().translate(_HOMOGLYPHS)


def injection_signals(text: str) -> list[str]:
    raw, sk = normalize(text).lower(), skeleton(text)
    return [f"regex:{name}" for name, rx in _INJECTION_RE.items() if rx.search(raw) or rx.search(sk)]


def check_input(text: str) -> InputCheck:
    if not text or not text.strip():
        return InputCheck(False, "", reason="empty")
    if len(text) > MAX_INPUT_LEN:
        return InputCheck(False, "", reason="too_long")
    clean = normalize(text)
    return InputCheck(True, clean, flags=injection_signals(clean))


def mentions_defect(text: str) -> bool:
    return bool(_DEFECT_RE.search(skeleton(text)))


def fix_return_args(args: dict, customer_text: str) -> tuple[dict, list[str]]:
    """Если клиент не говорил о браке, модель не может решить за него, что брака нет (найдено в прогоне)."""
    fixed, notes = dict(args), []
    if "defect" in fixed and fixed["defect"] is not None and not mentions_defect(customer_text):
        fixed["defect"] = None
        notes.append("defect_forced_null")
    return fixed, notes


# --- Выход -----------------------------------------------------------------

# Числа из регламента. Время слота 18:00–21:00 даёт ещё «00».
POLICY_NUMBERS = {"2490", "14", "3", "18", "21", "00"}
_NUMBER_RE = re.compile(r"(?<![A-Za-zА-Яа-я\d])\d{1,3}(?:[  ]\d{3})+(?!\d)|(?<![A-Za-zА-Яа-я\d])\d+(?!\d)")

NEEDS_NEGATION = r"скидк|промокод|бонус|компенсац|акци[яи]|подар"
_NEEDS_NEGATION_RE = re.compile(NEEDS_NEGATION, re.I)
_NEGATION_RE = re.compile(r"\b(нет|не|ни|отсутству\w*|невозможн\w*|нельзя)\b", re.I)
_ALWAYS_FORBIDDEN = {
    "exception": re.compile(r"в\s+виде\s+исключени|сделаем\s+исключени|исключительно\s+для\s+вас|в\s+порядке\s+исключени", re.I),
    "percent": re.compile(r"%|процент", re.I),
    # Точки отсчёта срока в регламенте нет (найдено в прогоне: «с момента покупки»).
    "return_anchor": re.compile(
        r"(с|со)\s+(момента|дня|даты)\s+(покупк|получени|оформлени|оплат|доставк|заказ)"
        r"|\b(с|со|после)\s+(покупки|получения|оформления|оплаты|доставки)\b", re.I),
    "prompt_leak": re.compile(r"ИСТОЧНИКИ ФАКТОВ|<policy|customer_message|системн\w+ промпт", re.I),
}
_DEFECT_MONEY_RE = re.compile(r"(верн[её]м|вернут|возмест\w*|получите)\s+(вам\s+)?(деньг|сумм|стоимост)", re.I)
_PROMISE_CHECK_RE = re.compile(r"\b(проверяю|уточняю|сейчас\s+(посмотрю|проверю|уточню)|минуточку|одну\s+минуту)\b", re.I)

# Факт в ответе → пункт регламента, который обязан быть в rule_ids.
_FACT_RULES = [
    (re.compile(r"2\s?490"), "P1"),
    (re.compile(r"18\D{1,8}21"), "P2"),
    (re.compile(r"3\s+рабочих"), "P3"),
    (re.compile(r"экспертиз", re.I), "P5"),
    (re.compile(r"скидок\s+нет|скидки\s+не\s+предоставл", re.I), "P6"),
]


@dataclass
class GuardResult:
    ok: bool
    violations: list[dict] = field(default_factory=list)
    retryable: bool = False
    rule_ids: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def numbers_in(text: str) -> set[str]:
    return {re.sub(r"[  ]", "", m) for m in _NUMBER_RE.findall(text)}


_CONTRAST_RE = re.compile(r"\s(?:но|однако|зато|а\s+вот|тем\s+не\s+менее)\s", re.I)


def _clauses(text: str) -> list[str]:
    """Отрицание проверяем в пределах предложения, а если в нём есть противопоставление — в пределах его части:
    «скидок нет, но дам промокод» не должно пройти, а «скидок нет, в том числе для любых запросов о скидке» — должно."""
    out = []
    for sentence in re.split(r"[.!?…\n]+", text):
        if not sentence.strip():
            continue
        if _CONTRAST_RE.search(sentence):
            out += [c for c in re.split(r"[;,—–]+|" + _CONTRAST_RE.pattern, sentence, flags=re.I) if c and c.strip()]
        else:
            out.append(sentence)
    return out


def check_output(answer: str, rule_ids: list[str], customer_text: str, tool_results: list[dict],
                 tool_calls: list[str], intents: list[str]) -> GuardResult:
    v: list[dict] = []
    retry: set[str] = set()
    sk = answer.translate(_HOMOGLYPHS)

    def add(code: str, detail: str, retryable: bool = False):
        v.append({"code": code, "detail": detail})
        if retryable:
            retry.add(code)

    if not answer.strip():
        add("empty_answer", "", retryable=True)
    if len(answer) > MAX_ANSWER_LEN:
        add("too_long", str(len(answer)), retryable=True)

    allowed = POLICY_NUMBERS | numbers_in(customer_text) | numbers_in(json.dumps(tool_results, ensure_ascii=False))
    unknown = numbers_in(sk) - allowed
    if unknown:
        add("unknown_number", ", ".join(sorted(unknown)))

    for code, rx in _ALWAYS_FORBIDDEN.items():
        m = rx.search(sk)
        if m:
            add(code, m.group(0))

    for s in _clauses(sk):
        if _NEEDS_NEGATION_RE.search(s) and not _NEGATION_RE.search(s):
            add("unnegated_offer", s.strip()[:80])
    if re.search(r"брак", sk, re.I):
        for s in _clauses(sk):
            if _DEFECT_MONEY_RE.search(s) and not _NEGATION_RE.search(s):
                add("defect_money_promise", s.strip()[:80])

    if _PROMISE_CHECK_RE.search(sk) and not tool_calls:
        add("promise_without_tool", _PROMISE_CHECK_RE.search(sk).group(0), retryable=True)
    if "order_status" in intents and re.search(r"\d{3,8}", customer_text) and "get_order" not in tool_calls:
        add("order_not_checked", "get_order не вызван", retryable=True)
    # Срок считает только код: если клиент назвал число дней, без check_return отвечать нельзя.
    if (re.search(r"\d+\s*-?\s*(й|ый|ой|ий)?\s*(день|дн)", customer_text, re.I)
            and "check_return" not in tool_calls and not _asks_question(sk)):
        add("return_not_checked", "check_return не вызван", retryable=True)

    # rule_ids: дополняем по фактам в ответе, лишних пунктов не убираем.
    fixed = list(dict.fromkeys(r for r in rule_ids if r in {"P1", "P2", "P3", "P4", "P5", "P6"}))
    notes = []
    for rx, pid in _FACT_RULES:
        if rx.search(sk) and pid not in fixed:
            fixed.append(pid)
            notes.append(f"rule_ids+{pid}")
    if re.search(r"\b14\b", sk) and not {"P3", "P4"} & set(fixed):
        fixed.append("P3")
        notes.append("rule_ids+P3")

    ok = not v
    return GuardResult(ok=ok, violations=v, retryable=bool(v) and {x["code"] for x in v} <= retry,
                       rule_ids=sorted(fixed), notes=notes)


def _asks_question(text: str) -> bool:
    return "?" in text
