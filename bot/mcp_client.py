"""MCP-клиент: запускает сервер магазина по stdio, сверяет описания инструментов с эталоном,
отдаёт модели только разрешённые инструменты и исполняет вызовы с таймаутом."""
import asyncio
import hashlib
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp_server" / "server.py"
HASHES = ROOT / "mcp_server" / "tool_hashes.json"


class ToolManifestError(RuntimeError):
    """Описание инструмента не совпало с эталоном (tool poisoning / rug pull)."""


def tool_hash(tool: dict) -> str:
    payload = json.dumps({k: tool[k] for k in ("name", "description", "input_schema")},
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def to_openai(tool: dict) -> dict:
    return {"type": "function", "function": {
        "name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]}}


def to_anthropic(tool: dict) -> dict:
    return {"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]}


class ShopTools:
    """Список инструментов фиксируется при старте и больше не перезапрашивается."""

    def __init__(self, timeout_s: float = 5, verify: bool = True):
        self.timeout_s = timeout_s
        self.verify = verify
        self.tools: dict[str, dict] = {}
        self._stack = AsyncExitStack()
        self._session: ClientSession | None = None

    async def __aenter__(self):
        params = StdioServerParameters(command=sys.executable, args=[str(SERVER)])
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        listed = await self._session.list_tools()
        self.tools = {t.name: {"name": t.name, "description": t.description or "",
                               "input_schema": t.inputSchema} for t in listed.tools}
        if self.verify:
            self._verify_manifest()
        return self

    async def __aexit__(self, *exc):
        await self._stack.aclose()

    def manifest(self) -> dict[str, str]:
        return {name: tool_hash(t) for name, t in sorted(self.tools.items())}

    def _verify_manifest(self):
        expected = json.loads(HASHES.read_text(encoding="utf-8"))
        actual = self.manifest()
        if actual != expected:
            raise ToolManifestError(f"tool manifest mismatch: expected {expected}, got {actual}")

    def specs(self, allowed: list[str], fmt: str) -> list[dict]:
        convert = to_openai if fmt == "openai" else to_anthropic
        return [convert(self.tools[name]) for name in allowed if name in self.tools]

    async def call(self, name: str, args: dict, allowed: list[str]) -> dict:
        if name not in allowed or name not in self.tools:
            return {"error": "tool_not_allowed", "tool": name}
        try:
            result = await asyncio.wait_for(self._session.call_tool(name, args), self.timeout_s)
        except asyncio.TimeoutError:
            return {"error": "tool_timeout", "tool": name}
        if result.isError:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"error": "tool_error", "tool": name, "detail": text[:200]}
        if result.structuredContent is not None:
            data = result.structuredContent
            return data.get("result", data) if isinstance(data, dict) and set(data) == {"result"} else data
        text = "".join(getattr(c, "text", "") for c in result.content)
        return json.loads(text)
