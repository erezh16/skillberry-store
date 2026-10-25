"""Provider-backed tools: a plugin owns how a remote MCP tool is reached.

An ``mcp`` tool normally carries a fixed ``packaging_params.mcp_url`` that the
store connects to itself (SSE). A tool may instead carry an opaque
``packaging_params.endpoint`` block whose ``provider`` names a *tool provider*
registered by a plugin. The provider then lists and executes the tool, and
supplies the endpoint-resolution steps for the runnable proxy CLI that skill
export generates (``tools/anthropic/mcp_proxy.py``).

Core defines the mechanism and ships no provider. The registry follows
``plugins/events.py``: module-level state plus a resolver the plugin loader
injects, so a provider whose owning plugin is inactive is invisible. Core
never interprets an ``endpoint`` beyond the generic shape check in
:func:`validate_mcp_packaging`.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol

from skillberry_store.services.exceptions import ToolProviderUnavailable

logger = logging.getLogger(__name__)

MCP_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ENDPOINT_MAX_BYTES = 4096


@dataclass
class CallResult:
    """The outcome of one provider ``call_tool``."""

    text: str  # joined text content
    is_error: bool = False
    structured: Optional[Any] = None


@dataclass
class ProviderContext:
    """Per-request context handed to a provider."""

    env_id: Optional[str] = None  # from the Skillberry-Context header
    bearer: Optional[str] = None  # caller's raw bearer, when present


class ToolProvider(Protocol):
    """What a plugin implements to back ``endpoint`` tools."""

    name: str  # matches endpoint.provider

    def validate(self, endpoint: Dict[str, Any]) -> None:
        """Raise ``ValueError`` if the endpoint block is not acceptable."""

    async def list_tools(
        self, endpoint: Dict[str, Any], ctx: ProviderContext
    ) -> List[Dict[str, Any]]:
        """``[{name, description, input_schema, annotations?}]`` at the endpoint."""

    async def call_tool(
        self,
        packaging_params: Dict[str, Any],
        arguments: Dict[str, Any],
        ctx: ProviderContext,
    ) -> CallResult:
        """Call the tool and return its result."""

    def proxy_resolution(
        self, packaging_params: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Ordered endpoint-resolution steps for the exported proxy CLI."""


# name -> provider, and name -> owning plugin slug
_providers: Dict[str, ToolProvider] = {}
_owner_by_name: Dict[str, str] = {}

# Injected by the loader: slug -> bool (is the plugin active?). When None,
# every registered provider is visible.
_active_resolver: Optional[Callable[[str], bool]] = None


def register_provider(provider: ToolProvider, owner_slug: str) -> None:
    """Register ``provider`` on behalf of the plugin ``owner_slug``."""
    name = getattr(provider, "name", None)
    if not isinstance(name, str) or not PROVIDER_NAME_RE.match(name):
        raise ValueError(f"Invalid tool provider name: {name!r}")
    previous = _owner_by_name.get(name)
    if previous is not None and previous != owner_slug:
        # Two plugins may not claim the same provider name; the same plugin
        # registering again (a new loader instance) replaces its provider.
        raise ValueError(
            f"Tool provider '{name}' is already registered by plugin '{previous}'"
        )
    _providers[name] = provider
    _owner_by_name[name] = owner_slug
    logger.info(f"Registered tool provider '{name}' (plugin '{owner_slug}')")


def unregister_provider(name: str) -> None:
    """Remove a provider (used by tests)."""
    _providers.pop(name, None)
    _owner_by_name.pop(name, None)


def set_active_resolver(resolver: Optional[Callable[[str], bool]]) -> None:
    """Inject the loader's 'is this plugin active?' resolver (or None to clear)."""
    global _active_resolver
    _active_resolver = resolver


def get_provider(name: Optional[str]) -> Optional[ToolProvider]:
    """The provider named ``name``, or None if unknown or its owner is inactive."""
    if not name:
        return None
    provider = _providers.get(name)
    if provider is None:
        return None
    if _active_resolver is not None:
        try:
            if not _active_resolver(_owner_by_name[name]):
                return None
        except Exception as e:
            logger.error(f"Active resolver failed for provider '{name}': {e}")
            return None
    return provider


