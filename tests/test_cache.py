from bot import cache, config

REPLY = {"answer": "Скидок нет ни при каких условиях.", "rule_ids": ["P6"],
         "needs_operator": False, "clarifying_question": False}


def test_key_ignores_case_punctuation_and_spaces():
    assert cache.make_key("Есть  промокод?!", "fast") == cache.make_key("есть промокод", "fast")


def test_key_depends_on_policy_version(monkeypatch):
    k1 = cache.make_key("есть промокод", "fast")
    monkeypatch.setitem(config.policy(), "version", 2)
    assert cache.make_key("есть промокод", "fast") != k1


def test_key_depends_on_route():
    assert cache.make_key("есть промокод", "fast") != cache.make_key("есть промокод", "strong")


def test_put_get_and_ttl():
    now = [1000.0]
    c = cache.ResponseCache(ttl_s=60, clock=lambda: now[0])
    c.put("k", REPLY)
    assert c.get("k") == REPLY
    now[0] += 61
    assert c.get("k") is None


def test_what_is_not_cached():
    ok = dict(REPLY)
    assert cache.is_cacheable("есть промокод?", ["discount"], False, ok, [])
    assert not cache.is_cacheable("доставка заказа 1842", ["order_status"], False, ok, [])
    assert not cache.is_cacheable("есть промокод?", ["discount"], True, ok, [])          # инъекция
    assert not cache.is_cacheable("вернуть на 16-й день", ["return"], False, ok, [])     # число не из регламента
    assert cache.is_cacheable("вернуть на 16-й день", ["return"], False, ok, ["check_return"])  # детерминированный инструмент
    assert cache.is_cacheable("вернуть на 16-й день", ["return"], False, {**ok, "clarifying_question": True}, ["check_return"])
    assert not cache.is_cacheable("статус 1001", ["order_status"], False, ok, ["get_order"])    # статус меняется
    assert not cache.is_cacheable("вернуть", ["return"], False, ok, ["check_return", "get_order"])
    assert not cache.is_cacheable("наушники сломались", ["defect"], False, {**ok, "needs_operator": True}, ["check_return"])
    assert not cache.is_cacheable("белые есть?", ["product"], False, {**ok, "needs_operator": True}, [])
    assert not cache.is_cacheable("вернуть", ["return"], False, {**ok, "clarifying_question": True}, [])


def test_broken_db_is_a_miss_not_a_crash():
    c = cache.ResponseCache()
    c.db.close()  # любая ошибка SQLite
    assert c.get("k") is None
    c.put("k", REPLY)  # не бросает исключение
