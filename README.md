# DRUG — Drafted Ughten

**English** · [Русский](#русский)

An open-source template for a Telegram support assistant that answers strictly from a knowledge base and never invents facts. When the data is missing, it says so and offers to call a human operator.

The headphone-shop scenario with its policy rules (P1–P6) is only a demo. To adapt the project to your own topic, replace the knowledge base, the prompts and the tools.

## Features

- Answers only from the knowledge base and cites the rules it relies on.
- Checks order status and return eligibility through tools, not from the model's memory.
- Resists "forget the rules" requests, role-play, and instructions hidden in order data.
- Picks a model by question complexity and falls back to reserve models on failure.
- Shows the path of every answer through the layers with the `/debug` command.

## Seven layers

| # | Layer | What it does |
|---|-------|--------------|
| 1 | Models and router | Detects the topic of a message (several at once). Simple questions go to a fast model; returns, defects and suspicious messages go to a stronger one. Every model has a chain of reserves. |
| 2 | Prompt | Answer rules, topic limits and the JSON format live in `prompts/`, not in code. The customer message is passed as data, not as an instruction. |
| 3 | RAG | Facts come only from the knowledge base (`kb/policy.json`). Nothing found means "no information". |
| 4 | MCP tools | Order status and return eligibility are computed by code. Tool output is treated as untrusted data and screened for injections. The tool manifest is protected by hashes. |
| 5 | Access | Rate limits, a pause for repeat offenders, a daily token budget. |
| 6 | Protection | Input and output guards: checks for numbers, percentages, promised discounts and exceptions, prompt leaks. A model from another family verifies the agent's answer. |
| 7 | Cache and observability | Exact-match cache in SQLite (the rules version is part of the key), a JSONL trace for every answer, `/debug` and `/stats` commands. |

## How it works

1. The message passes the input checks and access limits.
2. The router detects topics and picks a route: model, tools, relevant rules.
3. RAG pulls only the matching rules from the knowledge base.
4. The agent answers in JSON and calls MCP tools when needed.
5. The answer is checked by code, then by a verifier model. On a violation a new attempt is requested (up to three), tool results are kept.
6. On a timeout or model error the bot tries up to four reserve models before falling back to a template reply.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in the API key and the bot token
python cli.py "your question"     # try it in the terminal
python telegram_bot.py            # run the bot
```

Tests:

```bash
pytest -q
```

Evaluation run on real models (spends tokens):

```bash
python tests/run_evals.py
```

## Adapting it to your task

1. Replace `kb/policy.json` with your own knowledge base.
2. Edit the prompts in `prompts/`.
3. Add your own tools to the MCP server (`mcp_server/`) and register them in the routes.
4. Describe your test questions, attacks included, in `tests/cases.yaml` and run the evaluation.

Models, routes, limits and timeouts are configured in `config/routes.yaml`. A model or a provider can be changed without touching the code.

## Scaling

- New models and routes are added in the config.
- New tools plug in through MCP without touching the rest of the code.
- Rules are versioned. When the knowledge base changes, old cached answers are not reused.
- The test suite and the evaluation questions grow together with the project.

Today the bot is designed as a single instance with Telegram long polling, and the cache lives in SQLite. For heavy load the next steps are moving the cache and rate limits to Redis, switching to webhooks and running several instances.

## Limitations

- The bot does not replace a human operator. Disputed cases, defects and questions outside the knowledge base are handed to a person.
- Answer quality depends on the chosen models and the proxy. The verifier lowers the risk of mistakes but does not guarantee there are none.
- The demo orders in `mcp_server/data/` are fictional.

---

<a id="русский"></a>

# Русский

Открытый шаблон ассистента поддержки в Telegram, который отвечает строго по базе знаний и не выдумывает факты. Если нужных данных нет, он так и говорит и предлагает позвать оператора.

Сценарий магазина наушников с регламентом P1–P6 нужен только как пример работы. Под свою тему достаточно заменить базу знаний, промпты и инструменты.

## Что умеет

- Отвечает только по базе знаний и указывает, на какие правила опирается.
- Проверяет статус заказа и допустимость возврата через инструменты, а не «из головы» модели.
- Не поддаётся просьбам «забудь правила», ролевым играм и инструкциям, спрятанным в данных заказа.
- Подбирает модель под сложность вопроса и переключается на резервные при сбое.
- Показывает путь каждого ответа по слоям командой `/debug`.

## Семь слоёв

| № | Слой | Что делает |
|---|------|-----------|
| 1 | Модели и роутер | Определяет тему вопроса (можно несколько сразу). Простые вопросы идут на быструю модель, возврат, брак и подозрительные сообщения на сильную. Для каждой модели есть цепочка резервных. |
| 2 | Промпт | Правила ответа, границы тем и формат JSON лежат в `prompts/`, а не в коде. Сообщение клиента передаётся как данные, а не как инструкция. |
| 3 | RAG | Факты берутся только из базы знаний (`kb/policy.json`). Если ничего не найдено, ответ: «нет информации». |
| 4 | MCP-инструменты | Статус заказа и допустимость возврата считает код. Ответ инструмента считается недоверенными данными и проверяется на инъекции. Манифест инструментов защищён хэшами. |
| 5 | Доступ | Ограничение частоты запросов, пауза для нарушителей, дневной бюджет токенов. |
| 6 | Защита | Фильтры на входе и выходе: проверка чисел, процентов, обещаний скидок и исключений, утечки промпта. Ответ агента дополнительно проверяет модель другого семейства. |
| 7 | Кэш и наблюдаемость | Кэш точных совпадений в SQLite (версия правил входит в ключ), трассировка каждого ответа в JSONL, команды `/debug` и `/stats`. |

## Как это работает

1. Сообщение проходит проверку на подозрительные конструкции и ограничения доступа.
2. Роутер определяет темы и выбирает маршрут: модель, инструменты, нужные правила.
3. RAG достаёт из базы знаний только подходящие правила.
4. Агент отвечает в формате JSON. При необходимости он вызывает инструменты MCP.
5. Ответ проверяется кодом, затем верификатором другой модели. При нарушении запрашивается новая попытка (до трёх), результаты инструментов сохраняются.
6. При таймауте или ошибке модели бот пробует до четырёх резервных моделей и только потом отправляет шаблонный ответ.

## Быстрый старт

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # впишите ключ API и токен бота
python cli.py "ваш вопрос"        # проверка в консоли
python telegram_bot.py            # запуск бота
```

Тесты:

```bash
pytest -q
```

Проверочный прогон на реальных моделях (тратит токены):

```bash
python tests/run_evals.py
```

## Как адаптировать под свою задачу

1. Замените `kb/policy.json` своей базой знаний.
2. Поправьте промпты в `prompts/`.
3. Добавьте свои инструменты на MCP-сервере (`mcp_server/`) и пропишите их в маршрутах.
4. Опишите проверочные вопросы, включая атаки, в `tests/cases.yaml` и запустите прогон.

Модели, маршруты, лимиты и таймауты настраиваются в `config/routes.yaml`. Менять провайдера или модель можно без правок кода.

## Масштабирование

- Новые модели и маршруты добавляются в конфиге.
- Новые инструменты подключаются через MCP, не затрагивая остальной код.
- Правила версионируются. При изменении базы знаний старые ответы из кэша не используются.
- Набор тестов и проверочных вопросов растёт вместе с проектом.

Сейчас бот рассчитан на один экземпляр с долгим опросом Telegram, а кэш хранится в SQLite. Для большой нагрузки следующие шаги такие: вынести кэш и учёт лимитов в Redis, перейти на вебхуки и запускать несколько экземпляров.

## Ограничения

- Бот не заменяет оператора. Спорные случаи, брак и вопросы вне базы знаний передаются человеку.
- Качество ответов зависит от выбранных моделей и прокси. Верификатор снижает риск ошибок, но не гарантирует их полное отсутствие.
- Демонстрационные заказы в `mcp_server/data/` вымышленные.
