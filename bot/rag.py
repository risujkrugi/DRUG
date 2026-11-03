"""Поиск пунктов регламента: пункты маршрута + BM25 по тексту и синонимам с русским стеммингом.
Всё локально: без эмбеддингов и внешних API."""
import re
from dataclasses import dataclass
from functools import lru_cache

import snowballstemmer
from rank_bm25 import BM25Okapi

from bot import config

_stemmer = snowballstemmer.stemmer("russian")
_word = re.compile(r"[a-zа-я0-9]+")
MIN_SCORE = 0.8
TOP_K = 3
STOPWORDS = frozenset(
    "и в во на с со по за до из у о об от к ко ли же бы не ни нет да а но или как какой какая "
    "какие каких это тоже можно мне меня я вы мы есть при что чтобы для уже еще ваш ваши у вас "
    "день дня дней".split()
)


def tokenize(text: str) -> list[str]:
    words = _word.findall(text.lower().replace("ё", "е"))
    return _stemmer.stemWords([w for w in words if len(w) > 1 and w not in STOPWORDS])


@dataclass(frozen=True)
class Hit:
    id: str
    score: float
    by: str  # "route" | "bm25"


@lru_cache
def _index() -> tuple[list[str], BM25Okapi]:
    items = config.policy()["items"]
    ids = [i["id"] for i in items]
    docs = [tokenize(i["text"] + " " + " ".join(i["synonyms"])) for i in items]
    return ids, BM25Okapi(docs)


def search(text: str) -> list[Hit]:
    ids, bm25 = _index()
    scores = bm25.get_scores(tokenize(text))
    ranked = sorted(zip(ids, scores), key=lambda p: -p[1])
    return [Hit(i, round(float(s), 2), "bm25") for i, s in ranked[:TOP_K] if s >= MIN_SCORE]


def retrieve(text: str, intents: list[str]) -> list[Hit]:
    """Пункты маршрута идут первыми; BM25 добавляет то, что роутер мог пропустить.
    Ничего не нашлось — пустой список, и бот честно отвечает «нет информации»."""
    routes = config.routes()["routes"]
    hits: dict[str, Hit] = {}
    for intent in intents:
        for pid in routes.get(intent, {}).get("policy", []):
            hits.setdefault(pid, Hit(pid, 0.0, "route"))
    for h in search(text):
        hits.setdefault(h.id, h)
    order = list(config.policy_items())
    return sorted(hits.values(), key=lambda h: order.index(h.id))
