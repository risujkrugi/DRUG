"""MCP-сервер магазина (stdio). Только чтение: никаких действий с заказами, ценами и деньгами."""
import sys
from pathlib import Path
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mcp_server import logic  # noqa: E402

mcp = FastMCP("shop", log_level="WARNING")


@mcp.tool()
def get_order(
    order_id: Annotated[str, Field(pattern=r"^\d{3,8}$", description="Номер заказа, только цифры")],
) -> dict:
    """Статус заказа в демо-базе магазина (заглушка вместо CRM).
    Возвращает JSON: found=false, если заказа нет; иначе status и delivery_slot.
    Персональные данные не возвращаются."""
    return logic.get_order(order_id)


@mcp.tool()
def check_return(
    days: Annotated[int | None, Field(ge=0, le=365, description="Сколько дней прошло; null, если неизвестно")],
    opened: Annotated[bool, Field(description="Вскрыт ли товар")],
    defect: Annotated[bool | None, Field(description="Есть ли брак; null, если клиент не сказал")],
) -> dict:
    """Проверяет по регламенту, можно ли вернуть товар. Результат окончательный."""
    return logic.check_return(days, opened, defect)


if __name__ == "__main__":
    mcp.run()
