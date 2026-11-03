"""Прогон эвал-набора через настоящий конвейер (модели через прокси).

    python tests/run_evals.py --crash --runs 3      # только 3 вопроса ТЗ → reports/crash_test.md
    python tests/run_evals.py --runs 5              # весь набор → reports/evals.md (+ crash_test.md)
"""
import argparse
import asyncio
import os
import re
import sys
import uuid
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import tracing  # noqa: E402
from bot.app import build_assistant  # noqa: E402
from bot.config import ROOT  # noqa: E402

CASES = Path(__file__).with_name("cases.yaml")
REPORTS = ROOT / "reports"


def question_of(case: dict) -> str:
    if "question_repeat" in case:
        s, n = case["question_repeat"]
        return s * n
    return case["question"]


def grade(case: dict, res) -> tuple[str, list[str]]:
    t, problems = res.text, []
    for rx in case.get("must_not", []):
        if re.search(rx, t, re.I):
            problems.append(f"запрещено: /{rx}/")
    if case.get("source") == "template":
        if res.source != "template":
            problems.append("ожидался шаблон")
    elif res.source == "template":
        return ("fallback" if not problems else "fail"), problems + ["безопасный шаблон вместо ответа"]
    for rx in case.get("must", []):
        if not re.search(rx, t, re.I):
            problems.append(f"нет: /{rx}/")
    router = res.trace.get("router", {})
    for i in case.get("intents", []):
        if i not in router.get("intents", []):
            problems.append(f"роутер не нашёл {i}")
    if case.get("injection") and not router.get("injection_suspected"):
        problems.append("инъекция не распознана")
    called = {m["tool"] for m in res.trace.get("mcp", [])}
    for tool in case.get("tools", []):
        if tool not in called:
            problems.append(f"не вызван {tool}")
    if case.get("operator") and not res.needs_operator:
        problems.append("нет кнопки оператора")
    return ("pass" if not problems else "fail"), problems


async def run_all(cases: list[dict], runs: int, concurrency: int) -> list[dict]:
    os.environ["DEBUG_TRACES"] = "1"  # в эвалах сырой текст нужен для разбора
    sem = asyncio.Semaphore(concurrency)
    async with build_assistant(cache_path=":memory:", trace_path=ROOT / "logs" / "eval_traces.jsonl") as assistant:
        assistant.access.budget.daily_tokens = 10 ** 9

        async def one(case, n):
            async with sem:
                uid = f"eval-{uuid.uuid4().hex[:8]}"  # свой пользователь: лимиты и паузы не пересекаются
                hist = [tuple(x) for x in case.get("history", [])]
                res = await assistant.answer(uid, question_of(case), hist)
                status, problems = grade(case, res)
                print(f"{case['id']} #{n + 1}: {status} {res.trace.get('ms_total', 0) / 1000:.1f}s {problems}", flush=True)
                return {"case": case, "run": n + 1, "res": res, "status": status, "problems": problems}

        return await asyncio.gather(*(one(c, n) for c in cases for n in range(runs)))


def md(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ")


def crash_report(rows: list[dict]) -> str:
    out = ["# Краш-тест", "",
           "Три вопроса из ТЗ, прогнаны через полный конвейер бота (модели через API-прокси). "
           "Каждый вопрос — несколько независимых прогонов, ответы приведены дословно.", ""]
    for cid in ("Q1", "Q2", "Q3"):
        rs = [r for r in rows if r["case"]["id"] == cid]
        if not rs:
            continue
        ok = sum(r["status"] == "pass" for r in rs)
        out += [f"## {cid}. «{rs[0]['case']['question']}»", "", f"Прошли проверки: **{ok} из {len(rs)}**", ""]
        for r in rs:
            tr = r["res"].trace
            out += [f"**Прогон {r['run']}** — {r['status']}" + (f" ({'; '.join(r['problems'])})" if r["problems"] else ""),
                    "", f"> {r['res'].text}" + (" `[кнопка «Позвать оператора»]`" if r["res"].needs_operator else ""),
                    "", "```", tracing.debug_summary(tr), "```", ""]
    return "\n".join(out)


def evals_report(rows: list[dict], runs: int) -> str:
    by_case: dict[str, list] = {}
    for r in rows:
        by_case.setdefault(r["case"]["id"], []).append(r)
    total = len(rows)
    passed = sum(r["status"] == "pass" for r in rows)
    fallback = sum(r["status"] == "fallback" for r in rows)
    out = ["# Эвалы", "", f"Прогонов: {total} ({len(by_case)} кейсов × {runs}). "
           f"Прошли: **{passed}** ({100 * passed / total:.0f}%), безопасный шаблон: {fallback}, "
           f"провал: {total - passed - fallback}.", "",
           "| Кейс | Вопрос | pass | Маршрут | Агент | Пример ответа | Проблемы |", "|---|---|---|---|---|---|---|"]
    for cid, rs in by_case.items():
        tr = rs[0]["res"].trace
        agent = next((c["model"] for c in tr.get("llm_calls", []) if c["role"] == "agent"), "—")
        probs = sorted({p for r in rs for p in r["problems"]})
        q = question_of(rs[0]["case"])
        out.append(f"| {cid} | {md(q[:60])} | {sum(r['status'] == 'pass' for r in rs)}/{len(rs)} | "
                   f"{', '.join(tr.get('router', {}).get('intents', []))} | {agent} | {md(rs[0]['res'].text[:160])} | "
                   f"{md('; '.join(probs)) or '—'} |")
    return "\n".join(out) + "\n"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--crash", action="store_true", help="только вопросы ТЗ")
    p.add_argument("--only", nargs="*", help="id кейсов")
    p.add_argument("--concurrency", type=int, default=4)
    a = p.parse_args()
    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    if a.crash:
        cases = [c for c in cases if c.get("crash")]
    if a.only:
        cases = [c for c in cases if c["id"] in a.only]
    rows = asyncio.run(run_all(cases, a.runs, a.concurrency))
    REPORTS.mkdir(exist_ok=True)
    if any(r["case"].get("crash") for r in rows):
        (REPORTS / "crash_test.md").write_text(crash_report(rows), encoding="utf-8")
    if not a.crash:
        (REPORTS / "evals.md").write_text(evals_report(rows, a.runs), encoding="utf-8")
    passed = sum(r["status"] == "pass" for r in rows)
    print(f"\nИтого: {passed}/{len(rows)} pass")


if __name__ == "__main__":
    main()
