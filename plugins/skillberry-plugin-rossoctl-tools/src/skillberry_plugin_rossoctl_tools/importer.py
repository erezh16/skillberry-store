"""Import claimer for ``rossoctl-tools.json``.

It claims ``rossoctl-tools.json`` and every script that file lists, and turns
each script into an ``mcp`` tool with an ``endpoint`` block rebuilt from the
JSON and the spec embedded in the script. The generated runtime itself is
discarded (it is re-generated on export), which makes "export -> edit in git
-> re-import" lossless. A listed script that is missing or is not a generated
proxy fails the import rather than silently dropping a tool.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from skillberry_store.fast_api.server_utils import mcp_content_from_manifest
from skillberry_store.tools.anthropic.contributions import ImportClaim
from skillberry_store.tools.anthropic.mcp_proxy import extract_spec

from .export import TOOLS_JSON, TOOLS_JSON_SCHEMA
from .provider import PROVIDER_NAME, validate_endpoint

logger = logging.getLogger(__name__)


def tool_data_from_spec(
    spec: Dict[str, Any], entry: Dict[str, Any], mcp_entry: Dict[str, Any], namespace: str
) -> Dict[str, Any]:
    schema = spec.get("input_schema") or {}
    endpoint: Dict[str, Any] = {
        "provider": PROVIDER_NAME,
        "tool": entry["tool"],
        "namespace": namespace,
        "port": int(entry.get("port", 8000)),
        "path": entry.get("path", "/mcp"),
    }
    if entry.get("gateway_prefix"):
        endpoint["gateway_prefix"] = entry["gateway_prefix"]
    validate_endpoint(endpoint)
    name = mcp_entry.get("name") or spec.get("name")
    store_name = mcp_entry["script"].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return {
        "name": store_name,
        "description": spec.get("description") or "",
        "packaging_format": "mcp",
        "programming_language": "python",
        "params": {
            "type": "object",
            "properties": schema.get("properties") or {},
            "required": schema.get("required") or [],
        },
        "packaging_params": {
            "mcp_tool_name": name,
            "endpoint": endpoint,
            "input_schema_sha256": mcp_entry.get("schema_sha256") or spec.get("input_schema_sha256") or "",
        },
        "tags": ["mcp", f"rossoctl-tool:{entry['tool']}"],
        "extra": {"mcp_annotations": {"readOnlyHint": True} if mcp_entry.get("read_only") else {}},
    }


def namespace_from_spec(spec: Dict[str, Any], default: str) -> str:
    """The import namespace recorded in a proxy's cluster-DNS step, if any."""
    for step in spec.get("steps") or []:
        ns = ((step.get("vars") or {}).get("ns") or {}).get("default")
        if isinstance(ns, str) and ns:
            return ns
    return default


class RossoctlImportClaimer:
    def __init__(self, default_namespace: str = "default"):
        self.default_namespace = default_namespace

    def claim(self, files: Dict[str, str]) -> Optional[ImportClaim]:
        if TOOLS_JSON not in files:
            return None
        try:
            doc = json.loads(files[TOOLS_JSON])
        except ValueError as e:
            raise ValueError(f"{TOOLS_JSON} is not valid JSON: {e}")
        if not isinstance(doc, dict) or doc.get("schema") != TOOLS_JSON_SCHEMA:
            raise ValueError(f"{TOOLS_JSON} must have schema '{TOOLS_JSON_SCHEMA}'")
        claimed = {TOOLS_JSON}
        tools: List[Any] = []
        for entry in doc.get("tools") or []:
            for mcp_entry in entry.get("mcp_tools") or []:
                script = mcp_entry.get("script")
                if not script or script not in files:
                    raise ValueError(f"{TOOLS_JSON} lists '{script}', which is not in the package")
                spec = extract_spec(files[script])
                if spec is None:
                    raise ValueError(f"'{script}' is not a generated MCP proxy script")
                namespace = namespace_from_spec(spec, self.default_namespace)
                data = tool_data_from_spec(spec, entry, mcp_entry, namespace)
                tools.append((data, mcp_content_from_manifest(data), f"{data['name']}.py"))
                claimed.add(script)
        logger.info(f"Claimed {TOOLS_JSON} with {len(tools)} rossoctl tool proxies")
        return ImportClaim(claimed_paths=claimed, tools=tools)
