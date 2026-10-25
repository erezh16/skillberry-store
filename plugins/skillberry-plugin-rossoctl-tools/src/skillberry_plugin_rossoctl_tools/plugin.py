"""Skillberry Plugin rossoctl Tools: skills that use rossoctl tools.

rossoctl hosts every rossoctl tool; the store only holds proxies of them and
reaches rossoctl only through its backend REST API (``ROSSOCTL_BACKEND_URL``).
This plugin registers, through core's rossoctl-agnostic extension points:

* tool provider ``rossoctl`` (store-side execution via ``/invoke``, and the
  resolution steps for exported proxy scripts);
* an export contributor (``rossoctl-tools.json``, frontmatter metadata, a
  SKILL.md note) and the matching import claimer;
* the opt-in computed skill field ``_rossoctl_required_tools``;
* routes under ``/plugins/rossoctl-tools``: ``tools``, ``import``, ``check``,
  ``resync``.

It makes no network calls until a route is invoked or a rossoctl-backed tool
is executed.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from skillberry_store.fast_api.server_utils import mcp_content_from_manifest
from skillberry_store.plugins.base import (
    PluginBase,
    PluginMetadata,
    PluginType,
    requires,
)
from skillberry_store.tools.anthropic.mcp_proxy import canonical_schema_sha256

from .backend import (
    RossoctlBackend,
    RossoctlError,
    RossoctlNotFound,
    service_port,
    validate_label,
)
from .export import (
    COMPUTED_FIELD,
    RossoctlExportContributor,
    compute_required_tools,
    rossoctl_endpoint,
)
from .importer import RossoctlImportClaimer
from .provider import PROVIDER_NAME, RossoctlProvider

logger = logging.getLogger(__name__)

SLUG = "rossoctl-tools"


def _bearer(request) -> Optional[str]:
    """The caller's bearer, forwarded to rossoctl only in delegated mode.

    In ``disabled`` store ACL mode the plugin authenticates as itself (the
    client-credentials service account), so a caller's header is ignored.
    """
    if os.getenv("ROSSOCTL_FORWARD_CALLER_TOKEN", "").lower() != "true":
        return None
    auth = request.headers.get("authorization") or ""
    return auth[7:].strip() if auth.lower().startswith("bearer ") else None


def params_from_schema(input_schema: Dict[str, Any]) -> Dict[str, Any]:
    schema = input_schema or {}
    return {
        "type": "object",
        "properties": dict(schema.get("properties") or {}),
        "required": list(schema.get("required") or []),
    }


class SkillberryPluginRossoctlTools(PluginBase):
    """Use rossoctl tools in skills, through rossoctl's backend."""

    def __init__(self, backend: Optional[RossoctlBackend] = None):
        super().__init__()
        self._metadata = PluginMetadata(
            name="rossoctl Tools",
            version="0.1.0",
            description="Use rossoctl tools in skills, through rossoctl's backend",
            plugin_type=PluginType.IMPORTER,
        )
        self.backend = backend or RossoctlBackend()
        self.provider = RossoctlProvider(self.backend)
        self.register_tool_provider(self.provider)
        self.register_export_contributor(RossoctlExportContributor())
        self.register_import_claimer(
            RossoctlImportClaimer(default_namespace=os.getenv("ROSSOCTL_DEFAULT_NAMESPACE", "default"))
        )
        self.register_computed_field("skill", COMPUTED_FIELD, compute_required_tools)

    @property
    def metadata(self) -> PluginMetadata:
        return self._metadata

    def is_enabled(self) -> bool:
        return self.backend.configured

    def get_status_message(self) -> str:
        if not self.backend.configured:
            return "Disabled: set ROSSOCTL_BACKEND_URL"
        return "Ready"

    # ---- import ---------------------------------------------------------- #

    def _existing_tool_owner(self, name: str) -> Optional[str]:
        """The rossoctl tool behind an existing store tool named ``name``."""
        for tool in self.store.list_tools():
            if tool.get("name") == name:
                ep = rossoctl_endpoint(tool)
                return ep["tool"] if ep else ""
        return None

    async def import_tools(
        self,
        namespace: str,
        tools: List[Dict[str, Any]],
        skill: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        bearer: Optional[str] = None,
    ) -> Dict[str, Any]:
        self.backend.check_namespace(namespace)
        created: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        names_this_import: Dict[str, str] = {}
        for ref in tools:
            tool_name = ref["name"]
            try:
                validate_label(tool_name, "tool name")
                detail = await self.backend.get_tool(namespace, tool_name, bearer=bearer)
                port = service_port(detail)
                mcp_tools = await self.backend.connect(namespace, tool_name, bearer=bearer)
            except RossoctlNotFound:
                errors.append({"tool": tool_name, "detail": f"tool '{tool_name}' not found in namespace '{namespace}'"})
                continue
            except (RossoctlError, ValueError) as e:
                errors.append({"tool": tool_name, "detail": str(e)})
                continue
            if not mcp_tools:
                errors.append({"tool": tool_name, "detail": "the tool's MCP server lists no tools"})
                continue
            for mcp_tool in mcp_tools:
                try:
                    created.append(
                        self._create_proxy_tool(
                            namespace, tool_name, port, ref.get("gateway_prefix"), mcp_tool, tags or [], names_this_import
                        )
                    )
                except Exception as e:
                    logger.warning(f"Failed to import {tool_name}/{mcp_tool.get('name')}: {e}")
                    errors.append({"tool": tool_name, "detail": f"{mcp_tool.get('name')}: {e}"})

        skill_uuid = None
        if skill and created:
            snippet_uuids = []
            if skill.get("instructions"):
                snippet = self.store.create_snippet(
                    {
                        "name": f"{skill['name']}-instructions",
                        "description": f"Instructions for {skill['name']}",
                        "content": skill["instructions"],
                        "tags": ["file:SKILL.md"],
                        "content_type": "text/markdown",
                    }
                )
                snippet_uuids.append(snippet["uuid"])
            skill_tags = list(dict.fromkeys(["rossoctl", *(skill.get("tags") or []), *(tags or [])]))
            result = self.store.create_skill(
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "tool_uuids": [t["uuid"] for t in created],
                    "snippet_uuids": snippet_uuids,
                    "tags": skill_tags,
                }
            )
            skill_uuid = result["uuid"]
        return {"tools": created, "skill_uuid": skill_uuid, "errors": errors}

    def _create_proxy_tool(
        self,
        namespace: str,
        tool_name: str,
        port: int,
        gateway_prefix: Optional[str],
        mcp_tool: Dict[str, Any],
        tags: List[str],
        names_this_import: Dict[str, str],
    ) -> Dict[str, Any]:
        mcp_name = mcp_tool["name"]
        input_schema = mcp_tool.get("input_schema") or {"type": "object"}
        # Prefix on collision only (§11 Q1): a store tool of the same name that
        # proxies a *different* rossoctl tool, in the store or in this batch.
        store_name = mcp_name
        owner = names_this_import.get(mcp_name)
        if owner is None:
            owner = self._existing_tool_owner(mcp_name)
        if owner is not None and owner != tool_name:
            store_name = f"{tool_name.replace('-', '_')}__{mcp_name}"
        names_this_import[store_name] = tool_name
        endpoint: Dict[str, Any] = {
            "provider": PROVIDER_NAME,
            "tool": tool_name,
            "namespace": namespace,
            "port": port,
            "path": "/mcp",
        }
        if gateway_prefix:
            endpoint["gateway_prefix"] = gateway_prefix
        data = {
            "name": store_name,
            "description": mcp_tool.get("description") or "",
            "params": params_from_schema(input_schema),
            "packaging_format": "mcp",
            "programming_language": "python",
            "tags": list(dict.fromkeys(["mcp", f"rossoctl-tool:{tool_name}", f"namespace:{namespace}", *tags])),
            "extra": {"mcp_annotations": mcp_tool.get("annotations") or {}},
            "packaging_params": {
                "mcp_tool_name": mcp_name,
                "endpoint": endpoint,
                "input_schema_sha256": canonical_schema_sha256(input_schema),
            },
        }
        result = self.store.create_tool(
            data,
            module_content=mcp_content_from_manifest(data).encode(),
            module_filename=f"{store_name}.py",
        )
        return {"uuid": result["uuid"], "name": result["name"], "rossoctl_tool": tool_name}

    # ---- check / resync -------------------------------------------------- #

    def _target_tools(self, skill: Optional[str], tool_uuids: List[str]) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        if skill:
            found = self.store.get_skill(skill)
            if found is None:
                raise RossoctlNotFound(f"skill '{skill}' not found")
            tools.extend(found.get("tools") or [])
        for uuid in tool_uuids:
            tool = self.store.get_tool(uuid)
            if tool is None:
                raise RossoctlNotFound(f"tool '{uuid}' not found")
            tools.append(tool)
        return [t for t in tools if rossoctl_endpoint(t)]

    async def check(self, skill: Optional[str], tool_uuids: List[str], bearer: Optional[str] = None) -> Dict[str, Any]:
        results = []
        live_cache: Dict[tuple, Dict[str, Any]] = {}
        for tool in self._target_tools(skill, tool_uuids):
            ep = rossoctl_endpoint(tool)
            pp = tool["packaging_params"]
            entry: Dict[str, Any] = {
                "uuid": tool["uuid"],
                "name": tool["name"],
                "rossoctl_tool": ep["tool"],
                "stored_sha256": pp.get("input_schema_sha256") or "",
            }
            key = (ep["namespace"], ep["tool"])
            try:
                if key not in live_cache:
                    live = await self.backend.connect(ep["namespace"], ep["tool"], bearer=bearer)
                    live_cache[key] = {t["name"]: t for t in live}
            except (RossoctlError, ValueError) as e:
                entry.update({"drift": None, "error": str(e)})
                results.append(entry)
                continue
            live_tool = live_cache[key].get(pp["mcp_tool_name"])
            if live_tool is None:
                entry.update({"drift": True, "live_sha256": None, "error": "the tool no longer offers this MCP tool"})
            else:
                live_sha = canonical_schema_sha256(live_tool.get("input_schema") or {"type": "object"})
                entry.update({"live_sha256": live_sha, "drift": live_sha != entry["stored_sha256"]})
                entry["_live"] = live_tool
            results.append(entry)
        return {"tools": results}

    async def resync(self, skill: Optional[str], tool_uuids: List[str], bearer: Optional[str] = None) -> Dict[str, Any]:
        report = await self.check(skill, tool_uuids, bearer=bearer)
        replaced: Dict[str, str] = {}
        new_tools = []
        for entry in report["tools"]:
            live = entry.pop("_live", None)
            if not entry.get("drift") or live is None:
                continue
            old = self.store.get_tool(entry["uuid"])
            data = {k: v for k, v in old.items() if k not in ("uuid", "created_at", "modified_at", "parent", "module_name")}
            data["description"] = live.get("description") or old.get("description") or ""
            data["params"] = params_from_schema(live.get("input_schema") or {})
            data["packaging_params"] = {
                **old["packaging_params"],
                "input_schema_sha256": canonical_schema_sha256(live.get("input_schema") or {"type": "object"}),
            }
            if live.get("annotations") is not None:
                data["extra"] = {**(old.get("extra") or {}), "mcp_annotations": live.get("annotations") or {}}
            created = self.store.create_tool(
                data,
                module_content=mcp_content_from_manifest(data).encode(),
                module_filename=f"{data['name']}.py",
            )
            replaced[entry["uuid"]] = created["uuid"]
            new_tools.append({"old_uuid": entry["uuid"], "uuid": created["uuid"], "name": created["name"]})
        new_skill_uuid = None
        if skill and replaced:
            current = self.store.get_skill(skill)
            data = {
                k: v
                for k, v in current.items()
                if k not in ("uuid", "created_at", "modified_at", "parent", "tools", "snippets")
            }
            data["tool_uuids"] = [replaced.get(u, u) for u in current.get("tool_uuids") or []]
            new_skill_uuid = self.store.create_skill(data)["uuid"]
        for entry in report["tools"]:
            entry.pop("_live", None)
        return {"tools": new_tools, "skill_uuid": new_skill_uuid, "check": report["tools"]}

    # ---- routes ------------------------------------------------------------ #

    def get_router(self):
        from fastapi import APIRouter, HTTPException, Query, Request
        from pydantic import BaseModel, Field

        router = APIRouter()

        class ImportToolRef(BaseModel):
            name: str
            gateway_prefix: Optional[str] = None

        class ImportSkill(BaseModel):
            name: str
            description: str
            instructions: Optional[str] = None
            tags: List[str] = Field(default_factory=list)

        class ImportRequest(BaseModel):
            namespace: str
            tools: List[ImportToolRef]
            skill: Optional[ImportSkill] = None
            tags: List[str] = Field(default_factory=list)

        class CheckRequest(BaseModel):
            skill: Optional[str] = None
            tool_uuids: List[str] = Field(default_factory=list)

        def http_error(e: Exception) -> HTTPException:
            if isinstance(e, ValueError):
                return HTTPException(status_code=400, detail=str(e))
            if isinstance(e, RossoctlError):
                return HTTPException(status_code=e.status_code, detail=str(e))
            return HTTPException(status_code=500, detail=str(e))

        @requires("tools", "list")
        @router.get("/tools")
        async def list_rossoctl_tools(request: Request, namespace: str = Query(...)):
            """rossoctl's tools in ``namespace`` (pass-through of its ``GET /tools``)."""
            try:
                return {"items": await self.backend.list_tools(namespace, bearer=_bearer(request))}
            except (RossoctlError, ValueError) as e:
                raise http_error(e)

        @requires("tools", "create")
        @router.post("/import")
        async def import_rossoctl_tools(request: Request, body: ImportRequest):
            """Create proxy tools (and optionally a skill) from live rossoctl tools."""
            if not body.tools:
                raise HTTPException(status_code=400, detail="tools must not be empty")
            try:
                result = await self.import_tools(
                    body.namespace,
                    [t.model_dump() for t in body.tools],
                    skill=body.skill.model_dump() if body.skill else None,
                    tags=body.tags,
                    bearer=_bearer(request),
                )
            except (RossoctlError, ValueError) as e:
                raise http_error(e)
            if not result["tools"] and result["errors"]:
                # Nothing imported: 404 when every requested tool is missing,
                # otherwise rossoctl (or the tools behind it) failed.
                all_missing = all("not found" in e["detail"] for e in result["errors"])
                raise HTTPException(status_code=404 if all_missing else 502, detail=result["errors"])
            return result

        @requires("tools", "list")
        @router.post("/check")
        async def check_rossoctl_tools(request: Request, body: CheckRequest):
            """Input-schema drift of rossoctl-backed tools against the live tools. No writes."""
            try:
                report = await self.check(body.skill, body.tool_uuids, bearer=_bearer(request))
            except (RossoctlError, ValueError) as e:
                raise http_error(e)
            for entry in report["tools"]:
                entry.pop("_live", None)
            return report

        @requires("tools", "create")
        @router.post("/resync")
        async def resync_rossoctl_tools(request: Request, body: CheckRequest):
            """A new version of each drifted tool, plus a new skill version."""
            try:
                return await self.resync(body.skill, body.tool_uuids, bearer=_bearer(request))
            except (RossoctlError, ValueError) as e:
                raise http_error(e)

        return router

    def get_cli_commands(self) -> Optional[Dict[str, Any]]:
        return None

    def get_ui_config(self) -> Optional[Dict[str, Any]]:
        return {
            "icon": "DownloadIcon",
            "color": "#B32FD8",
            "actions": [
                {
                    "label": "Import rossoctl tools",
                    "endpoint": f"/plugins/{SLUG}/import",
                    "method": "POST",
                    "params_schema": {
                        "type": "object",
                        "properties": {
                            "namespace": {
                                "type": "string",
                                "description": "rossoctl namespace to import from (e.g. team1)",
                            },
                            "tools": {
                                "type": "array",
                                "description": "rossoctl tools to import, e.g. [{\"name\": \"weather-tool\"}]",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "gateway_prefix": {"type": "string"},
                                    },
                                    "required": ["name"],
                                },
                            },
                            "skill": {
                                "type": "object",
                                "description": "Optional skill to create from the imported tools",
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "instructions": {"type": "string"},
                                    "tags": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["name", "description"],
                            },
                            "tags": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Extra tags for the imported tools",
                            },
                        },
                        "required": ["namespace", "tools"],
                    },
                }
            ],
        }
