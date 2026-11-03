"""Трейсы: один JSON на запрос в logs/traces.jsonl. Сырой текст — только при DEBUG_TRACES=1."""
import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bot.config import ROOT

TRACE_PATH = ROOT / "logs" / "traces.jsonl"


class Trace:
    def __init__(self, user_id: str, text: str, path: Path | None = None,
                 raw: bool | None = None, salt: str | None = None):
        self.path = path or TRACE_PATH
        self.raw = os.getenv("DEBUG_TRACES") == "1" if raw is None else raw
        salt = salt if salt is not None else os.getenv("TRACE_SALT", "")
        self._t0 = time.monotonic()
        self.data: dict = {
            "trace_id": uuid.uuid4().hex[:16],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "user_hash": hashlib.sha256(f"{salt}:{user_id}".encode()).hexdigest()[:16],
            "input": {"len": len(text), **({"text": text} if self.raw else {})},
            "llm_calls": [],
            "mcp": [],
        }

    def set(self, key: str, value) -> None:
        self.data[key] = value

    def llm(self, role: str, model: str, returned_model: str | None, usage: dict, ms: int, **extra) -> None:
        call = {"role": role, "model": model, "returned_model": returned_model, "usage": usage, "ms": ms, **extra}
        if returned_model and returned_model != model and not returned_model.startswith(model):
            call["model_mismatch"] = True
        self.data["llm_calls"].append(call)

    def tool(self, name: str, args: dict, result: dict, ms: int, **extra) -> None:
        self.data["mcp"].append({"tool": name, "args": args, "result": result, "ms": ms, **extra})

    def finish(self, output: dict) -> dict:
        out = dict(output)
        if not self.raw:
            out.pop("answer", None)
        self.data["output"] = out
        self.data["ms_total"] = int((time.monotonic() - self._t0) * 1000)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(self.data, ensure_ascii=False) + "\n")
        return self.data


def debug_summary(data: dict) -> str:
    """Краткая версия трейса для /debug под ответом в Telegram."""
    r = data.get("router", {})
    lines = [
        f"маршрут: {', '.join(r.get('intents', []))} ({r.get('by', '—')})"
        + (" · подозрение на инъекцию" if r.get("injection_suspected") else ""),
        f"модели: " + (", ".join(f"{c['role']}={c['model']}" for c in data.get("llm_calls", [])) or "—"),
        f"регламент: {', '.join(h['id'] for h in data.get('rag', [])) or '—'}",
        f"инструменты: " + (", ".join(f"{m['tool']}({json.dumps(m['args'], ensure_ascii=False)})"
                                       for m in data.get("mcp", [])) or "—"),
        f"guard: {'ok' if data.get('guard', {}).get('ok', True) else ', '.join(v['code'] for v in data['guard']['violations'])}",
        f"верификатор: {_verdict(data.get('verifier'))}",
        f"ответ: {data.get('output', {}).get('source', '—')} · rule_ids: {', '.join(data.get('output', {}).get('rule_ids', [])) or '—'}"
        f" · кэш: {data.get('cache', '—')} · {data.get('ms_total', 0) / 1000:.1f} с",
    ]
    return "\n".join(lines)


def _verdict(v: dict | None) -> str:
    if not v:
        return "—"
    return "ok" if v.get("ok") else "отклонил: " + "; ".join(x.get("reason", "") for x in v.get("violations", []))[:200]