def endpoint_of(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ``endpoint`` block of a provider-backed ``mcp`` tool, else None."""
    if tool.get("packaging_format") != "mcp":
        return None
    pp = tool.get("packaging_params")
    if not isinstance(pp, dict):
        return None
    endpoint = pp.get("endpoint")
    return endpoint if isinstance(endpoint, dict) else None


def is_provider_backed(tool: Dict[str, Any]) -> bool:
    return endpoint_of(tool) is not None


def validate_mcp_packaging(
    packaging_format: Optional[str], packaging_params: Optional[Dict[str, Any]]
) -> None:
    """Shape check for ``mcp`` packaging params. Raises ``ValueError``.

    Shared by the pydantic schema and by ``ToolsService.create``/``update``, so
    tools created through any path (including plugins) are checked. When the
    named provider is active its own ``validate`` runs as well; otherwise only
    the generic shape check applies, so a store without the provider can still
    hold, copy and export the tool.
    """
    if packaging_format != "mcp":
        return
    if not packaging_params:
        raise ValueError(
            "packaging_params is required when packaging_format is 'mcp'. "
            "It should contain 'mcp_tool_name' and one of 'mcp_url' or 'endpoint'."
        )
    name = packaging_params.get("mcp_tool_name")
    if not isinstance(name, str) or not MCP_TOOL_NAME_RE.match(name):
        raise ValueError(
            "MCP packaging requires 'mcp_tool_name' in packaging_params, matching "
            f"{MCP_TOOL_NAME_RE.pattern}"
        )
    endpoint = packaging_params.get("endpoint")
    if not packaging_params.get("mcp_url") and endpoint is None:
        raise ValueError(
            "MCP packaging requires one of 'mcp_url' or 'endpoint' in packaging_params"
        )
    if endpoint is None:
        return
    if not isinstance(endpoint, dict):
        raise ValueError("packaging_params.endpoint must be a JSON object")
    provider_name = endpoint.get("provider")
    if not isinstance(provider_name, str) or not PROVIDER_NAME_RE.match(
        provider_name
    ):
        raise ValueError(
            f"packaging_params.endpoint.provider must match {PROVIDER_NAME_RE.pattern}"
        )
    try:
        size = len(json.dumps(endpoint).encode("utf-8"))
    except (TypeError, ValueError) as e:
        raise ValueError(f"packaging_params.endpoint is not JSON-serializable: {e}")
    if size > ENDPOINT_MAX_BYTES:
        raise ValueError(
            f"packaging_params.endpoint is {size} bytes; the limit is {ENDPOINT_MAX_BYTES}"
        )
    provider = get_provider(provider_name)
    if provider is not None:
        provider.validate(endpoint)


async def execute_via_provider(
    tool: Dict[str, Any], parameters: Dict[str, Any], ctx: ProviderContext
) -> Dict[str, Any]:
    """Execute a provider-backed tool.

    Returns the same dict shape ``FileExecutor`` returns: ``{"return value": …}``
    (plus ``"structured"`` when present) or ``{"error": …}``.

    Raises:
        ToolProviderUnavailable: no active plugin provides the tool's provider.
    """
    endpoint = endpoint_of(tool) or {}
    provider_name = endpoint.get("provider")
    tool_name = tool.get("name", "")
    provider = get_provider(provider_name)
    if provider is None:
        raise ToolProviderUnavailable(provider_name, tool_name)
    try:
        result = await provider.call_tool(
            tool["packaging_params"], parameters or {}, ctx
        )
    except ToolProviderUnavailable:
        raise
    except Exception as e:
        logger.error(f"Provider '{provider_name}' failed calling '{tool_name}': {e}")
        return {"error": f"Error calling tool '{tool_name}': {e}"}
    if result.is_error:
        return {"error": result.text or f"Tool '{tool_name}' reported an error"}
    out: Dict[str, Any] = {"return value": result.text}
    if result.structured is not None:
        out["structured"] = result.structured
    return out
