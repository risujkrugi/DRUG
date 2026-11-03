import asyncio
import json

import pytest

from bot import mcp_client
from bot.mcp_client import ShopTools, ToolManifestError
from mcp_server import logic


# --- get_order -------------------------------------------------------------

def test_order_1842_not_found():
    assert logic.get_order("1842") == {"source": "orders_demo", "order_id": "1842", "found": False}


def test_order_found_without_pii():
    r = logic.get_order("1001")
    assert r["found"] is True and r["status"] == "передан курьеру"
    assert set(r) == {"source", "order_id", "found", "status", "delivery_slot"}


def test_order_comment_with_injection_is_never_returned():
    r = logic.get_order("1002")
    assert "comment" not in r
    assert "скидк" not in json.dumps(r, ensure_ascii=False)


@pytest.mark.parametrize("bad", ["abc", "12", "123456789", "1842; drop", "", None, 1842])
def test_order_invalid_id(bad):
    assert logic.get_order(bad)["error"] == "invalid_order_id"


# --- check_return ----------------------------------------------------------

@pytest.mark.parametrize("days,opened,defect,eligible,rules,need_info", [
    (16, True, None, False, ["P3", "P4"], "уточнить, есть ли брак"),   # вопрос 2 из ТЗ
    (16, True, False, False, ["P3", "P4"], None),
    (14, True, False, True, ["P3", "P4"], None),                       # граница включительно
    (14, False, None, True, ["P3"], None),
    (15, False, False, False, ["P3"], None),
    (0, False, None, True, ["P3"], None),
    (20, True, True, "expertise", ["P5"], None),                       # брак — экспертиза при любом сроке
    (3, False, True, "expertise", ["P5"], None),
    (None, True, None, "need_info", ["P3"], "сколько дней прошло"),
])
def test_check_return_table(days, opened, defect, eligible, rules, need_info):
    r = logic.check_return(days, opened, defect)
    assert r["eligible"] == eligible
    assert r["rule_ids"] == rules
    assert r.get("need_info") == need_info


def test_defect_never_promises_money():
    reason = logic.check_return(20, True, True)["reason"]
    assert "не обещаем" in reason and "оператор" in reason


# --- MCP по stdio ----------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


def test_mcp_roundtrip_and_allowlist():
    async def go():
        async with ShopTools() as t:
            assert set(t.tools) == {"get_order", "check_return"}
            found = await t.call("get_order", {"order_id": "1842"}, ["get_order"])
            denied = await t.call("check_return", {"days": 1, "opened": False, "defect": None}, ["get_order"])
            invalid = await t.call("get_order", {"order_id": "abc"}, ["get_order"])
            ret = await t.call("check_return", {"days": 16, "opened": True, "defect": None}, ["check_return"])
            return found, denied, invalid, ret
    found, denied, invalid, ret = run(go())
    assert found["found"] is False
    assert denied["error"] == "tool_not_allowed"
    assert invalid["error"] == "tool_error"
    assert ret["eligible"] is False


def test_tool_specs_only_allowed():
    async def go():
        async with ShopTools() as t:
            return t.specs(["get_order"], "openai"), t.specs([], "anthropic")
    oa, an = run(go())
    assert [s["function"]["name"] for s in oa] == ["get_order"]
    assert an == []


def test_tampered_manifest_is_rejected(tmp_path, monkeypatch):
    fake = tmp_path / "hashes.json"
    fake.write_text(json.dumps({"get_order": "0" * 64, "check_return": "0" * 64}))
    monkeypatch.setattr(mcp_client, "HASHES", fake)

    async def go():
        async with ShopTools():
            pass
    with pytest.raises(ToolManifestError):
        run(go())
