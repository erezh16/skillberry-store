"""Tool provider ``rossoctl``: rossoctl-hosted MCP tools, reached through its backend.

The ``endpoint`` block it owns (``packaging_params.endpoint``)::

    {"provider": "rossoctl", "tool": "weather-tool", "namespace": "team1",
     "port": 8000, "path": "/mcp", "gateway_prefix": "weather_"}

Store-side execution goes through rossoctl's ``/invoke`` (rossoctl makes the
call, under its authorization). The exported proxy, which runs in an agent's
pod, resolves the tool's in-cluster URL itself (``proxy_resolution``).
"""

import json
import re
from typing import Any, Dict, List

from skillberry_store.modules.tool_providers import CallResult, ProviderContext

from .backend import RossoctlBackend, validate_label

PROVIDER_NAME = "rossoctl"
SA_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


def env_token(name: str) -> str:
    """The ``<T>`` in ``ROSSOCTL_TOOL_<T>_URL``; rossoctl uses the same rule."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def validate_endpoint(endpoint: Dict[str, Any]) -> None:
    validate_label(endpoint.get("tool"), "endpoint.tool")
    validate_label(endpoint.get("namespace"), "endpoint.namespace")
    port = endpoint.get("port", 8000)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ValueError(f"endpoint.port must be an integer in 1..65535 (got {port!r})")
    path = endpoint.get("path", "/mcp")
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError(f"endpoint.path must start with '/' (got {path!r})")
    prefix = endpoint.get("gateway_prefix")
    if prefix is not None and (not isinstance(prefix, str) or not re.match(r"^[A-Za-z0-9_.\-]{0,64}$", prefix)):
        raise ValueError(f"endpoint.gateway_prefix is invalid (got {prefix!r})")


def proxy_steps(packaging_params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolution steps for an agent-side proxy of a rossoctl tool."""
    ep = packaging_params["endpoint"]
    token = env_token(ep["tool"])
    steps: List[Dict[str, Any]] = [{"kind": "env", "var": f"ROSSOCTL_TOOL_{token}_URL"}]
    if ep.get("gateway_prefix"):
        steps.append(
            {
                "kind": "template",
                "template": "{gw}",
                "vars": {"gw": {"env": "ROSSOCTL_MCP_GATEWAY_URL"}},
                "tool_name": f"{ep['gateway_prefix']}{packaging_params['mcp_tool_name']}",
            }
        )
    steps.append(
        {
            "kind": "template",
            "template": "http://{tool}-mcp.{ns}.svc.cluster.local:{port}{path}",
            "vars": {
                "tool": {"default": ep["tool"]},
                "ns": {
                    "env": "ROSSOCTL_NAMESPACE",
                    "file": SA_NAMESPACE_FILE,
                    "default": ep["namespace"],
                },
                "port": {"env": f"ROSSOCTL_TOOL_{token}_PORT", "default": str(ep.get("port", 8000))},
                "path": {"default": ep.get("path", "/mcp")},
            },
        }
    )
    for step in steps:
        step["timeout_env"] = "ROSSOCTL_TOOL_TIMEOUT"
    return steps


def result_text(result: Dict[str, Any]) -> str:
    """Join the text of an ``/invoke`` result; ``data`` items are JSON-encoded."""
    parts = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "data":
            data = item.get("data")
            parts.append(data if isinstance(data, str) else json.dumps(data))
    return "\n".join(parts)


class RossoctlProvider:
    name = PROVIDER_NAME

    def __init__(self, backend: RossoctlBackend):
        self.backend = backend

    def validate(self, endpoint: Dict[str, Any]) -> None:
        validate_endpoint(endpoint)

    async def list_tools(self, endpoint: Dict[str, Any], ctx: ProviderContext) -> List[Dict[str, Any]]:
        tools = await self.backend.connect(endpoint["namespace"], endpoint["tool"], bearer=ctx.bearer)
        return [
            {
                "name": t.get("name"),
                "description": t.get("description") or "",
                "input_schema": t.get("input_schema") or {"type": "object"},
                "annotations": t.get("annotations") or {},
            }
            for t in tools
        ]

    async def call_tool(
        self, packaging_params: Dict[str, Any], arguments: Dict[str, Any], ctx: ProviderContext
    ) -> CallResult:
        ep = packaging_params["endpoint"]
        result = await self.backend.invoke(
            ep["namespace"], ep["tool"], packaging_params["mcp_tool_name"], arguments, bearer=ctx.bearer
        )
        return CallResult(text=result_text(result), is_error=bool(result.get("isError")))

    def proxy_resolution(self, packaging_params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return proxy_steps(packaging_params)
