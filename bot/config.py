"""Загрузка конфигурации: регламент, маршруты, промпты, схема ответа."""
import json
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


@lru_cache
def policy() -> dict:
    return json.loads((ROOT / "kb" / "policy.json").read_text(encoding="utf-8"))


@lru_cache
def policy_items() -> dict[str, dict]:
    return {item["id"]: item for item in policy()["items"]}


@lru_cache
def routes() -> dict:
    return yaml.safe_load((ROOT / "config" / "routes.yaml").read_text(encoding="utf-8"))


@lru_cache
def prompt(name: str) -> str:
    return (ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


@lru_cache
def answer_schema() -> dict:
    return json.loads((ROOT / "config" / "answer_schema.json").read_text(encoding="utf-8"))


def policy_block(ids: list[str]) -> str:
    items = policy_items()
    return "\n".join(f"{i} {items[i]['text']}" for i in ids if i in items)
