"""Client for the MCP server that Loxone Config opens from its title bar."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .settings import Settings

_READ_PREFIX = re.compile(
    r"^(get|list|read|search|find|describe|query|show|inspect|explain|help|status|check|validate|lookup|fetch)[_\-.A-Z]",
    re.I,
)
_SAVE = re.compile(
    r"(save|upload|transfer|deploy|send|write|übertrag|speicher).{0,40}(miniserver)|"
    r"(miniserver).{0,40}(save|upload|transfer|deploy|speicher)",
    re.I,
)
_GEMINI_NAME = re.compile(r"[^a-zA-Z0-9_.\-]")


@dataclass
class McpTool:
    name: str
    gemini_name: str
    description: str
    schema: dict
    read_only: bool

    @property
    def saves_to_miniserver(self) -> bool:
        return bool(_SAVE.search(f"{self.name} {self.description}"))


def _dump(obj: Any) -> dict:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_none=True)
    return dict(obj or {})


def _clean_schema(schema: dict) -> dict:
    schema = {k: v for k, v in (schema or {}).items() if k not in ("$schema", "$id")}
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


class ConfigMcp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._tools: list[McpTool] = []
        self._tools_at = 0.0
        self.last_error: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.settings.get("mcp.url"))

    def _client(self):
        from mcp import Client

        return Client(self.settings.get("mcp.url"), read_timeout_seconds=90)

    async def tools(self, refresh: bool = False) -> list[McpTool]:
        if not self.configured:
            return []
        if self._tools and not refresh and time.monotonic() - self._tools_at < 300:
            return self._tools
        try:
            async with self._client() as client:
                result = _dump(await client.list_tools())
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"[:200]
            raise
        tools = []
        used: set[str] = set()
        for raw in result.get("tools", []):
            name = str(raw.get("name", ""))
            gemini_name = _GEMINI_NAME.sub("_", name)[:60] or "tool"
            while gemini_name in used:
                gemini_name += "_"
            used.add(gemini_name)
            annotations = raw.get("annotations") or {}
            read_only = annotations.get("readOnlyHint")
            if read_only is None:
                read_only = bool(_READ_PREFIX.match(name))
            tools.append(
                McpTool(
                    name=name,
                    gemini_name=gemini_name,
                    description=str(raw.get("description") or raw.get("title") or name)[:1000],
                    schema=_clean_schema(raw.get("inputSchema") or {}),
                    read_only=bool(read_only),
                )
            )
        self._tools = tools
        self._tools_at = time.monotonic()
        self.last_error = None
        return tools

    async def call(self, name: str, arguments: dict) -> dict:
        async with self._client() as client:
            result = _dump(await client.call_tool(name, arguments or {}))
        texts = []
        for block in result.get("content", []):
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            else:
                texts.append(f"[{block.get('type')}]")
        payload: dict[str, Any] = {"content": "\n".join(texts)[:8000]}
        if result.get("structuredContent") is not None:
            payload["structured"] = result["structuredContent"]
        if result.get("isError"):
            payload["error"] = True
        return payload

    async def test(self) -> dict:
        tools = await self.tools(refresh=True)
        save = next((t.name for t in tools if t.saves_to_miniserver), None)
        return {
            "ok": True,
            "tools": len(tools),
            "read_only": sum(1 for t in tools if t.read_only),
            "save_tool": save,
        }
