"""What rossoctl-backed tools add to an exported skill package.

* frontmatter ``metadata.rossoctl-requires-tools: "reservation-tool,weather-tool"``
* ``rossoctl-tools.json`` (``skillberry.ai/rossoctl-tools/v1``)
* a "Tools (rossoctl)" note under core's Tools section

plus the opt-in computed skill field ``_rossoctl_required_tools``.
"""

import json
from typing import Any, Dict, List, Optional

from skillberry_store.tools.anthropic.contributions import ExportContribution
from skillberry_store.tools.anthropic.exporter import tool_script_paths

from .provider import PROVIDER_NAME, env_token

TOOLS_JSON = "rossoctl-tools.json"
TOOLS_JSON_SCHEMA = "skillberry.ai/rossoctl-tools/v1"
REQUIRES_METADATA_KEY = "rossoctl-requires-tools"
COMPUTED_FIELD = "_rossoctl_required_tools"


def rossoctl_endpoint(tool: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if tool.get("packaging_format") != "mcp":
        return None
    endpoint = (tool.get("packaging_params") or {}).get("endpoint")
    if isinstance(endpoint, dict) and endpoint.get("provider") == PROVIDER_NAME:
        return endpoint
    return None


def required_tools(tools: List[Dict[str, Any]]) -> List[str]:
    """Sorted, de-duplicated rossoctl tool names behind ``tools``."""
    return sorted({ep["tool"] for ep in map(rossoctl_endpoint, tools) if ep and ep.get("tool")})


def compute_required_tools(skill: Dict[str, Any], tools: List[Dict[str, Any]]) -> List[str]:
    """The ``_rossoctl_required_tools`` computed skill field."""
    return required_tools(tools)


def build_tools_json(skill_name: str, tools: List[Dict[str, Any]]) -> Dict[str, Any]:
    scripts = tool_script_paths(tools, skill_name)
    by_tool: Dict[str, Dict[str, Any]] = {}
    for tool in tools:
        ep = rossoctl_endpoint(tool)
        if not ep:
            continue
        entry = by_tool.setdefault(
            ep["tool"],
            {"tool": ep["tool"], "port": ep.get("port", 8000), "path": ep.get("path", "/mcp"), "mcp_tools": []},
        )
        if ep.get("gateway_prefix"):
            entry["gateway_prefix"] = ep["gateway_prefix"]
        pp = tool.get("packaging_params") or {}
        annotations = (tool.get("extra") or {}).get("mcp_annotations") or {}
        entry["mcp_tools"].append(
            {
                "name": pp.get("mcp_tool_name") or tool["name"],
                "script": scripts[tool["name"]],
                "schema_sha256": pp.get("input_schema_sha256") or "",
                "read_only": annotations.get("readOnlyHint") is True,
            }
        )
    out = []
    for name in sorted(by_tool):
        entry = by_tool[name]
        entry["mcp_tools"].sort(key=lambda t: t["name"])
        out.append(entry)
    return {"schema": TOOLS_JSON_SCHEMA, "tools": out}


def skill_md_note(tools: List[str]) -> str:
    names = ", ".join(f"`{t}`" for t in tools)
    first = env_token(tools[0]) if tools else "TOOL"
    return (
        "### Tools (rossoctl)\n\n"
        f"These scripts call the rossoctl tools {names}. They are resolved in the "
        "namespace this agent runs in, so the skill works in any namespace that runs "
        "them. Exit code `3` means the tool is not deployed (or not reachable) in "
        "this namespace: say which tool, and do not make up a result. An endpoint "
        f"can be overridden with `ROSSOCTL_TOOL_<TOOL>_URL` (for example "
        f"`ROSSOCTL_TOOL_{first}_URL`)."
    )


class RossoctlExportContributor:
    """Adds the rossoctl dependency declaration to skills that need it."""

    def contribute(
        self,
        skill: Dict[str, Any],
        tools: List[Dict[str, Any]],
        snippets: List[Dict[str, Any]],
    ) -> Optional[ExportContribution]:
        required = required_tools(tools)
        if not required:
            return None
        rossoctl_tools = [t for t in tools if rossoctl_endpoint(t)]
        doc = build_tools_json(skill["name"], rossoctl_tools)
        return ExportContribution(
            files={TOOLS_JSON: json.dumps(doc, indent=2, sort_keys=False) + "\n"},
            metadata={REQUIRES_METADATA_KEY: ",".join(required)},
            skill_md_section=skill_md_note(required),
        )
