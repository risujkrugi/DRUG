"""Детерминированная логика инструментов. Без LLM: сроки и статусы считает код."""
import json
import re
from pathlib import Path

ORDERS_PATH = Path(__file__).parent / "data" / "orders_demo.json"
ORDER_ID_RE = re.compile(r"^\d{3,8}$")
RETURN_DAYS = 14
# Поля заказа, которые разрешено отдавать модели. Имя, телефон и комментарий не уходят никогда.
ORDER_PUBLIC_FIELDS = ("status", "delivery_slot")


def load_orders(path: Path = ORDERS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["orders"]


def get_order(order_id: str, orders: dict | None = None) -> dict:
    if not isinstance(order_id, str) or not ORDER_ID_RE.fullmatch(order_id):
        return {"source": "orders_demo", "error": "invalid_order_id"}
    orders = load_orders() if orders is None else orders
    order = orders.get(order_id)
    if order is None:
        return {"source": "orders_demo", "order_id": order_id, "found": False}
    public = {k: order[k] for k in ORDER_PUBLIC_FIELDS if k in order}
    return {"source": "orders_demo", "order_id": order_id, "found": True, **public}


def check_return(days: int | None, opened: bool, defect: bool | None) -> dict:
    if defect:
        return {
            "eligible": "expertise",
            "rule_ids": ["P5"],
            "reason": "брак принимается на экспертизу, деньги сразу не обещаем; "
                      "срок приёма брака в регламенте не указан — нужен оператор",
        }
    if days is None:
        return {"eligible": "need_info", "rule_ids": ["P3"], "need_info": "сколько дней прошло"}
    if days < 0:
        return {"eligible": "need_info", "rule_ids": ["P3"], "need_info": "некорректное число дней"}

    rule_ids = ["P3", "P4"] if opened else ["P3"]
    if days <= RETURN_DAYS:
        return {"eligible": True, "rule_ids": rule_ids,
                "reason": f"возврат возможен в течение {RETURN_DAYS} дней"}
    result = {"eligible": False, "rule_ids": rule_ids,
              "reason": f"возврат без брака возможен только в течение {RETURN_DAYS} дней"}
    if defect is None:
        result["need_info"] = "уточнить, есть ли брак"
    return result
