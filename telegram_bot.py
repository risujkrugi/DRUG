"""Telegram-бот (aiogram 3, long polling). Ответы модели — plain text; HTML только в статичном /start."""
import asyncio
import json
import logging
import os
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           Message, ReplyKeyboardMarkup)
from aiogram.utils.chat_action import ChatActionSender
from dotenv import load_dotenv

from bot import templates, tracing
from bot.app import build_assistant
from bot.config import ROOT

log = logging.getLogger("tz-bot")
dp = Dispatcher()
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=4))
debug_on: set[str] = set()
assistant = None

START_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=q)] for q in templates.START_BUTTONS],
                               resize_keyboard=True)
OPERATOR_KB = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=templates.OPERATOR_BUTTON,
                                                                          callback_data="operator")]])


@dp.message(CommandStart())
async def on_start(m: Message):
    await m.answer(templates.START_HTML, parse_mode=ParseMode.HTML, reply_markup=START_KB)


@dp.message(Command("debug"))
async def on_debug(m: Message):
    uid = str(m.from_user.id)
    if not assistant.access.can_debug(uid):
        return await m.answer("Команда недоступна.")
    arg = (m.text or "").split(maxsplit=1)[1:] or [""]
    turn_on = arg[0] != "off" and (arg[0] == "on" or uid not in debug_on)
    (debug_on.add if turn_on else debug_on.discard)(uid)
    await m.answer("Отладка включена: под каждым ответом будет маршрут, модели, пункты регламента, "
                   "инструменты и вердикты проверок." if turn_on else "Отладка выключена.")


@dp.message(Command("stats"))
async def on_stats(m: Message):
    if not assistant.access.is_admin(str(m.from_user.id)):
        return await m.answer("Команда недоступна.")
    await m.answer(stats_text())


@dp.callback_query(F.data == "operator")
async def on_operator(cq: CallbackQuery):
    log.info("operator requested by %s", cq.from_user.id)
    await cq.answer()
    await cq.message.answer(templates.OPERATOR_ACK)


@dp.message(F.text)
async def on_text(m: Message):
    uid = str(m.from_user.id)
    try:
        async with ChatActionSender.typing(bot=m.bot, chat_id=m.chat.id):
            res = await assistant.answer(uid, m.text, list(history[m.chat.id]))
    except Exception:  # пользователь всегда получает ответ, даже если что-то сломалось
        log.exception("answer failed")
        return await m.answer(templates.SERVICE_DOWN, reply_markup=OPERATOR_KB)
    if res.source != "template":
        history[m.chat.id].append((m.text, res.text))
    await m.answer(res.text, reply_markup=OPERATOR_KB if res.needs_operator else None)
    if uid in debug_on:
        await m.answer(tracing.debug_summary(res.trace))


@dp.message()
async def on_other(m: Message):
    await m.answer("Я понимаю только текстовые сообщения.")


def stats_text() -> str:
    path = tracing.TRACE_PATH
    if not path.exists():
        return "Пока нет данных."
    today = datetime.now(timezone.utc).date().isoformat()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.startswith("{")]
    rows = [r for r in rows if r.get("ts", "").startswith(today)]
    if not rows:
        return "Сегодня запросов не было."
    src = Counter(r.get("output", {}).get("source") for r in rows)
    models = Counter(c["model"] for r in rows for c in r.get("llm_calls", []) if c.get("role") == "agent")
    n = len(rows)
    pct = lambda k: f"{100 * k / n:.0f}%"
    return "\n".join([
        f"Запросов сегодня: {n}",
        f"Ответы: модель {src['llm']}, кэш {src['cache']} ({pct(src['cache'])}), шаблон {src['template']} ({pct(src['template'])})",
        f"Нужен оператор: {pct(sum(1 for r in rows if r.get('output', {}).get('needs_operator')))}",
        f"Guard отклонил черновик: {sum(1 for r in rows for a in r.get('attempts', []) if 'guard' in a)}",
        f"Верификатор отклонил: {sum(1 for r in rows for a in r.get('attempts', []) if 'verifier' in a)}",
        f"Подозрение на инъекцию: {sum(1 for r in rows if r.get('router', {}).get('injection_suspected'))}",
        f"Подмена модели прокси: {sum(1 for r in rows for c in r.get('llm_calls', []) if c.get('model_mismatch'))}",
        "Агент: " + ", ".join(f"{k} {v}" for k, v in models.most_common()),
        f"Среднее время ответа: {sum(r.get('ms_total', 0) for r in rows) / n / 1000:.1f} с",
    ])


async def main():
    global assistant
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bot = Bot(os.environ["TELEGRAM_BOT_TOKEN"])
    await bot.set_my_commands([BotCommand(command="start", description="Начать"),
                               BotCommand(command="debug", description="Показать, как бот принял решение")])
    async with build_assistant() as a:
        assistant = a
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
