"""Сборка ассистента из .env: провайдеры, MCP-сервер, кэш, доступ."""
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

from bot.access import Access
from bot.agent import Assistant
from bot.cache import ResponseCache
from bot.config import ROOT
from bot.mcp_client import ShopTools
from bot.providers import Providers


@asynccontextmanager
async def build_assistant(cache_path=None, trace_path=None):
    load_dotenv(ROOT / ".env")
    admins = {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}
    access = Access(admins, public_debug=os.getenv("PUBLIC_DEBUG") == "1",
                    daily_tokens=int(os.getenv("DAILY_BUDGET_TOKENS", "2000000")))
    if cache_path is None:
        (ROOT / "logs").mkdir(exist_ok=True)
        cache_path = ROOT / "logs" / "cache.sqlite"
    async with ShopTools(timeout_s=5) as tools:
        yield Assistant(Providers(), tools, ResponseCache(cache_path), access, trace_path=trace_path)
