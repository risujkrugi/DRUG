"""Кэш готовых ответов (SQLite): только точное совпадение после нормализации.
Семантический кэш не используем: «вернуть на 14-й день» и «на 16-й день» для эмбеддингов почти одинаковы."""
import hashlib
import json
import logging
import re
import sqlite3
import time
from pathlib import Path

from bot import config, guards

DEFAULT_TTL_S = 24 * 3600
log = logging.getLogger(__name__)
NO_CACHE_INTENTS = {"order_status"}


def normalize_key_text(text: str) -> str:
    text = guards.normalize(text).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def make_key(text: str, route_key: str) -> str:
    version = config.policy()["version"]
    digest = hashlib.sha256(normalize_key_text(text).encode()).hexdigest()
    return f"v{version}:{route_key}:{digest}"


# Инструменты, результат которых целиком определяется аргументами: ответ можно кешировать по точному тексту.
# get_order сюда не входит: статус заказа меняется.
DETERMINISTIC_TOOLS = {"check_return"}


def is_cacheable(text: str, intents: list[str], injection_suspected: bool, reply: dict, tool_calls: list[str]) -> bool:
    if injection_suspected or NO_CACHE_INTENTS & set(intents) or reply.get("needs_operator"):
        return False
    if tool_calls:
        # Точный текст вопроса (с числом дней) входит в ключ, поэтому ответ по check_return безопасен;
        # уточняющий вопрос «есть ли брак?» тоже часть этого детерминированного ответа.
        return set(tool_calls) <= DETERMINISTIC_TOOLS
    if reply.get("clarifying_question"):
        return False
    # Без инструментов числа, которых нет в регламенте, делают вопрос уникальным.
    return guards.numbers_in(text) <= guards.POLICY_NUMBERS


class ResponseCache:
    def __init__(self, path: str | Path = ":memory:", ttl_s: int = DEFAULT_TTL_S, clock=time.time):
        self.ttl_s, self.clock = ttl_s, clock
        try:
            self.db = self._open(str(path))
        except sqlite3.Error as e:  # файл недоступен для записи — работаем с кэшем в памяти
            log.warning("cache file %s unavailable (%s), using memory", path, e)
            self.db = self._open(":memory:")

    @staticmethod
    def _open(path: str) -> sqlite3.Connection:
        db = sqlite3.connect(path, check_same_thread=False)
        # Журнал в памяти: без лишних файлов рядом с базой.
        db.execute("PRAGMA journal_mode=MEMORY")
        db.execute("CREATE TABLE IF NOT EXISTS replies (key TEXT PRIMARY KEY, reply TEXT, ts REAL)")
        db.execute("INSERT OR REPLACE INTO replies VALUES ('__probe__', '{}', 0)")  # проверка записи
        db.execute("DELETE FROM replies WHERE key = '__probe__'")
        db.commit()
        return db

    # Кэш — ускорение, а не зависимость: любая ошибка базы означает промах, а не падение ответа.
    def get(self, key: str) -> dict | None:
        try:
            row = self.db.execute("SELECT reply, ts FROM replies WHERE key = ?", (key,)).fetchone()
            if row is None:
                return None
            if self.clock() - row[1] > self.ttl_s:
                self.db.execute("DELETE FROM replies WHERE key = ?", (key,))
                self.db.commit()
                return None
            return json.loads(row[0])
        except (sqlite3.Error, ValueError) as e:
            log.warning("cache get failed: %s", e)
            return None

    def put(self, key: str, reply: dict) -> None:
        try:
            self.db.execute("INSERT OR REPLACE INTO replies VALUES (?, ?, ?)",
                            (key, json.dumps(reply, ensure_ascii=False), self.clock()))
            self.db.commit()
        except sqlite3.Error as e:
            log.warning("cache put failed: %s", e)
